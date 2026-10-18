"""动画表情图片记录和分页查询服务。

功能：
1. 从 OneBot 入站事件中提取摘要为“动画表情”的图片并幂等保存；
2. 接收时读取图片并缓存 Base64，供后续分类使用；
3. 按当前登录用户的数据范围分页查询图片及 AI 分类结果。
"""

from datetime import datetime
import hashlib
import json
import logging
import random
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.common.permissions import owner_scope
from app.common.responses import BusinessError
from app.common.time_utils import SHANGHAI_TIMEZONE, beijing_now, format_beijing
from app.models.entities import AnimatedEmoji, BotAccount, ChatMessage, User
from app.services.ai_image_service import AiImage, AiImageService, ImageCandidate


UNCATEGORIZED = "未分类"
ADVERTISING_CATEGORY = "广告"
logger = logging.getLogger("uvicorn.error")


def normalise_ai_category(reply: Any) -> str:
    """解析 AI 明确分类结果，拒绝失败状态、闲聊和猜测内容。"""
    text = str(reply or "").strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        result = json.loads(text)
    except (TypeError, ValueError):
        return ""
    if not isinstance(result, dict) or result.get("success") is not True:
        return ""
    value = str(result.get("category") or "").strip()
    failure_markers = ("看不到", "无法", "不确定", "未知", "猜测", "瞎猜")
    if (
        not value
        or value in {UNCATEGORIZED, "无法判断", "不确定", "未知"}
        or any(marker in value for marker in failure_markers)
    ):
        return ""
    return value[:100]


