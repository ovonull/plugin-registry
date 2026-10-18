"""极验行为验证 3.0 初始化、二次校验和一次性消费。"""

from datetime import timedelta
import hashlib
import logging
import secrets

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.config import (
    GEETEST_CAPTCHA_ID,
    GEETEST_PRIVATE_KEY,
    GEETEST_USER_ID,
    Settings,
)
from app.common.responses import BusinessError
from app.common.time_utils import beijing_now
from app.models.entities import GeetestChallenge


logger = logging.getLogger(__name__)
SDK_VERSION = "python-fastapi:3.1.1"


class GeetestService:
    """封装 GT3 上游请求，并在数据库保存 challenge 状态。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.http_client = httpx.AsyncClient(
            timeout=settings.geetest_timeout,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    async def close(self) -> None:
        """释放极验 HTTP 连接池。"""
        await self.http_client.aclose()

    async def register(self, session: AsyncSession) -> dict:
        """从极验取得初始化 challenge，并登记为待验证状态。"""
        url = f"{self.settings.geetest_api_url}/register.php"
        params = {
            "gt": GEETEST_CAPTCHA_ID,
            "json_format": "1",
            "sdk": SDK_VERSION,
            "digestmod": "md5",
            "user_id": GEETEST_USER_ID,
            "client_type": "web",
        }
        try:
            response = await self.http_client.get(url, params=params)
            response.raise_for_status()
            raw_challenge = response.json().get("challenge", "")
        except Exception as exc:
            logger.error("极验初始化请求失败：%s", exc)
            raise BusinessError("极验服务暂时不可用，请稍后重试", 50311) from exc
        if len(raw_challenge) != 32:
            raise BusinessError("极验初始化返回无效，请稍后重试", 50312)

        challenge = hashlib.md5(
            (raw_challenge + GEETEST_PRIVATE_KEY).encode("utf-8")
        ).hexdigest()
        session.add(
            GeetestChallenge(
                challenge=challenge,
                expires_at=beijing_now() + timedelta(minutes=10),
            )
        )
        await session.commit()
        return {
            "success": 1,
            "gt": GEETEST_CAPTCHA_ID,
            "challenge": challenge,
            "new_captcha": True,
        }

    async def validate(
        self,
        session: AsyncSession,
        challenge: str,
        validate: str,
        seccode: str,
    ) -> None:
        """完成 GT3 二次校验，将 challenge 标记为已验证。"""
        expected_validate = hashlib.md5(
            (GEETEST_PRIVATE_KEY + "geetest" + challenge).encode("utf-8")
        ).hexdigest()
        if not secrets.compare_digest(validate, expected_validate):
            raise BusinessError("滑动验证签名无效，请重新验证", 40032)

        url = f"{self.settings.geetest_api_url}/validate.php"
        payload = {
            "seccode": seccode,
            "json_format": "1",
            "challenge": challenge,
            "sdk": SDK_VERSION,
            "captchaid": GEETEST_CAPTCHA_ID,
            "user_id": GEETEST_USER_ID,
            "client_type": "web",
        }
        try:
            response = await self.http_client.post(url, data=payload)
            response.raise_for_status()
            response_seccode = response.json().get("seccode")
        except Exception as exc:
            logger.error("极验二次校验请求失败，challenge=%s：%s", challenge[:12], exc)
            raise BusinessError("极验校验服务异常，请重新验证", 50313) from exc
        if not response_seccode or response_seccode == "false":
            raise BusinessError("滑动验证未通过，请重试", 40032)

        # GT3 最终返回的 challenge 可能与初始化值不同。参照官方流程，
        # 二次校验成功后才以该 challenge 建立一次性验证状态。
        record = await session.scalar(
            select(GeetestChallenge)
            .where(GeetestChallenge.challenge == challenge)
            .with_for_update()
        )
        if record is not None and (
            record.is_verified or record.consumed_at is not None
        ):
            raise BusinessError("滑动验证结果已提交，请重新验证", 40034)

        now = beijing_now()
        if record is None:
            session.add(
                GeetestChallenge(
                    challenge=challenge,
                    is_verified=True,
                    verified_at=now,
                    expires_at=now + timedelta(minutes=5),
                )
            )
        else:
            record.is_deleted = False
            record.is_verified = True
            record.verified_at = now
            record.consumed_at = None
            record.expires_at = now + timedelta(minutes=5)
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise BusinessError("滑动验证结果已提交，请重新验证", 40034) from exc

    async def consume(self, session: AsyncSession, challenge: str) -> None:
        """原子检查并一次性消费已通过的 challenge。"""
        record = await session.scalar(
            select(GeetestChallenge)
            .where(
                GeetestChallenge.challenge == challenge,
                GeetestChallenge.is_verified.is_(True),
                GeetestChallenge.consumed_at.is_(None),
                GeetestChallenge.is_deleted.is_(False),
                GeetestChallenge.expires_at > beijing_now(),
            )
            .with_for_update()
        )
        if record is None:
            raise BusinessError("请先完成滑动验证", 40033)
        record.consumed_at = beijing_now()
        await session.commit()
