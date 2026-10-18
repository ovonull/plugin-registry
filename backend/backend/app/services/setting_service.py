"""系统设置分组读取、保存与敏感字段脱敏。"""

from collections.abc import Mapping
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.config import SETTINGS_ENCRYPTION_SECRET
from app.common.responses import BusinessError
from app.models.entities import SystemSetting


GROUP_KEYS: dict[str, dict[str, str]] = {
    "email": {
        "host": "smtp.host",
        "port": "smtp.port",
        "username": "smtp.username",
        "password": "smtp.password",
        "sender_name": "smtp.sender_name",
        "use_ssl": "smtp.use_ssl",
    },
    "appearance": {
        "color": "theme.color",
        "font": "theme.font",
        "effect": "theme.effect",
    },
    "introduction": {
        "name": "system.name",
        "description": "system.description",
    },
    "animated_emoji": {
        "classification_prompt": "animated_emoji.classification_prompt",
    },
}
SENSITIVE_KEYS = {"smtp.password"}
PUBLIC_GROUPS = ("appearance", "introduction")
DEFAULTS = {
    "email": {
        "host": "",
        "port": "465",
        "username": "",
        "password": "",
        "sender_name": "NapCatQQ Service",
        "use_ssl": "true",
    },
    "appearance": {"color": "ocean", "font": "system", "effect": "solid"},
    "introduction": {
        "name": "NapCatQQ Service",
        "description": "QQ 机器人与 OneBot 服务管理平台",
    },
    "animated_emoji": {
        "classification_prompt": (
            "你是 QQ 动画表情图片分类器，必须先确认你确实看到了图片内容。"
            "无论图片是否属于典型动画表情，都必须根据实际图片内容归入一个中文分类，"
            "并始终返回严格 JSON："
            '{"success":true,"category":"搞笑"}。'
            "如果内容不清晰、不是动画表情或难以归类，也必须选择最接近的中文分类；"
            "确实无法归入具体类型时使用“其他”，仍然返回 success=true 和非空 category。"
            "绝对不要返回 success=false、空 category 或“未分类”。"
            "只允许返回 JSON，不要 Markdown、解释、闲聊或其他文字。"
        ),
    },
}


def _fernet() -> Fernet:
    """使用固定应用密钥加密 SMTP 密码，确保重启后仍可读取。"""
    key = base64.urlsafe_b64encode(
        hashlib.sha256(SETTINGS_ENCRYPTION_SECRET.encode("utf-8")).digest()
    )
    return Fernet(key)


def encrypt_secret(value: str) -> str:
    """加密需要可恢复读取的应用密钥。"""
    return "enc:" + _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str) -> str:
    """解密应用密钥，历史明文值保持兼容。"""
    if not value.startswith("enc:"):
        return value
    try:
        return _fernet().decrypt(value[4:].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeDecodeError):
        return ""


class SettingService:
    """集中处理全局设置，路由层不直接操作设置表。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def initialize_defaults(self) -> None:
        """幂等补齐各设置 Tab 的默认记录。"""
        existing = {
            row.setting_key: row
            for row in (
                await self.session.execute(select(SystemSetting))
            ).scalars().all()
        }
        for group, fields in DEFAULTS.items():
            for field, value in fields.items():
                key = GROUP_KEYS[group][field]
                if key in existing:
                    continue
                self.session.add(
                    SystemSetting(
                        group_name=group,
                        setting_key=key,
                        setting_value=encrypt_secret(value) if key in SENSITIVE_KEYS else value,
                        description=key,
                        is_sensitive=key in SENSITIVE_KEYS,
                    )
                )
        await self.session.commit()

    async def _records(self, group: str) -> dict[str, SystemSetting]:
        if group not in GROUP_KEYS:
            raise BusinessError("系统设置分组不存在", 40041)
        rows = (
            await self.session.execute(
                select(SystemSetting).where(
                    SystemSetting.group_name == group,
                    SystemSetting.is_deleted.is_(False),
                )
            )
        ).scalars().all()
        return {row.setting_key: row for row in rows}

    async def get_group(self, group: str, mask_sensitive: bool = True) -> dict:
        """读取指定 Tab，敏感字段默认返回掩码。"""
        records = await self._records(group)
        output: dict[str, object] = {}
        for field, key in GROUP_KEYS[group].items():
            row = records.get(key)
            value = decrypt_secret(row.setting_value) if row else DEFAULTS[group].get(field, "")
            if key in SENSITIVE_KEYS and mask_sensitive:
                value = "******" if value else ""
            if field == "port":
                output[field] = int(value or 0)
            elif field == "use_ssl":
                output[field] = str(value).lower() == "true"
            else:
                output[field] = value
        return output

    async def get_public(self) -> dict:
        """返回登录页可用的外观和系统介绍设置。"""
        return {
            group: await self.get_group(group)
            for group in PUBLIC_GROUPS
        }

    async def save_group(self, group: str, values: Mapping[str, object]) -> dict:
        """保存单个设置 Tab，空密码或掩码表示保留原值。"""
        records = await self._records(group)
        for field, key in GROUP_KEYS[group].items():
            if field not in values:
                continue
            raw = str(values[field]).strip()
            if key in SENSITIVE_KEYS and raw in {"", "******"}:
                continue
            stored = encrypt_secret(raw) if key in SENSITIVE_KEYS else raw
            row = records.get(key)
            if row is None:
                self.session.add(
                    SystemSetting(
                        group_name=group,
                        setting_key=key,
                        setting_value=stored,
                        description=key,
                        is_sensitive=key in SENSITIVE_KEYS,
                    )
                )
            else:
                row.setting_value = stored
        await self.session.commit()
        return await self.get_group(group)

    async def get_raw_email(self) -> dict:
        """供 SMTP 发送服务读取未脱敏配置。"""
        return await self.get_group("email", mask_sensitive=False)
