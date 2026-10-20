"""插件库分发接口的接入令牌管理。

设计要点：

- 明文令牌只在创建时返回一次，库里只存 sha256 摘要与前缀，泄露风险最小；
- 支持多条令牌并行，便于给不同下游分别发放、分别吊销；
- 鉴权命中后回写 ``last_used_at``，但一分钟内不重复写库，避免每次请求都产生 UPDATE。
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.responses import BusinessError
from app.common.security import hash_token
from app.common.time_utils import beijing_now
from app.models.plugin import RegistryToken

TOKEN_PREFIX = "reg_"
# 前缀长度用于列表展示：reg_ + 8 位十六进制，足够人工区分又不泄露主体。
DISPLAY_PREFIX_LENGTH = 12
MAX_TOKEN_NAME_LENGTH = 100
# last_used_at 的最小写入间隔，避免高频请求反复更新同一行。
LAST_USED_WRITE_INTERVAL = timedelta(seconds=60)


class RegistryTokenService:
    """接入令牌的创建、列表、吊销与校验。"""

    async def create(
        self, session: AsyncSession, name: str
    ) -> tuple[RegistryToken, str]:
        """创建一枚新令牌。

        Args:
            session: 数据库会话。
            name: 用途标注，可为空。
        Returns:
            (记录, 明文令牌)，明文字段仅本次可用。
        """
        plain = f"{TOKEN_PREFIX}{secrets.token_hex(24)}"
        row = RegistryToken(
            name=str(name or "").strip()[:MAX_TOKEN_NAME_LENGTH],
            token_hash=hash_token(plain),
            token_prefix=plain[:DISPLAY_PREFIX_LENGTH],
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row, plain

    async def list_tokens(self, session: AsyncSession) -> list[RegistryToken]:
        """按创建时间倒序返回未吊销的令牌。"""
        rows = await session.execute(
            select(RegistryToken)
            .where(RegistryToken.is_deleted.is_(False))
            .order_by(RegistryToken.created_at.desc())
        )
        return list(rows.scalars())

    async def revoke(self, session: AsyncSession, token_id: int) -> None:
        """吊销令牌：软删除后立即失效。"""
        row = await session.get(RegistryToken, token_id)
        if row is None or row.is_deleted:
            raise BusinessError("令牌不存在或已吊销", 40480)
        row.is_deleted = True
        row.updated_at = beijing_now()
        await session.commit()

    async def verify(self, session: AsyncSession, plaintext: str) -> bool:
        """校验明文令牌是否有效，命中时顺带回写最后使用时间。"""
        raw = str(plaintext or "").strip()
        if not raw:
            return False
        row = await session.scalar(
            select(RegistryToken).where(
                RegistryToken.token_hash == hash_token(raw),
                RegistryToken.is_deleted.is_(False),
            )
        )
        if row is None:
            return False
        now = datetime.now(timezone.utc)
        last = row.last_used_at
        if last is None or (now - _aware(last)) >= LAST_USED_WRITE_INTERVAL:
            row.last_used_at = beijing_now()
            await session.commit()
        return True


def _aware(value: datetime) -> datetime:
    """把可能存在的朴素时间当作 UTC，便于与当前时间比较。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def mask_prefix(prefix: str) -> str:
    """列表展示用：前缀加掩码，例如 ``reg_7b5e******``。"""
    raw = str(prefix or "")
    return f"{raw}******" if raw else "******"


def sha256_hex(value: str) -> str:
    """调试辅助：与鉴权使用同一算法，便于核对。"""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
