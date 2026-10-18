"""机器人好友列表同步与查询服务。

功能：
1. 通过 NapCat 的 get_friend_list 动作把指定机器人的全部好友同步入库；
2. 附加读取 get_friends_with_category 补齐好友分组名称；
3. 通过 get_friend_msg_history 翻页同步与好友的私聊历史记录并去重入库；
4. 按当前用户数据范围分页查询好友，管理员可查看全部。
"""

import logging
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.database import Database
from app.common.permissions import owner_scope
from app.common.responses import BusinessError
from app.common.time_utils import beijing_now, format_beijing
from app.models.entities import BotFriend, User
from app.services.bot_service import BotService
from app.services.chat_message_service import ChatMessageService
from app.services.onebot_service import OneBotService


logger = logging.getLogger("uvicorn.error")
# 单次历史同步每页拉取条数和最大翻页次数，避免异常数据导致长时间占用连接。
HISTORY_PAGE_SIZE = 50
HISTORY_MAX_PAGES = 40
# 连续多页全部为已存在记录时提前结束，说明已经回到本地已同步区间。
HISTORY_REPEATED_PAGE_LIMIT = 3
# NapCat 返回的性别取值统一映射到 friend_sex 数据字典的编码。
SEX_CODES = {
    "male": "male",
    "man": "male",
    "m": "male",
    "1": "male",
    "female": "female",
    "woman": "female",
    "f": "female",
    "2": "female",
}


def _text(value: Any, max_length: int) -> str:
    """转换为字符串并按数据库字段长度截断，避免脏数据写入失败。"""
    if value is None:
        return ""
    return str(value).strip()[:max_length]


def _number(value: Any) -> int:
    """把 NapCat 的数字字段安全转换为整数，无法解析时返回 0。"""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _sex_code(value: Any) -> str:
    """把 NapCat 的性别值归一化为数据字典编码。"""
    return SEX_CODES.get(str(value or "").strip().lower(), "unknown")


def _birthday(item: dict[str, Any]) -> str:
    """把 NapCat 拆分的生日年月日拼成 YYYY-MM-DD，缺年份时返回空串。"""
    year = _number(item.get("birthday_year"))
    if year <= 0:
        return ""
    month = _number(item.get("birthday_month"))
    day = _number(item.get("birthday_day"))
    return f"{year:04d}-{month:02d}-{day:02d}"


