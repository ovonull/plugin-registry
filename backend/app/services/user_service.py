"""用户注册、三种登录、会话与个人资料服务。"""

from datetime import timedelta
import logging
import secrets

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.config import Settings
from app.common.permissions import ROLE_ADMIN, ROLE_USER
from app.common.responses import BusinessError
from app.common.security import (
    create_session_token,
    hash_password,
    hash_token,
    verify_password,
)
from app.common.time_utils import beijing_now, format_beijing
from app.models.entities import AuthSession, DataDictionary, User
from app.schemas import (
    EmailBindRequest,
    LoginRequest,
    PasswordUpdateRequest,
    ProfileUpdateRequest,
    RegisterRequest,
)
from app.services.email_log_service import EmailLogService
from app.services.email_service import EmailService
from app.services.geetest_service import GeetestService
from app.services.login_log_service import LoginLogService
from app.services.user_defaults import USER_DICTIONARY_DEFAULTS


logger = logging.getLogger(__name__)


class UserService:
    """封装用户和登录会话的全部数据库操作。"""

    def __init__(
        self,
        settings: Settings,
        geetest: GeetestService | None = None,
        login_log_service: LoginLogService | None = None,
        email_log_service: EmailLogService | None = None,
    ) -> None:
        """初始化用户服务：极验、登录日志与邮件日志服务未传入时使用默认实例。"""
        self.settings = settings
        self.geetest = geetest or GeetestService(settings)
        self.login_log_service = login_log_service or LoginLogService()
        self.email_log_service = email_log_service or EmailLogService()
        self.initial_admin_password: str | None = None

    async def initialize_defaults(self, session: AsyncSession) -> None:
        """幂等补齐角色、登录方式字典和首次管理员账号。"""
        # 初始化字典项集中维护在 user_defaults 模块，便于随新增功能同步扩展。
        for dict_type, code, name, order in USER_DICTIONARY_DEFAULTS:
            existing = await session.scalar(
                select(DataDictionary).where(
                    DataDictionary.dict_type == dict_type,
                    DataDictionary.item_code == code,
                )
            )
            if existing is None:
                session.add(
                    DataDictionary(
                        dict_type=dict_type,
                        item_code=code,
                        item_name=name,
                        sort_order=order,
                    )
                )
            elif existing.is_deleted:
                existing.is_deleted = False
                existing.item_name = name
                existing.sort_order = order

        existing_admin = await session.scalar(
            select(User).where(User.username == self.settings.admin_username)
        )
        if existing_admin is None:
            password = self.settings.admin_password or secrets.token_urlsafe(12)
            self.initial_admin_password = password
            session.add(
                User(
                    username=self.settings.admin_username,
                    nickname="系统管理员",
                    password_hash=hash_password(password),
                    role_code=ROLE_ADMIN,
                )
            )
            logger.warning(
                "首次管理员已创建，用户名=%s，初始密码=%s，请登录后立即修改",
                self.settings.admin_username,
                password,
            )
        await session.commit()

    async def register(self, session: AsyncSession, payload: RegisterRequest) -> dict:
        """消费注册验证码并创建普通用户，同时建立登录会话。"""
        email = str(payload.email).lower()
        await EmailService(session).verify_code(
            email, "register", payload.email_code, payload.geetest_challenge
        )
        existing = await session.scalar(
            select(User).where(
                or_(User.username == payload.username, User.email == email),
            )
        )
        if existing is not None:
            message = "用户名已存在" if existing.username == payload.username else "邮箱已注册"
            raise BusinessError(message, 40010)
        user = User(
            username=payload.username,
            email=email,
            email_verified_at=beijing_now(),
            nickname=(payload.nickname or payload.username).strip(),
            password_hash=hash_password(payload.password),
            role_code=ROLE_USER,
        )
        session.add(user)
        try:
            await session.flush()
        except IntegrityError as exc:
            await session.rollback()
            raise BusinessError("用户名或邮箱已存在", 40010) from exc
        return await self._create_login_session(session, user)

    async def login(
        self, session: AsyncSession, payload: LoginRequest, client_ip: str = ""
    ) -> dict:
        """
        校验指定登录方式并创建可撤销会话，成功与失败都必须写入登录日志。

        Args:
            session: 数据库会话。
            payload: 登录请求参数（登录方式、账号、密码或验证码）。
            client_ip: 客户端 IP，由路由层优先从 X-Forwarded-For 提取。
        Returns:
            登录令牌、有效期和用户资料。
        Raises:
            BusinessError: 登录失败，失败原因会同时写入登录日志。
        """
        return await self.login_log_service.audit_login(
            session,
            login_type=payload.login_type,
            login_account=self._login_account(payload),
            client_ip=client_ip,
            authenticate=lambda: self._authenticate(session, payload),
        )

    @staticmethod
    def _login_account(payload: LoginRequest) -> str:
        """
        提取本次登录尝试使用的账号，用于登录日志。

        Args:
            payload: 登录请求参数。
        Returns:
            账号密码方式返回用户名，其余方式返回邮箱。
        """
        if payload.login_type == "username_password":
            return (payload.username or "").strip()
        return str(payload.email or "").strip().lower()

    async def _authenticate(self, session: AsyncSession, payload: LoginRequest) -> dict:
        """按指定登录方式校验凭据并创建会话，登录失败抛出 BusinessError。"""
        if payload.login_type == "email_code":
            if not payload.email or not payload.email_code:
                raise BusinessError("请输入邮箱和验证码", 40003)
            email = str(payload.email).lower()
            await EmailService(session).verify_code(
                email,
                "login",
                payload.email_code,
                payload.geetest_challenge,
            )
            user = await session.scalar(
                select(User).where(
                    User.email == email,
                    User.is_deleted.is_(False),
                )
            )
        else:
            await self.geetest.consume(session, payload.geetest_challenge)
            if payload.login_type == "username_password":
                if not payload.username or not payload.password:
                    raise BusinessError("请输入用户名和密码", 40003)
                user = await session.scalar(
                    select(User).where(
                        User.username == payload.username,
                        User.is_deleted.is_(False),
                    )
                )
                identifier = payload.username
            else:
                if not payload.email or not payload.password:
                    raise BusinessError("请输入邮箱和密码", 40003)
                email = str(payload.email).lower()
                user = await session.scalar(
                    select(User).where(
                        User.email == email,
                        User.is_deleted.is_(False),
                    )
                )
                identifier = email
            if user is None or not verify_password(payload.password or "", user.password_hash):
                raise BusinessError("账号或密码错误", 40011)

        if user is None:
            raise BusinessError("邮箱或验证码错误", 40011)
        if not user.is_active:
            raise BusinessError("账号已停用，请联系管理员", 40302)
        user.last_login_at = beijing_now()
        return await self._create_login_session(session, user)

    async def send_email_code(
        self,
        session: AsyncSession,
        email: str,
        purpose: str,
        challenge: str,
        owner: User | None = None,
        request_ip: str | None = None,
    ) -> None:
        """消费极验结果后发送验证码，登录/注册用途不暴露邮箱是否存在。"""
        normalized = email.strip().lower()
        if purpose == "bind":
            if owner is None:
                await self.email_log_service.record_send(
                    session, purpose, normalized, False, None, request_ip, "请先登录"
                )
                raise BusinessError("请先登录", 40100)
            existing = await session.scalar(
                select(User).where(
                    User.email == normalized,
                    User.id != owner.id,
                )
            )
            if existing is not None:
                await self.email_log_service.record_send(
                    session, purpose, normalized, False, owner.id, request_ip,
                    "该邮箱已被其他账号绑定",
                )
                raise BusinessError("该邮箱已被其他账号绑定", 40061)
            await self.geetest.consume(session, challenge)
            await EmailService(session, self.email_log_service).send_code(
                normalized, purpose, challenge, owner.id, request_ip
            )
            return

        await self.geetest.consume(session, challenge)
        email_owner = await session.scalar(
            select(User).where(User.email == normalized)
        )
        active_user = (
            email_owner
            if email_owner is not None and not email_owner.is_deleted
            else None
        )
        should_send = (purpose == "login" and active_user is not None) or (
            purpose == "register" and email_owner is None
        )
        if should_send:
            await EmailService(session, self.email_log_service).send_code(
                normalized,
                purpose,
                challenge,
                active_user.id if active_user else None,
                request_ip,
            )
            return
        # 登录/注册用途不向客户端暴露邮箱是否存在，但被拒绝的发送同样必须写入邮件日志。
        reason = "该邮箱未注册或对应账号已注销" if purpose == "login" else "该邮箱已被注册"
        await self.email_log_service.record_send(
            session, purpose, normalized, False,
            email_owner.id if email_owner else None, request_ip, reason,
        )

    async def get_current_user(self, session: AsyncSession, token: str) -> User:
        """根据会话令牌获取当前有效用户。"""
        auth_session = await session.scalar(
            select(AuthSession).where(
                AuthSession.token_hash == hash_token(token),
                AuthSession.is_deleted.is_(False),
                AuthSession.expires_at > beijing_now(),
            )
        )
        if auth_session is None:
            raise BusinessError("登录状态已失效，请重新登录", 40101)
        user = await session.scalar(
            select(User).where(
                User.id == auth_session.owner_id,
                User.is_deleted.is_(False),
                User.is_active.is_(True),
            )
        )
        if user is None:
            raise BusinessError("账号不可用，请重新登录", 40102)
        return user

    async def logout(self, session: AsyncSession, token: str) -> None:
        """软删除当前登录会话。"""
        auth_session = await session.scalar(
            select(AuthSession).where(
                AuthSession.token_hash == hash_token(token),
                AuthSession.is_deleted.is_(False),
            )
        )
        if auth_session is not None:
            auth_session.is_deleted = True
            auth_session.updated_at = beijing_now()
            await session.commit()

    async def update_profile(
        self, session: AsyncSession, user: User, payload: ProfileUpdateRequest
    ) -> dict:
        """更新当前用户昵称。"""
        user.nickname = payload.nickname.strip()
        user.updated_at = beijing_now()
        await session.commit()
        return await self.serialize_user(session, user)

    async def update_password(
        self, session: AsyncSession, user: User, payload: PasswordUpdateRequest
    ) -> None:
        """验证原密码后修改密码并撤销已有会话。"""
        if not verify_password(payload.current_password, user.password_hash):
            raise BusinessError("当前密码不正确", 40012)
        if payload.current_password == payload.new_password:
            raise BusinessError("新密码不能与当前密码相同", 40013)
        user.password_hash = hash_password(payload.new_password)
        user.updated_at = beijing_now()
        await session.execute(
            update(AuthSession)
            .where(AuthSession.owner_id == user.id, AuthSession.is_deleted.is_(False))
            .values(is_deleted=True, updated_at=beijing_now())
        )
        await session.commit()

    async def bind_email(
        self, session: AsyncSession, user: User, payload: EmailBindRequest
    ) -> dict:
        """验证绑定验证码并更新邮箱。"""
        email = str(payload.email).lower()
        existing = await session.scalar(
            select(User).where(
                User.email == email,
                User.id != user.id,
            )
        )
        if existing is not None:
            raise BusinessError("该邮箱已被其他账号绑定", 40061)
        await EmailService(session).verify_code(
            email, "bind", payload.email_code, payload.geetest_challenge, user.id
        )
        user.email = email
        user.email_verified_at = beijing_now()
        user.updated_at = beijing_now()
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise BusinessError("该邮箱已被其他账号绑定", 40061) from exc
        return await self.serialize_user(session, user)

    async def _create_login_session(self, session: AsyncSession, user: User) -> dict:
        """创建会话并序列化认证响应。"""
        token = create_session_token()
        session.add(
            AuthSession(
                owner_id=user.id,
                token_hash=hash_token(token),
                expires_at=beijing_now() + timedelta(hours=self.settings.session_hours),
            )
        )
        await session.commit()
        return {
            "token": token,
            "expires_in": self.settings.session_hours * 3600,
            "user": await self.serialize_user(session, user),
        }

    async def serialize_user(self, session: AsyncSession, user: User) -> dict:
        """将用户模型转换为前端资料结构。"""
        role_name = await session.scalar(
            select(DataDictionary.item_name).where(
                DataDictionary.dict_type == "user_role",
                DataDictionary.item_code == user.role_code,
                DataDictionary.is_deleted.is_(False),
            )
        )
        return {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "email_verified": user.email_verified_at is not None,
            "nickname": user.nickname,
            "role_code": user.role_code,
            "role_name": role_name or user.role_code,
            "last_login_at": format_beijing(user.last_login_at),
            "created_at": format_beijing(user.created_at),
        }
