"""系统级定时任务配置与后台调度服务。

功能：
1. 幂等初始化内置定时任务。
2. 按配置间隔启动、暂停和执行系统任务。
3. 自动同意已开启机器人收到的历史好友申请。
"""

import asyncio
import json
import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.common.responses import BusinessError
from app.common.time_utils import beijing_now, format_beijing
from app.models.entities import AnimatedEmoji, BotAccount, ScheduledTask
from app.schemas import ScheduledTaskUpdateRequest
from app.services.animated_emoji_service import (
    ADVERTISING_CATEGORY,
    AnimatedEmojiService,
    UNCATEGORIZED,
    normalise_ai_category,
)
from app.services.setting_service import SettingService


logger = logging.getLogger("uvicorn.error")
SCHEDULER_SCAN_SECONDS = 1
AUTO_APPROVE_TASK_KEY = "auto_approve_friend_requests"
ANIMATED_EMOJI_CLASSIFY_TASK_KEY = "classify_animated_emojis"
HISTORICAL_FRIEND_REQUEST_UNSUPPORTED = (
    "当前 NapCat 版本不支持查询普通好友历史申请；"
    "set_friend_add_request 只能处理实时上报事件中的 flag。"
    "新好友申请仍会通过实时事件自动同意。"
)
DEFAULT_TASKS: tuple[dict[str, Any], ...] = (
    {
        "task_key": AUTO_APPROVE_TASK_KEY,
        "task_name": "自动同意历史好友申请",
        "task_description": "检查历史好友申请；当前 NapCat 版本不支持普通好友历史查询，实时申请仍会自动同意。",
        "interval_seconds": 300,
    },
    {
        "task_key": ANIMATED_EMOJI_CLASSIFY_TASK_KEY,
        "task_name": "动画表情分类",
        "task_description": "仅处理未分类动画表情，调用所属机器人 AI 视觉模型生成明确分类。",
        "interval_seconds": 300,
    },
)


