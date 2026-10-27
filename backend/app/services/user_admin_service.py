"""
管理员用户管理服务。

功能：
1. 修改指定用户的昵称、邮箱、角色和启用状态；
2. 重置指定用户的登录密码；
3. 软删除用户（同时撤销其全部登录会话）。

安全约束（不可绕过）：
1. 管理员不能通过本服务操作自己，避免误把自己降级或停用；
2. 不能停用、降级或删除系统最后一个可用管理员；
3. 所有删除均为软删除，禁止物理删除用户数据。
"""

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit_log_utils import load_dictionary_names
from app.common.permissions import ROLE_ADMIN, ROLE_USER, is_admin
from app.common.responses import BusinessError
from app.common.security import hash_password
from app.common.time_utils import beijing_now, format_beijing
from app.models.entities import AuthSession, User
from app.schemas import (
    AdminPasswordResetRequest,
    AdminUserCreateRequest,
    AdminUserUpdateRequest,
)


class UserAdminService:
    """封装管理员对用户的管理操作。"""

    @staticmethod
    def serialize_admin_user(
        user: User, role_names: dict[str, str] | None = None
    ) -> dict:
        """
        序列化管理员视图的用户信息，不返回密码等敏感字段。

        Args:
            user: 用户模型。
            role_names: 角色编码到中文名称的映射。
        Returns:
            前端可直接展示的用户结构。
        """
        return {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "nickname": user.nickname,
            "role_code": user.role_code,
            "role_name": (role_names or {}).get(user.role_code, user.role_code),
            "is_active": user.is_active,
            "last_login_at": format_beijing(user.last_login_at),
            "created_at": format_beijing(user.created_at),
        }

    async def list_users(
        self,
        session: AsyncSession,
        page: int = 1,
        page_size: int = 20,
        keyword: str = "",
        role_code: str = "",
        is_active: bool | None = None,
    ) -> dict:
        """
        分页查询未删除用户，供管理员后台查看。

        Args:
            session: 数据库会话。
            page: 页码，从 1 开始。
            page_size: 每页条数。
            keyword: 关键字，匹配用户名、昵称或邮箱。
            role_code: 角色编码筛选，空字符串表示不筛选。
            is_active: 启用状态筛选，None 表示不筛选。
        Returns:
            含 items、page、page_size、total、pages 的分页结构。
        """
        conditions = [User.is_deleted.is_(False)]
        if keyword:
            like = f"%{keyword}%"
            conditions.append(
                or_(
                    User.username.like(like),
                    User.nickname.like(like),
                    User.email.like(like),
                )
            )
        if role_code:
            conditions.append(User.role_code == role_code)
        if is_active is not None:
            conditions.append(User.is_active.is_(is_active))
        total = int(
            await session.scalar(select(func.count(User.id)).where(*conditions)) or 0
        )
        rows = (
            await session.scalars(
                select(User)
                .where(*conditions)
                .order_by(User.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        role_names = await load_dictionary_names(session, "user_role")
        return {
            "items": [self.serialize_admin_user(user, role_names) for user in rows],
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": max(1, (total + page_size - 1) // page_size),
        }

    async def create_user(
        self, session: AsyncSession, payload: AdminUserCreateRequest
    ) -> dict:
        """
        由管理员创建普通用户，不要求邮箱验证码或极验。

        Args:
            session: 数据库会话。
            payload: 新建用户参数。
        Returns:
            新建成功的用户信息。
        Raises:
            BusinessError: 用户名或邮箱已存在。
        """
        email = str(payload.email).lower() if payload.email else None
        duplicate_conditions = [User.username == payload.username]
        if email:
            duplicate_conditions.append(User.email == email)
        existing = await session.scalar(
            select(User).where(or_(*duplicate_conditions))
        )
        if existing is not None:
            if existing.username == payload.username:
                raise BusinessError("用户名已存在", 40010)
            raise BusinessError("邮箱已注册", 40010)

        user = User(
            username=payload.username,
            email=email,
            nickname=(payload.nickname or payload.username).strip(),
            password_hash=hash_password(payload.password),
            role_code=ROLE_USER,
            is_active=True,
        )
        session.add(user)
        try:
            await session.flush()
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise BusinessError("用户名或邮箱已存在", 40010) from exc
        return await self._serialize(session, user)

    async def _load_target_user(
        self, session: AsyncSession, operator: User, user_id: int
    ) -> User:
        """
        读取待操作用户并校验操作合法性。

        Args:
            session: 数据库会话。
            operator: 当前登录的管理员。
            user_id: 目标用户 id。
        Returns:
            目标用户模型。
        Raises:
            BusinessError: 目标用户不存在、或尝试操作自己。
        """
        if not is_admin(operator):
            raise BusinessError("只有管理员可以管理用户", 40370)
        if operator.id == user_id:
            raise BusinessError("不能在用户管理中操作自己的账号，请前往个人设置修改", 40371)
        target = await session.scalar(
            select(User).where(User.id == user_id, User.is_deleted.is_(False))
        )
        if target is None:
            raise BusinessError("用户不存在或已被删除", 40372)
        return target

    async def _active_admin_count(self, session: AsyncSession) -> int:
        """
        统计当前仍然可用的管理员数量。

        Args:
            session: 数据库会话。
        Returns:
            未删除且已启用的管理员数量。
        """
        return int(
            await session.scalar(
                select(func.count(User.id)).where(
                    User.role_code == ROLE_ADMIN,
                    User.is_deleted.is_(False),
                    User.is_active.is_(True),
                )
            )
            or 0
        )

    async def _ensure_not_last_admin(
        self, session: AsyncSession, target: User, action: str
    ) -> None:
        """
        阻止让系统最后一个可用管理员失效的操作。

        Args:
            session: 数据库会话。
            target: 目标用户。
            action: 操作名称，用于拼装错误提示。
        Returns:
            None
        Raises:
            BusinessError: 目标用户是最后一个可用管理员。
        """
        if target.role_code != ROLE_ADMIN or not target.is_active:
            return
        if await self._active_admin_count(session) <= 1:
            raise BusinessError(f"系统至少需要保留一个可用管理员，无法{action}该账号", 40373)

    async def _revoke_sessions(self, session: AsyncSession, user_id: int) -> None:
        """
        撤销指定用户的全部有效登录会话。

        Args:
            session: 数据库会话。
            user_id: 目标用户 id。
        Returns:
            None
        """
        await session.execute(
            update(AuthSession)
            .where(AuthSession.owner_id == user_id, AuthSession.is_deleted.is_(False))
            .values(is_deleted=True, updated_at=beijing_now())
        )

    async def _serialize(self, session: AsyncSession, user: User) -> dict:
        """
        序列化单个用户，角色中文名称取自数据字典。

        Args:
            session: 数据库会话。
            user: 用户模型。
        Returns:
            前端可直接展示的用户结构。
        """
        role_names = await load_dictionary_names(session, "user_role")
        return self.serialize_admin_user(user, role_names)

    async def update_user(
        self,
        session: AsyncSession,
        operator: User,
        user_id: int,
        payload: AdminUserUpdateRequest,
    ) -> dict:
        """
        修改用户的昵称、邮箱、角色和启用状态，只更新提交的字段。

        Args:
            session: 数据库会话。
            operator: 当前登录的管理员。
            user_id: 目标用户 id。
            payload: 待更新字段。
        Returns:
            更新后的用户信息。
        Raises:
            BusinessError: 用户不存在、邮箱重复或违反管理员保护规则。
        """
        target = await self._load_target_user(session, operator, user_id)

        # 只有显式提交了 email 字段才处理，这样才能区分「不修改」和「清空邮箱」。
        if "email" in payload.model_fields_set:
            email = str(payload.email).lower() if payload.email else None
            if email != target.email:
                if email is not None:
                    existing = await session.scalar(
                        select(User).where(
                            User.email == email,
                            User.id != target.id,
                            User.is_deleted.is_(False),
                        )
                    )
                    if existing is not None:
                        raise BusinessError("该邮箱已被其他账号使用", 40374)
                # 邮箱变更后需要重新验证，即使是从有到无也要清掉验证时间。
                target.email = email
                target.email_verified_at = None

        if payload.nickname is not None:
            target.nickname = payload.nickname.strip()

        # 停用或降级前先确认不会让系统失去唯一可用管理员。
        if payload.role_code is not None and payload.role_code != target.role_code:
            if payload.role_code != ROLE_ADMIN:
                await self._ensure_not_last_admin(session, target, "降级")
            target.role_code = payload.role_code

        if payload.is_active is not None and payload.is_active != target.is_active:
            if not payload.is_active:
                await self._ensure_not_last_admin(session, target, "停用")
                # 停用后必须踢掉已有会话，否则旧 token 仍然可用。
                await self._revoke_sessions(session, target.id)
            target.is_active = payload.is_active

        target.updated_at = beijing_now()
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise BusinessError("邮箱已被其他账号使用", 40374) from exc
        return await self._serialize(session, target)

    async def reset_password(
        self,
        session: AsyncSession,
        operator: User,
        user_id: int,
        payload: AdminPasswordResetRequest,
    ) -> None:
        """
        重置指定用户密码并强制其重新登录。

        Args:
            session: 数据库会话。
            operator: 当前登录的管理员。
            user_id: 目标用户 id。
            payload: 新密码。
        Returns:
            None
        """
        target = await self._load_target_user(session, operator, user_id)
        target.password_hash = hash_password(payload.new_password)
        target.updated_at = beijing_now()
        await self._revoke_sessions(session, target.id)
        await session.commit()

    async def delete_user(
        self, session: AsyncSession, operator: User, user_id: int
    ) -> None:
        """
        软删除指定用户并撤销其登录会话，历史数据保留。

        Args:
            session: 数据库会话。
            operator: 当前登录的管理员。
            user_id: 目标用户 id。
        Returns:
            None
        """
        target = await self._load_target_user(session, operator, user_id)
        # 删除同样会让系统失去该管理员，因此与停用走同一套保护。
        await self._ensure_not_last_admin(session, target, "删除")
        target.is_deleted = True
        target.updated_at = beijing_now()
        await self._revoke_sessions(session, target.id)
        await session.commit()