class FriendService:
    """维护机器人好友快照并提供分页查询。"""

    def __init__(
        self,
        bot_service: BotService,
        onebot_service: OneBotService,
        database: Database,
    ) -> None:
        self.bot_service = bot_service
        self.onebot_service = onebot_service
        self.chat_message_service = ChatMessageService(database)

    async def list_friends(
        self,
        session: AsyncSession,
        user: User,
        page: int,
        page_size: int,
        bot_qq: str = "",
        keyword: str = "",
        category_name: str = "",
        sex: str = "",
    ) -> dict[str, Any]:
        """分页查询好友，按机器人、分组和好友 QQ 排序。

        Args:
            session: 数据库会话。
            user: 当前登录用户，用于限定数据范围。
            page: 页码，从 1 开始。
            page_size: 每页条数。
            bot_qq: 机器人 QQ 过滤条件。
            keyword: 好友 QQ、昵称或备注的模糊查询条件。
            category_name: 好友分组名称的模糊查询条件。
            sex: 性别编码过滤条件。
        Returns:
            含分页信息的好友列表。
        """
        conditions = [BotFriend.is_deleted.is_(False)]
        if bot_qq:
            conditions.append(BotFriend.bot_qq == bot_qq)
        if keyword:
            conditions.append(
                or_(
                    BotFriend.friend_qq.contains(keyword, autoescape=True),
                    BotFriend.nickname.contains(keyword, autoescape=True),
                    BotFriend.remark.contains(keyword, autoescape=True),
                )
            )
        if category_name:
            conditions.append(
                BotFriend.category_name.contains(category_name, autoescape=True)
            )
        if sex:
            conditions.append(BotFriend.sex == sex)
        total = int(
            await session.scalar(
                self._scope(select(func.count(BotFriend.id)).where(*conditions), user)
            )
            or 0
        )
        rows = (
            await session.scalars(
                self._scope(select(BotFriend).where(*conditions), user)
                .order_by(
                    BotFriend.bot_qq,
                    BotFriend.category_name,
                    BotFriend.friend_qq,
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        owners = await self._owner_names(session, {item.owner_id for item in rows})
        return {
            "items": [self.serialize(item, owners) for item in rows],
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": max(1, (total + page_size - 1) // page_size),
        }

    async def sync_friends(
        self, session: AsyncSession, user: User, bot_qq: str
    ) -> dict[str, Any]:
        """把指定机器人的全部好友同步入库。

        新好友直接新增，已存在的好友更新资料，本次未返回的好友标记为软删除，
        整个过程幂等且不会物理删除历史数据。

        Args:
            session: 数据库会话。
            user: 当前登录用户，用于校验机器人归属。
            bot_qq: 需要同步的机器人 QQ。
        Returns:
            同步统计结果。
        Raises:
            BusinessError: 机器人不存在、已停用或 NapCat 未返回有效好友列表。
        """
        bot = await self.bot_service.get_bot(session, user, bot_qq)
        if not bot.is_enabled:
            raise BusinessError("机器人账号已停用", 40034)
        friends = await self._fetch_friends(bot.bot_qq)
        categories = await self._fetch_categories(bot.bot_qq)
        existing = {
            item.friend_qq: item
            for item in (
                await session.scalars(
                    select(BotFriend).where(BotFriend.bot_qq == bot.bot_qq)
                )
            ).all()
        }
        now = beijing_now()
        created_count = 0
        updated_count = 0
        synced_ids: set[str] = set()
        for item in friends:
            if not isinstance(item, dict):
                continue
            friend_qq = _text(item.get("user_id") or item.get("uin"), 30)
            if not friend_qq:
                continue
            synced_ids.add(friend_qq)
            values = self._friend_values(item, categories)
            record = existing.get(friend_qq)
            if record is None:
                session.add(
                    BotFriend(
                        owner_id=bot.owner_id,
                        bot_qq=bot.bot_qq,
                        friend_qq=friend_qq,
                        synced_at=now,
                        **values,
                    )
                )
                created_count += 1
                continue
            record.owner_id = bot.owner_id
            for field, value in values.items():
                setattr(record, field, value)
            record.is_deleted = False
            record.synced_at = now
            record.updated_at = now
            updated_count += 1
        invalid_count = 0
        for friend_qq, record in existing.items():
            if friend_qq in synced_ids or record.is_deleted:
                continue
            # 已经不在好友列表中的记录只做软删除，保留历史数据可追溯。
            record.is_deleted = True
            record.updated_at = now
            invalid_count += 1
        await session.commit()
        logger.info(
            "好友同步完成，机器人=%s，返回=%s，新增=%s，更新=%s，失效=%s",
            bot.bot_qq,
            len(synced_ids),
            created_count,
            updated_count,
            invalid_count,
        )
        return {
            "bot_qq": bot.bot_qq,
            "total_count": len(synced_ids),
            "created_count": created_count,
            "updated_count": updated_count,
            "invalid_count": invalid_count,
        }

    async def sync_friend_messages(
        self, session: AsyncSession, user: User, bot_qq: str, friend_qq: str
    ) -> dict[str, Any]:
        """翻页同步指定好友的私聊历史记录并去重入库。

        NapCat 的 get_friend_msg_history 每次只返回一页，需要用本页最早一条的
        message_seq 继续往前翻。已存在的消息按机器人和消息 ID 跳过，因此重复
        同步不会产生重复数据。

        Args:
            session: 数据库会话。
            user: 当前登录用户，用于校验机器人和好友归属。
            bot_qq: 机器人 QQ。
            friend_qq: 好友 QQ。
        Returns:
            同步统计结果。
        Raises:
            BusinessError: 机器人或好友不存在、机器人已停用、NapCat 未返回有效历史。
        """
        bot = await self.bot_service.get_bot(session, user, bot_qq)
        if not bot.is_enabled:
            raise BusinessError("机器人账号已停用", 40034)
        friend = await session.scalar(
            self._scope(
                select(BotFriend).where(
                    BotFriend.bot_qq == bot.bot_qq,
                    BotFriend.friend_qq == friend_qq,
                    BotFriend.is_deleted.is_(False),
                ),
                user,
            )
        )
        if friend is None:
            raise BusinessError("好友不存在或无权访问，请先同步好友列表", 40470)
        message_seq: Any = 0
        fetched_total = 0
        created_total = 0
        existing_total = 0
        ignored_total = 0
        page_count = 0
        repeated_pages = 0
        for _ in range(HISTORY_MAX_PAGES):
            batch = await self._fetch_message_history(
                bot.bot_qq, friend.friend_qq, message_seq
            )
            if not batch:
                break
            page_count += 1
            stats = await self.chat_message_service.record_history_messages(
                bot.bot_qq, batch, bot.owner_id, bot.id
            )
            fetched_total += stats["fetched"]
            created_total += stats["created"]
            existing_total += stats["existing"]
            ignored_total += stats["ignored"]
            repeated_pages = 0 if stats["created"] else repeated_pages + 1
            if repeated_pages >= HISTORY_REPEATED_PAGE_LIMIT:
                break
            if len(batch) < HISTORY_PAGE_SIZE:
                break
            next_seq = self._earliest_seq(batch)
            if next_seq is None or next_seq == message_seq:
                break
            if (
                isinstance(next_seq, int)
                and isinstance(message_seq, int)
                and message_seq > 0
                and next_seq >= message_seq
            ):
                # 游标没有继续变小说明已经拿不到更早的消息，直接结束翻页。
                break
            message_seq = next_seq
        logger.info(
            "好友聊天记录同步完成，机器人=%s，好友=%s，页数=%s，读取=%s，新增=%s，已存在=%s，忽略=%s",
            bot.bot_qq,
            friend.friend_qq,
            page_count,
            fetched_total,
            created_total,
            existing_total,
            ignored_total,
        )
        return {
            "bot_qq": bot.bot_qq,
            "friend_qq": friend.friend_qq,
            "page_count": page_count,
            "fetched_count": fetched_total,
            "created_count": created_total,
            "existing_count": existing_total,
            "ignored_count": ignored_total,
        }

    async def _fetch_message_history(
        self, bot_qq: str, friend_qq: str, message_seq: Any
    ) -> list[Any]:
        """调用 NapCat get_friend_msg_history 拉取一页私聊历史。"""
        response = await self.onebot_service.call_action(
            "get_friend_msg_history",
            {
                "user_id": friend_qq,
                "message_seq": message_seq,
                "count": HISTORY_PAGE_SIZE,
            },
            self_id=bot_qq,
        )
        data = response.get("data") if isinstance(response, dict) else None
        messages = data.get("messages") if isinstance(data, dict) else data
        if messages is None and isinstance(data, dict):
            messages = data.get("message")
        if messages is None:
            raise BusinessError("NapCat 未返回有效聊天记录", 50230)
        if not isinstance(messages, list):
            raise BusinessError("NapCat 未返回有效聊天记录", 50230)
        return messages

    @staticmethod
    def _earliest_seq(batch: list[Any]) -> Any:
        """取本页最早一条消息的翻页游标，按最小 seq 取值，与返回顺序无关。"""
        candidates: list[int] = []
        fallback: Any = None
        for item in batch:
            if not isinstance(item, dict):
                continue
            for field in ("message_seq", "real_seq", "message_id"):
                value = item.get(field)
                if value is None or value == "":
                    continue
                try:
                    candidates.append(int(value))
                except (TypeError, ValueError):
                    if fallback is None:
                        fallback = value
                break
        return min(candidates) if candidates else fallback

    async def _fetch_friends(self, bot_qq: str) -> list[Any]:
        """调用 NapCat get_friend_list 获取好友原始列表。"""
        response = await self.onebot_service.call_action(
            "get_friend_list", {}, self_id=bot_qq
        )
        data = response.get("data") if isinstance(response, dict) else None
        if not isinstance(data, list):
            raise BusinessError("NapCat 未返回有效好友列表", 50220)
        if not data:
            raise BusinessError("该机器人当前没有可同步的好友", 50221)
        return data

    async def _fetch_categories(self, bot_qq: str) -> dict[str, str]:
        """读取好友分组名称映射，NapCat 不支持该动作时返回空映射。"""
        try:
            response = await self.onebot_service.call_action(
                "get_friends_with_category", {}, self_id=bot_qq
            )
        except BusinessError as error:
            logger.warning(
                "好友分组读取失败，机器人=%s，将不填充分组名称：%s", bot_qq, error.message
            )
            return {}
        data = response.get("data") if isinstance(response, dict) else None
        if not isinstance(data, list):
            logger.warning("好友分组返回格式异常，机器人=%s，将不填充分组名称", bot_qq)
            return {}
        categories: dict[str, str] = {}
        for item in data:
            if not isinstance(item, dict):
                continue
            category_id = _text(
                item.get("categoryId") if item.get("categoryId") is not None
                else item.get("category_id"),
                30,
            )
            category_name = _text(
                item.get("categoryName") or item.get("category_name"), 100
            )
            if category_id and category_name:
                categories[category_id] = category_name
        return categories

    @staticmethod
    def _friend_values(
        item: dict[str, Any], categories: dict[str, str]
    ) -> dict[str, Any]:
        """把 NapCat 好友对象转换为数据库字段值。"""
        category_id = _text(
            item.get("categoryId") if item.get("categoryId") is not None
            else item.get("category_id"),
            30,
        )
        category_name = _text(
            categories.get(category_id)
            or item.get("categoryName")
            or item.get("category_name"),
            100,
        )
        return {
            "nickname": _text(item.get("nickname") or item.get("nick"), 255),
            "remark": _text(item.get("remark"), 255),
            "sex": _sex_code(item.get("sex")),
            "age": _number(item.get("age")),
            "level": _number(item.get("level")),
            "qid": _text(item.get("qid"), 50),
            "long_nick": _text(item.get("longNick") or item.get("long_nick"), 500),
            "category_id": category_id,
            "category_name": category_name,
            "birthday": _birthday(item),
        }

    @staticmethod
    def _scope(query: Select, user: User) -> Select:
        """为好友查询追加普通用户的数据范围。"""
        owner_id = owner_scope(user)
        return query if owner_id is None else query.where(BotFriend.owner_id == owner_id)

    async def set_block(
        self, session: AsyncSession, user: User, friend_id: int, blocked: bool
    ) -> dict[str, Any]:
        """屏蔽或取消屏蔽指定好友，屏蔽后其消息在接收时被直接跳过。

        Args:
            session: 数据库会话。
            user: 当前登录用户，用于限定数据范围。
            friend_id: 好友记录 ID。
            blocked: True 表示屏蔽，False 表示取消屏蔽。
        Returns:
            更新后的好友信息。
        Raises:
            BusinessError: 好友不存在或无权访问。
        """
        friend = await session.scalar(
            self._scope(
                select(BotFriend).where(
                    BotFriend.id == friend_id,
                    BotFriend.is_deleted.is_(False),
                ),
                user,
            )
        )
        if friend is None:
            raise BusinessError("好友不存在或无权访问", 40460)
        friend.is_blocked = bool(blocked)
        friend.updated_at = beijing_now()
        await session.commit()
        owners = await self._owner_names(session, {friend.owner_id})
        return self.serialize(friend, owners)

    @staticmethod
    async def _owner_names(session: AsyncSession, owner_ids: set[int]) -> dict[int, str]:
        """批量读取好友记录所属用户名称。"""
        if not owner_ids:
            return {}
        rows = await session.execute(
            select(User.id, User.username).where(User.id.in_(owner_ids))
        )
        return {row.id: row.username for row in rows}

    @staticmethod
    def serialize(friend: BotFriend, owners: dict[int, str]) -> dict[str, Any]:
        """序列化好友记录供前端展示。"""
        return {
            "id": friend.id,
            "owner_id": friend.owner_id,
            "owner_username": owners.get(friend.owner_id, ""),
            "bot_qq": friend.bot_qq,
            "friend_qq": friend.friend_qq,
            "nickname": friend.nickname or "",
            "remark": friend.remark or "",
            "sex": friend.sex or "unknown",
            "age": friend.age or 0,
            "level": friend.level or 0,
            "qid": friend.qid or "",
            "long_nick": friend.long_nick or "",
            "category_id": friend.category_id or "",
            "category_name": friend.category_name or "",
            "birthday": friend.birthday or "",
            "synced_at": format_beijing(friend.synced_at),
            "created_at": format_beijing(friend.created_at),
            "updated_at": format_beijing(friend.updated_at),
            "is_blocked": bool(friend.is_blocked),
        }