class ScheduledTaskService:
    """维护通用定时任务并运行后台扫描器。"""

    def __init__(
        self,
        database: Any,
        onebot_service: Any,
        animated_emoji_service: AnimatedEmojiService,
    ) -> None:
        self.database = database
        self.onebot_service = onebot_service
        self.animated_emoji_service = animated_emoji_service
        self._task: asyncio.Task | None = None
        self._run_lock = asyncio.Lock()

    async def initialize_defaults(self, session: AsyncSession) -> None:
        """幂等补齐系统内置任务，不覆盖已有任务的用户配置。"""
        for values in DEFAULT_TASKS:
            row = await session.scalar(
                select(ScheduledTask).where(ScheduledTask.task_key == values["task_key"])
            )
            if row is None:
                row = ScheduledTask(
                    **values,
                    owner_id=None,
                    is_enabled=True,
                    next_run_at=beijing_now() + timedelta(seconds=values["interval_seconds"]),
                    last_status="pending",
                )
                session.add(row)
                continue
            if row.is_deleted:
                row.is_deleted = False
                row.is_enabled = True
                row.updated_at = beijing_now()
        await session.commit()

    async def list_tasks(
        self, session: AsyncSession, page: int = 1, page_size: int = 20
    ) -> dict[str, Any]:
        """分页返回所有未删除定时任务，按主键倒序展示。"""
        total = int(
            await session.scalar(
                select(func.count(ScheduledTask.id)).where(ScheduledTask.is_deleted.is_(False))
            )
            or 0
        )
        rows = (
            await session.scalars(
                select(ScheduledTask)
                .where(ScheduledTask.is_deleted.is_(False))
                .order_by(ScheduledTask.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        return {
            "items": [self.serialize(row) for row in rows],
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": max(1, (total + page_size - 1) // page_size),
        }

    async def update_task(
        self,
        session: AsyncSession,
        task_id: int,
        payload: ScheduledTaskUpdateRequest,
    ) -> dict[str, Any]:
        """修改任务执行间隔或启停状态，并重新计算下次执行时间。"""
        row = await session.scalar(
            select(ScheduledTask).where(
                ScheduledTask.id == task_id,
                ScheduledTask.is_deleted.is_(False),
            )
        )
        if row is None:
            raise BusinessError("定时任务不存在", 40470)
        row.interval_seconds = payload.interval_seconds
        row.is_enabled = payload.is_enabled
        row.next_run_at = (
            beijing_now() + timedelta(seconds=payload.interval_seconds)
            if payload.is_enabled
            else None
        )
        row.updated_at = beijing_now()
        await session.commit()
        await session.refresh(row)
        return self.serialize(row)

    async def run_task(self, task_id: int) -> dict[str, Any]:
        """立即执行指定任务，不受任务当前暂停状态或下次执行时间限制。"""
        result = await self._process_task(task_id, force=True)
        if result is None:
            raise BusinessError("定时任务不存在", 40470)
        return result

    async def start(self) -> None:
        """启动定时任务后台调度器。"""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run_scheduler())

    async def close(self) -> None:
        """停止定时任务后台调度器。"""
        if self._task is None:
            return
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    async def _run_scheduler(self) -> None:
        """周期扫描到期任务，单次异常不会终止后续调度。"""
        while True:
            try:
                await self._run_due_tasks()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("定时任务调度执行异常")
            await asyncio.sleep(SCHEDULER_SCAN_SECONDS)

    async def _run_due_tasks(self) -> None:
        """读取到期任务并逐条执行。"""
        now = beijing_now()
        async with self.database.session_factory() as session:
            task_ids = (
                await session.scalars(
                    select(ScheduledTask.id).where(
                        ScheduledTask.is_enabled.is_(True),
                        ScheduledTask.is_deleted.is_(False),
                        (ScheduledTask.next_run_at.is_(None) | (ScheduledTask.next_run_at <= now)),
                    )
                )
            ).all()
        for task_id in task_ids:
            await self._process_task(int(task_id))

    async def _process_task(
        self, task_id: int, force: bool = False
    ) -> dict[str, Any] | None:
        """执行一个到期或手动指定的任务并记录状态、结果和错误信息。"""
        async with self._run_lock:
            async with self.database.session_factory() as session:
                conditions = [
                    ScheduledTask.id == task_id,
                    ScheduledTask.is_deleted.is_(False),
                ]
                if not force:
                    conditions.append(ScheduledTask.is_enabled.is_(True))
                row = await session.scalar(select(ScheduledTask).where(*conditions))
                if row is None:
                    return None
                now = beijing_now()
                if not force and row.next_run_at is not None and row.next_run_at > now:
                    return None
                row.next_run_at = now + timedelta(seconds=row.interval_seconds) if row.is_enabled else None
                row.last_run_at = now
                row.last_status = "running"
                row.last_error = ""
                row.updated_at = now
                await session.commit()
                task_key = row.task_key
                interval = row.interval_seconds
                enabled = row.is_enabled
        try:
            result = await self._dispatch(task_key)
            if result.get("unsupported"):
                status = "unsupported"
            else:
                status = "success" if not result.get("failed") else "partial"
            error = str(result.get("error") or "")
        except Exception as exc:
            logger.exception("定时任务执行失败，task_key=%s", task_key)
            result = {}
            status = "failed"
            error = str(exc)
        async with self.database.session_factory() as session:
            row = await session.scalar(select(ScheduledTask).where(ScheduledTask.id == task_id))
            if row is None:
                return
            row.last_status = status
            row.last_error = error[:5000]
            row.last_result = json.dumps(result, ensure_ascii=False, default=str)[:10000]
            row.next_run_at = beijing_now() + timedelta(seconds=interval) if enabled else None
            row.updated_at = beijing_now()
            await session.commit()
        logger.info("[定时任务] task_key=%s，状态=%s，结果=%s", task_key, status, result)
        return self.serialize(row)

    async def _dispatch(self, task_key: str) -> dict[str, Any]:
        """根据任务标识分派具体业务处理器。"""
        if task_key == AUTO_APPROVE_TASK_KEY:
            return await self._auto_approve_friend_requests()
        if task_key == ANIMATED_EMOJI_CLASSIFY_TASK_KEY:
            return await self._classify_animated_emojis()
        raise BusinessError(f"不支持的定时任务类型：{task_key}", 40071)

    async def _classify_animated_emojis(self) -> dict[str, Any]:
        """逐条处理未分类动画表情，并按机器人 AI 配置生成分类。"""
        async with self.database.session_factory() as session:
            rows = (
                await session.scalars(
                    select(AnimatedEmoji).where(
                        AnimatedEmoji.category == UNCATEGORIZED,
                        AnimatedEmoji.is_deleted.is_(False),
                        or_(
                            and_(
                                AnimatedEmoji.source_path.is_not(None),
                                func.trim(AnimatedEmoji.source_path) != "",
                            ),
                            and_(
                                AnimatedEmoji.source_url.is_not(None),
                                func.trim(AnimatedEmoji.source_url) != "",
                            ),
                            and_(
                                AnimatedEmoji.image_base64.is_not(None),
                                func.trim(AnimatedEmoji.image_base64) != "",
                            ),
                        ),
                    ).order_by(AnimatedEmoji.received_at.asc(), AnimatedEmoji.id.asc())
                )
            ).all()
        result: dict[str, Any] = {
            "pending": len(rows),
            "processed": 0,
            "classified": 0,
            "failed": 0,
            "deleted": 0,
            "error": "",
        }
        for item in rows:
            result["processed"] += 1
            try:
                category = await self._classify_animated_emoji(item)
                if category:
                    result["classified"] += 1
                else:
                    result["failed"] += 1
            except BusinessError as exc:
                if exc.code in {40079, 40081}:
                    if exc.code == 40079:
                        deleted = await self._soft_delete_unreadable(item.id, str(exc))
                    else:
                        deleted = await self._soft_delete_advertisement(item.id, str(exc))
                    if deleted:
                        result["deleted"] += 1
                        logger.warning(
                            "[动画表情分类] 记录已软删除，记录=%s，机器人=%s，错误=%s",
                            item.id,
                            item.bot_qq,
                            exc,
                        )
                        continue
                result["failed"] += 1
                await self._save_classification_error(item.id, str(exc))
                logger.error(
                    "[动画表情分类] 处理失败，记录=%s，机器人=%s，错误=%s",
                    item.id,
                    item.bot_qq,
                    exc,
                )
            except Exception as exc:
                result["failed"] += 1
                await self._save_classification_error(item.id, str(exc))
                logger.error(
                    "[动画表情分类] 处理失败，记录=%s，机器人=%s，错误=%s",
                    item.id,
                    item.bot_qq,
                    exc,
                )
        if result["failed"]:
            result["error"] = f"{result['failed']} 条动画表情分类失败，将在下次任务中重试"
        return result

    async def _save_classification_error(self, item_id: int, error: str) -> None:
        """保存本次分类失败原因，记录仍保留为未分类以便重试。"""
        async with self.database.session_factory() as session:
            row = await session.scalar(
                select(AnimatedEmoji).where(
                    AnimatedEmoji.id == item_id,
                    AnimatedEmoji.is_deleted.is_(False),
                )
            )
            if row is None:
                return
            row.classification_error = error[:5000]
            row.updated_at = beijing_now()
            await session.commit()

    async def _soft_delete_unreadable(self, item_id: int, error: str) -> bool:
        """图片来源全部失效时软删除记录，保留历史数据和失败原因。"""
        return await self._soft_delete_record(item_id, error, "图片无法读取")

    async def _soft_delete_advertisement(self, item_id: int, error: str) -> bool:
        """AI 判定为广告时软删除记录，保留历史数据和分类原因。"""
        return await self._soft_delete_record(item_id, error, "AI 判定为广告")

    async def _soft_delete_record(self, item_id: int, error: str, reason: str) -> bool:
        """按指定原因软删除动画表情记录，保留历史数据和处理原因。"""
        async with self.database.session_factory() as session:
            row = await session.scalar(
                select(AnimatedEmoji).where(
                    AnimatedEmoji.id == item_id,
                    AnimatedEmoji.is_deleted.is_(False),
                )
            )
            if row is None:
                return False
            row.is_deleted = True
            row.classification_error = f"{reason}，已自动软删除：{error}"[:5000]
            row.updated_at = beijing_now()
            await session.commit()
            return True

    async def _classify_animated_emoji(self, item: AnimatedEmoji) -> str:
        """加载单张动画表情并要求所属机器人 AI 返回一个分类。"""
        async with self.database.session_factory() as session:
            row = await session.scalar(
                select(AnimatedEmoji)
                .options(undefer(AnimatedEmoji.image_base64))
                .where(
                    AnimatedEmoji.id == item.id,
                    AnimatedEmoji.category == UNCATEGORIZED,
                    AnimatedEmoji.is_deleted.is_(False),
                )
            )
            if row is None:
                return ""
            row.classification_attempts += 1
            row.classification_error = ""
            image_base64 = str(row.image_base64 or "")
            media_type = str(row.media_type or "image/jpeg")
            source_path = str(row.source_path or "")
            source_url = str(row.source_url or "")
            await session.commit()

        async with self.database.session_factory() as session:
            config = await self.onebot_service.bot_ai_service.active_runtime_config(
                session, item.bot_qq
            )
            prompt_settings = await SettingService(session).get_group("animated_emoji")
        if config is None:
            raise BusinessError(f"机器人 {item.bot_qq} 未开启 AI 配置", 40078)
        _, http_url, http_token = await self.onebot_service._connection_settings(item.bot_qq)
        if image_base64:
            logger.info(
                "[动画表情分类] 使用已缓存 Base64，记录=%s，机器人=%s",
                item.id,
                item.bot_qq,
            )
            images = [{"media_type": media_type, "data": image_base64}]
        else:
            image_event = {
                "message": [
                    {
                        "type": "image",
                        "data": {
                            "file": item.file_name,
                            "url": source_url,
                            "path": source_path,
                        },
                    }
                ]
            }
            self.onebot_service.ai_image_service.http_client = self.onebot_service.http_client
            images = await self.onebot_service.ai_image_service.collect(
                image_event, http_url, http_token
            )
        if not images:
            raise BusinessError("动画表情图片无法读取，公网地址可能已过期", 40079)
        generation_config = (
            self.onebot_service.bot_ai_service.image_model_config(config)
        )
        # 图片分类使用本任务的专用指令，不继承聊天机器人的角色提示词。
        generation_config = dict(generation_config)
        generation_config["system_prompt"] = ""
        generation_config["_system_prompt_override"] = ""
        reply = await self.onebot_service.bot_ai_service.generate(
            generation_config,
            str(prompt_settings.get("classification_prompt") or "").strip(),
            images=images,
        )
        category = self._normalise_category(reply)
        if not category:
            raise BusinessError("AI 无法明确识别动画表情，分类失败，将稍后重试", 40080)
        if category == ADVERTISING_CATEGORY:
            raise BusinessError("AI 判定该动画表情为广告", 40081)
        async with self.database.session_factory() as session:
            row = await session.scalar(
                select(AnimatedEmoji).where(AnimatedEmoji.id == item.id)
            )
            if row is None or row.is_deleted:
                return ""
            row.category = category
            row.classification_error = ""
            row.classified_at = beijing_now()
            row.updated_at = beijing_now()
            await session.commit()
        return category

    @staticmethod
    def _normalise_category(reply: Any) -> str:
        """只接受明确成功的 JSON 分类，拒绝猜测、闲聊和无法判断结果。"""
        return normalise_ai_category(reply)

    async def _auto_approve_friend_requests(self) -> dict[str, Any]:
        """获取并同意所有开启自动同意开关机器人的历史好友申请。"""
        async with self.database.session_factory() as session:
            bots = (
                await session.scalars(
                    select(BotAccount).where(
                        BotAccount.is_enabled.is_(True),
                        BotAccount.auto_approve_friend.is_(True),
                        BotAccount.is_deleted.is_(False),
                    )
                )
            ).all()
        result: dict[str, Any] = {
            "bots": len(bots),
            "requests": 0,
            "approved": 0,
            "failed": 0,
            "unsupported": True,
            "error": HISTORICAL_FRIEND_REQUEST_UNSUPPORTED,
        }
        logger.warning("[定时任务] %s", HISTORICAL_FRIEND_REQUEST_UNSUPPORTED)
        return result

    @staticmethod
    def serialize(row: ScheduledTask) -> dict[str, Any]:
        """将定时任务实体转换为统一接口响应。"""
        return {
            "id": row.id,
            "task_key": row.task_key,
            "task_name": row.task_name,
            "task_description": row.task_description,
            "interval_seconds": row.interval_seconds,
            "is_enabled": row.is_enabled,
            "next_run_at": format_beijing(row.next_run_at),
            "last_run_at": format_beijing(row.last_run_at),
            "last_status": row.last_status,
            "last_error": row.last_error,
            "last_result": row.last_result,
            "created_at": format_beijing(row.created_at),
            "updated_at": format_beijing(row.updated_at),
        }
