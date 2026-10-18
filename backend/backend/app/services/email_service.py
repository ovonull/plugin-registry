"""SMTP 邮箱验证码发送、限流、哈希存储与一次性校验。"""

import asyncio
from datetime import timedelta
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr
import hashlib
import logging
import secrets
import smtplib

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.config import SETTINGS_ENCRYPTION_SECRET
from app.common.responses import BusinessError
from app.common.time_utils import beijing_now
from app.models.entities import EmailCode
from app.services.email_log_service import EmailLogService
from app.services.setting_service import SettingService


logger = logging.getLogger(__name__)


class EmailService:
    """发送和校验登录、注册、绑定邮箱验证码。"""

    def __init__(
        self, session: AsyncSession, email_log_service: EmailLogService | None = None
    ) -> None:
        self.session = session
        self.email_log_service = email_log_service or EmailLogService()

    @staticmethod
    def _code_hash(email: str, purpose: str, code: str) -> str:
        raw = f"{SETTINGS_ENCRYPTION_SECRET}:{email}:{purpose}:{code}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    async def send_code(
        self,
        email: str,
        purpose: str,
        challenge: str,
        owner_id: int | None,
        request_ip: str | None,
    ) -> None:
        """限流后发送六位验证码并仅保存摘要，任何发送尝试都会写入邮件日志。"""
        normalized = email.strip().lower()
        now = beijing_now()
        latest = await self.session.scalar(
            select(EmailCode)
            .where(
                EmailCode.email == normalized,
                EmailCode.purpose == purpose,
                EmailCode.is_deleted.is_(False),
            )
            .order_by(EmailCode.id.desc())
            .limit(1)
        )
        if latest is not None and latest.created_at > now - timedelta(seconds=60):
            await self.email_log_service.record_send(
                self.session, purpose, normalized, False, owner_id, request_ip,
                "发送过于频繁，请60秒后再试",
            )
            raise BusinessError("发送过于频繁，请60秒后再试", 42901)
        hourly_count = await self.session.scalar(
            select(func.count(EmailCode.id)).where(
                EmailCode.email == normalized,
                EmailCode.purpose == purpose,
                EmailCode.created_at > now - timedelta(hours=1),
                EmailCode.is_deleted.is_(False),
            )
        )
        if int(hourly_count or 0) >= 5:
            await self.email_log_service.record_send(
                self.session, purpose, normalized, False, owner_id, request_ip,
                "该邮箱一小时内发送次数已达上限",
            )
            raise BusinessError("该邮箱一小时内发送次数已达上限", 42902)

        code = f"{secrets.randbelow(1_000_000):06d}"
        try:
            await self._send_smtp(normalized, code, purpose)
        except BusinessError as exc:
            # 配置缺失或 SMTP 发送失败必须先把失败原因落库，再抛出业务异常。
            await self.email_log_service.record_send(
                self.session, purpose, normalized, False, owner_id, request_ip, exc.message
            )
            raise
        except Exception as exc:
            await self.email_log_service.record_send(
                self.session, purpose, normalized, False, owner_id, request_ip,
                f"发送异常：{type(exc).__name__}：{exc}",
            )
            raise
        await self.email_log_service.record_send(
            self.session, purpose, normalized, True, owner_id, request_ip, "邮件发送成功"
        )
        await self.session.execute(
            update(EmailCode)
            .where(
                EmailCode.email == normalized,
                EmailCode.purpose == purpose,
                EmailCode.used_at.is_(None),
                EmailCode.is_deleted.is_(False),
            )
            .values(used_at=now)
        )
        self.session.add(
            EmailCode(
                owner_id=owner_id,
                email=normalized,
                purpose=purpose,
                challenge=challenge,
                code_hash=self._code_hash(normalized, purpose, code),
                expires_at=now + timedelta(minutes=10),
                request_ip=request_ip,
            )
        )
        await self.session.commit()

    async def verify_code(
        self,
        email: str,
        purpose: str,
        code: str,
        challenge: str,
        owner_id: int | None = None,
    ) -> None:
        """验证与 challenge 绑定的最新验证码并标记为已使用。"""
        normalized = email.strip().lower()
        conditions = [
            EmailCode.email == normalized,
            EmailCode.purpose == purpose,
            EmailCode.challenge == challenge,
            EmailCode.used_at.is_(None),
            EmailCode.is_deleted.is_(False),
        ]
        if owner_id is not None:
            conditions.append(EmailCode.owner_id == owner_id)
        record = await self.session.scalar(
            select(EmailCode)
            .where(*conditions)
            .order_by(EmailCode.id.desc())
            .limit(1)
            .with_for_update()
        )
        now = beijing_now()
        if record is None or record.expires_at < now:
            raise BusinessError("邮箱验证码无效或已过期", 40051)
        if record.attempt_count >= 5:
            record.used_at = now
            await self.session.commit()
            raise BusinessError("邮箱验证码尝试次数过多，请重新获取", 40053)
        if not secrets.compare_digest(
            record.code_hash,
            self._code_hash(normalized, purpose, code),
        ):
            record.attempt_count += 1
            await self.session.commit()
            raise BusinessError("邮箱验证码不正确", 40052)
        record.used_at = now
        await self.session.flush()

    async def _send_smtp(self, to_email: str, code: str, purpose: str) -> None:
        smtp = await SettingService(self.session).get_raw_email()
        if not smtp.get("host") or not smtp.get("username") or not smtp.get("password"):
            raise BusinessError("系统尚未配置发件邮箱，请联系管理员", 50051)
        action = {"login": "登录", "register": "注册", "bind": "绑定邮箱"}[purpose]
        try:
            await asyncio.to_thread(self._send_blocking, smtp, to_email, code, action)
        except Exception as exc:
            logger.error("邮箱验证码发送失败，email=%s：%s", to_email, exc)
            raise BusinessError("邮件发送失败，请检查邮箱地址或联系管理员", 50052) from exc

    @staticmethod
    def _send_blocking(smtp: dict, to_email: str, code: str, action: str) -> None:
        """在线程池中执行阻塞 SMTP 发送。"""
        message = MIMEText(
            f"您的{action}验证码为：{code}\n\n验证码10分钟内有效，请勿泄露给他人。",
            "plain",
            "utf-8",
        )
        sender_name = str(smtp.get("sender_name") or "NapCatQQ Service")
        username = str(smtp["username"])
        message["Subject"] = Header(f"{action}验证码", "utf-8")
        message["From"] = formataddr((str(Header(sender_name, "utf-8")), username))
        message["To"] = to_email
        host, port = str(smtp["host"]), int(smtp["port"])
        server: smtplib.SMTP
        if smtp.get("use_ssl"):
            server = smtplib.SMTP_SSL(host, port, timeout=20)
        else:
            server = smtplib.SMTP(host, port, timeout=20)
            server.starttls()
        try:
            server.login(username, str(smtp["password"]))
            server.sendmail(username, [to_email], message.as_string())
        finally:
            server.quit()