class AnimatedEmojiService:
    """管理收到的动画表情图片记录。"""

    def __init__(
        self,
        database: Any,
    ) -> None:
        self.database = database
        self.image_service: AiImageService | None = None

    def set_image_service(self, image_service: AiImageService) -> None:
        """绑定 OneBot 共用图片服务，避免重复创建 HTTP 连接池。"""
        self.image_service = image_service

    async def record_event(
        self,
        event: dict[str, Any],
        fallback_bot_qq: str = "",
        http_url: str = "",
        http_token: str = "",
    ) -> int:
        """提取入站动画表情并缓存图片。

        Args:
            event: OneBot 入站消息事件。
            fallback_bot_qq: 事件缺少 self_id 时使用的机器人 QQ。
            http_url: 当前机器人配置的 NapCat HTTP 地址。
            http_token: 当前机器人配置的 NapCat HTTP Token。
        Returns:
            本次新增的动画表情记录数。
        """
        if event.get("post_type") != "message":
            return 0
        bot_qq = str(event.get("self_id") or fallback_bot_qq or "").strip()
        if not bot_qq or str(event.get("user_id") or "") == bot_qq:
            return 0
        candidates = AiImageService.animated_candidates(event)
        if not candidates:
            return 0
        captured: dict[str, tuple[str, str]] = {}
        for candidate in candidates:
            source_key = _source_key(
                str(event.get("message_id") or event.get("id") or _event_identity(event)),
                candidate,
            )
            image = await self._capture_candidate(candidate, http_url, http_token)
            if image is not None:
                captured[source_key] = (image["data"], image["media_type"])
        try:
            async with self.database.session_factory() as session:
                bot = await session.scalar(
                    select(BotAccount).where(
                        BotAccount.bot_qq == bot_qq,
                        BotAccount.is_deleted.is_(False),
                    )
                )
                added = 0
                received_at = _event_time(event)
                message_id = str(event.get("message_id") or event.get("id") or "")
                group_id = str(event.get("group_id") or "")
                sender = event.get("sender")
                sender = sender if isinstance(sender, dict) else {}
                user_id = str(event.get("user_id") or sender.get("user_id") or "")
                message_identity = message_id or _event_identity(event)
                sender_name = str(
                    sender.get("card") or sender.get("nickname") or user_id
                )[:100]
                for candidate in candidates:
                    source_key = _source_key(message_identity, candidate)
                    exists = await session.scalar(
                        select(AnimatedEmoji.id).where(
                            AnimatedEmoji.bot_qq == bot_qq,
                            AnimatedEmoji.source_key == source_key,
                            AnimatedEmoji.is_deleted.is_(False),
                        )
                    )
                    if exists is not None:
                        continue
                    session.add(
                        AnimatedEmoji(
                            owner_id=bot.owner_id if bot else None,
                            bot_qq=bot_qq,
                            group_id=group_id,
                            group_name=str(event.get("group_name") or "")[:100],
                            user_id=user_id,
                            sender_nickname=sender_name,
                            message_id=message_id,
                            file_name=candidate.get("name", "")[:255],
                            summary=candidate.get("summary") or "[动画表情]",
                            media_type=(
                                captured.get(source_key, ("", ""))[1]
                                or candidate.get("media_type")
                                or "image/jpeg"
                            ),
                            image_base64=captured.get(source_key, ("", ""))[0],
                            source_url=candidate.get("url", "")[:2000],
                            source_path=candidate.get("path", "")[:1000],
                            source_key=source_key,
                            category=UNCATEGORIZED,
                            received_at=received_at,
                        )
                    )
                    added += 1
                if added:
                    await session.commit()
                return added
        except Exception:
            # 记录失败不应阻断 OneBot 收包和自动回复流程，具体错误由调用方记录。
            raise

    async def _capture_candidate(
        self,
        candidate: ImageCandidate,
        http_url: str = "",
        http_token: str = "",
    ) -> dict[str, str] | None:
        """在入库前读取图片并保存平台无关的 Base64 数据。"""
        if self.image_service is None:
            logger.warning(
                "动画表情 Base64 保存未启用，文件=%s",
                candidate.get("name") or candidate.get("url") or "未提供",
            )
            return None
        try:
            image = await self.image_service.load_candidate(
                candidate,
                http_url,
                http_token,
            )
            if image is None:
                logger.warning(
                    "动画表情 Base64 下载失败，文件=%s，地址=%s",
                    candidate.get("name") or "未提供",
                    candidate.get("url") or "未提供",
                )
            else:
                logger.info(
                    "动画表情图片已转为 Base64，文件=%s，媒体类型=%s，编码长度=%s",
                    candidate.get("name") or "未提供",
                    image.get("media_type") or "image/jpeg",
                    len(image.get("data") or ""),
                )
            return image
        except Exception as exc:
            logger.warning(
                "动画表情 Base64 保存失败，文件=%s，错误=%s",
                candidate.get("name") or "未提供",
                exc,
            )
            return None

    async def get_event_images(
        self, event: dict[str, Any], bot_qq: str
    ) -> list[AiImage]:
        """读取当前事件已保存的动画表情 Base64，避免 AI 回复再次下载公网图片。"""
        candidates = AiImageService.animated_candidates(event)
        if not candidates:
            return []
        message_identity = str(
            event.get("message_id") or event.get("id") or _event_identity(event)
        )
        source_keys = {
            _source_key(message_identity, candidate) for candidate in candidates
        }
        async with self.database.session_factory() as session:
            rows = (
                await session.scalars(
                    select(AnimatedEmoji)
                    .options(undefer(AnimatedEmoji.image_base64))
                    .where(
                        AnimatedEmoji.bot_qq == str(bot_qq),
                        AnimatedEmoji.source_key.in_(source_keys),
                        AnimatedEmoji.is_deleted.is_(False),
                    )
                )
            ).all()
        by_source_key = {str(row.source_key): row for row in rows}
        images: list[AiImage] = []
        for candidate in candidates:
            source_key = _source_key(message_identity, candidate)
            row = by_source_key.get(source_key)
            image_base64 = str(row.image_base64 or "") if row is not None else ""
            if not image_base64:
                continue
            images.append(
                {
                    "media_type": str(
                        row.media_type or candidate.get("media_type") or "image/jpeg"
                    ),
                    "data": image_base64,
                }
            )
        if images:
            logger.info(
                "动画表情 Base64 已保存并复用，机器人=%s，消息=%s，图片数=%s",
                bot_qq,
                event.get("message_id") or event.get("id") or "未提供",
                len(images),
            )
        return images

    async def random_image_for_category(
        self, session: AsyncSession, bot_qq: str, category: str
    ) -> dict[str, str] | None:
        """按机器人和分类随机返回一张有 Base64 数据的动画表情。"""
        normalized = str(category or "").strip()
        if not bot_qq or not normalized:
            return None
        image_ids = list(
            await session.scalars(
                select(AnimatedEmoji.id)
                .where(
                    AnimatedEmoji.bot_qq == str(bot_qq),
                    AnimatedEmoji.is_deleted.is_(False),
                    AnimatedEmoji.category == normalized,
                    AnimatedEmoji.image_base64.is_not(None),
                    AnimatedEmoji.image_base64 != "",
                )
            )
        )
        if not image_ids:
            return None
        row = await session.scalar(
            select(AnimatedEmoji)
            .options(undefer(AnimatedEmoji.image_base64))
            .where(AnimatedEmoji.id == random.choice(image_ids))
        )
        if row is None or not str(row.image_base64 or "").strip():
            return None
        return {
            "id": str(row.id),
            "category": str(row.category or category),
            "media_type": str(row.media_type or "image/jpeg"),
            "data": str(row.image_base64).strip(),
        }

    async def categories_for_bot(
        self, session: AsyncSession, bot_qq: str
    ) -> list[str]:
        """查询机器人已有且包含 Base64 图片的动画表情分类。"""
        if not str(bot_qq or "").strip():
            return []
        rows = await session.scalars(
            select(AnimatedEmoji.category)
            .where(
                AnimatedEmoji.bot_qq == str(bot_qq),
                AnimatedEmoji.is_deleted.is_(False),
                AnimatedEmoji.category != UNCATEGORIZED,
                AnimatedEmoji.image_base64.is_not(None),
                AnimatedEmoji.image_base64 != "",
            )
            .distinct()
        )
        categories: list[str] = []
        seen: set[str] = set()
        for value in rows:
            category = str(value or "").strip()
            key = category.casefold()
            if category and key not in seen:
                seen.add(key)
                categories.append(category)
        return categories

    async def list_records(
        self,
        session: AsyncSession,
        user: User,
        page: int,
        page_size: int,
        bot_qq: str = "",
        category: str = "",
    ) -> dict[str, Any]:
        """按接收时间全局倒序分页查询动画表情。"""
        conditions = [AnimatedEmoji.is_deleted.is_(False)]
        if bot_qq:
            conditions.append(AnimatedEmoji.bot_qq == bot_qq)
        if category:
            conditions.append(AnimatedEmoji.category == category)
        query = self._scope(
            select(AnimatedEmoji)
            .where(*conditions),
            user,
        )
        count_query = self._scope(
            select(func.count(AnimatedEmoji.id)).where(*conditions), user
        )
        total = int(await session.scalar(count_query) or 0)
        rows = (
            await session.scalars(
                query.order_by(
                    AnimatedEmoji.received_at.desc(),
                    AnimatedEmoji.id.desc(),
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        owners = await self._owner_names(
            session, {item.owner_id for item in rows if item.owner_id is not None}
        )
        recovered_urls = await self._recover_source_urls(session, user, rows)
        return {
            "items": [
                self.serialize(item, owners, recovered_urls.get(item.id, ""))
                for item in rows
            ],
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": max(1, (total + page_size - 1) // page_size),
        }

    async def get_preview(
        self, session: AsyncSession, user: User, record_id: int
    ) -> dict[str, str]:
        """按记录 ID 读取单张动画表情 Base64，供列表懒加载预览。"""
        record = await session.scalar(
            self._scope(
                select(AnimatedEmoji)
                .options(undefer(AnimatedEmoji.image_base64))
                .where(
                    AnimatedEmoji.id == record_id,
                    AnimatedEmoji.is_deleted.is_(False),
                ),
                user,
            )
        )
        if record is None:
            raise BusinessError("动画表情记录不存在或无权访问", 40460)
        image_base64 = str(record.image_base64 or "").strip()
        if not image_base64:
            raise BusinessError("该动画表情没有可用的图片数据", 40461)
        media_type = str(record.media_type or "image/jpeg").strip()
        if not media_type.startswith("image/"):
            media_type = "image/jpeg"
        return {"media_type": media_type, "data": image_base64}

    async def soft_delete(
        self, session: AsyncSession, user: User, record_id: int
    ) -> None:
        """软删除一条动画表情记录，不物理删除历史数据。"""
        record = await session.scalar(
            self._scope(
                select(AnimatedEmoji).where(
                    AnimatedEmoji.id == record_id,
                    AnimatedEmoji.is_deleted.is_(False),
                ),
                user,
            )
        )
        if record is None:
            raise BusinessError("动画表情记录不存在或无权访问", 40460)
        record.is_deleted = True
        record.updated_at = beijing_now()
        await session.commit()

    async def soft_delete_many(
        self, session: AsyncSession, user: User, record_ids: list[int]
    ) -> int:
        """按勾选 ID 批量软删除动画表情记录，不物理删除数据。"""
        ids = sorted({int(item) for item in record_ids})
        if not ids:
            raise BusinessError("请至少勾选一条动画表情记录", 40060)
        records = (
            await session.scalars(
                self._scope(
                    select(AnimatedEmoji).where(
                        AnimatedEmoji.id.in_(ids),
                        AnimatedEmoji.is_deleted.is_(False),
                    ),
                    user,
                )
            )
        ).all()
        if len(records) != len(ids):
            raise BusinessError("部分动画表情记录不存在或无权访问", 40460)
        now = beijing_now()
        for record in records:
            record.is_deleted = True
            record.updated_at = now
        await session.commit()
        return len(records)

    @staticmethod
    def _scope(query, user: User):
        """为普通用户追加记录所属用户过滤。"""
        owner_id = owner_scope(user)
        return query.where(AnimatedEmoji.owner_id == owner_id) if owner_id is not None else query

    @staticmethod
    async def _owner_names(
        session: AsyncSession, owner_ids: set[int]
    ) -> dict[int, str]:
        """批量读取记录所属用户名称。"""
        if not owner_ids:
            return {}
        rows = await session.execute(select(User.id, User.username).where(User.id.in_(owner_ids)))
        return {row.id: row.username for row in rows}

    @staticmethod
    async def _recover_source_urls(
        session: AsyncSession,
        user: User,
        items: list[AnimatedEmoji],
    ) -> dict[int, str]:
        """从聊天原始事件恢复历史记录中未保存的图片地址。

        只读取并返回恢复结果，不回写动画表情记录，避免对历史数据做未经确认的修复。
        """
        missing = [item for item in items if not (item.source_url or "").strip()]
        if not missing:
            return {}
        message_ids = {
            str(item.message_id).strip()
            for item in missing
            if str(item.message_id or "").strip()
        }
        bot_qqs = {str(item.bot_qq).strip() for item in missing if item.bot_qq}
        if not message_ids or not bot_qqs:
            return {}
        conditions = [
            ChatMessage.bot_qq.in_(bot_qqs),
            ChatMessage.message_id.in_(message_ids),
            ChatMessage.is_deleted.is_(False),
        ]
        owner_id = owner_scope(user)
        if owner_id is not None:
            conditions.append(ChatMessage.owner_id == owner_id)
        messages = (
            await session.scalars(select(ChatMessage).where(*conditions))
        ).all()
        if not messages:
            return {}
        by_key: dict[tuple[str, str, str], str] = {}
        by_file: dict[tuple[str, str, str], str] = {}
        for message in messages:
            try:
                event = json.loads(message.event_data or "{}")
            except (TypeError, ValueError):
                continue
            if not isinstance(event, dict):
                continue
            for candidate in AiImageService.animated_candidates(event):
                url = str(candidate.get("url") or "").strip()
                if not url:
                    continue
                key = (message.bot_qq, str(message.message_id or ""))
                identity = str(candidate.get("identity") or "")
                if identity:
                    by_key.setdefault((*key, identity), url)
                file_name = str(candidate.get("name") or "").strip().casefold()
                if file_name:
                    by_file.setdefault((*key, file_name), url)
        recovered: dict[int, str] = {}
        for item in missing:
            key = (item.bot_qq, str(item.message_id or ""))
            url = ""
            source_key = str(item.source_key or "")
            for (bot_qq, message_id, identity), candidate_url in by_key.items():
                if (
                    bot_qq == key[0]
                    and message_id == key[1]
                    and source_key.endswith(f":{identity}")
                ):
                    url = candidate_url
                    break
            if not url and item.file_name:
                url = by_file.get((*key, item.file_name.casefold()), "")
            if url:
                recovered[item.id] = url
        return recovered

    @staticmethod
    def serialize(
        item: AnimatedEmoji,
        owners: dict[int, str] | None = None,
        recovered_url: str = "",
    ) -> dict[str, Any]:
        """序列化动画表情元数据，图片内容通过预览接口懒加载。"""
        owners = owners or {}
        source_url = str(item.source_url or recovered_url or "")
        media_type = str(item.media_type or "image/jpeg").strip()
        if not media_type.startswith("image/"):
            media_type = "image/jpeg"
        return {
            "id": item.id,
            "owner_id": item.owner_id,
            "owner_username": owners.get(item.owner_id, "未知用户")
            if item.owner_id is not None
            else "未关联用户",
            "bot_qq": item.bot_qq,
            "group_id": item.group_id,
            "group_name": item.group_name,
            "user_id": item.user_id,
            "sender_nickname": item.sender_nickname,
            "message_id": item.message_id,
            "file_name": item.file_name,
            "summary": item.summary,
            "media_type": media_type,
            "source_url": source_url,
            "category": item.category or UNCATEGORIZED,
            "classification_error": item.classification_error,
            "classification_attempts": item.classification_attempts,
            "classified_at": format_beijing(item.classified_at),
            "received_at": format_beijing(item.received_at),
            "created_at": format_beijing(item.created_at),
            "updated_at": format_beijing(item.updated_at),
        }


def _event_time(event: dict[str, Any]) -> datetime:
    """将 OneBot Unix 时间转换为北京时间数据库时间。"""
    value = event.get("time")
    try:
        if value is not None:
            timestamp = float(value)
            if timestamp > 100_000_000_000:
                timestamp /= 1000
            return datetime.fromtimestamp(timestamp, SHANGHAI_TIMEZONE).replace(
                tzinfo=None
            )
    except (TypeError, ValueError, OverflowError, OSError):
        pass
    return beijing_now()


def _source_key(message_id: str, candidate: ImageCandidate) -> str:
    """按消息和图片来源生成幂等键，避免 raw 与 message 重复记录。"""
    identity = candidate.get("identity") or candidate.get("url") or candidate.get("path")
    return f"{message_id}:{identity}"[:255]


def _event_identity(event: dict[str, Any]) -> str:
    """为缺少 message_id 的事件生成稳定指纹，避免跨消息误去重。"""
    payload = json.dumps(event, ensure_ascii=False, sort_keys=True, default=str)
    return f"event:{hashlib.sha1(payload.encode('utf-8')).hexdigest()}"
