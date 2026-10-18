"""系统功能列表的内置数据初始化和查询服务。"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import SystemFeature


DEFAULT_FEATURES: tuple[dict[str, Any], ...] = (
    {
        "code": "ai_special_title",
        "name": "AI 专属头衔",
        "description": "群成员发送关键词后，由机器人 AI 根据成员昵称生成专属群头衔并自动设置。",
        "trigger_keyword": "#头衔 [可选头衔]（支持空格）",
        "category": "AI 功能",
        "sort_order": 10,
    },
    {
        "code": "ai_chat",
        "name": "AI 智能对话",
        "description": "支持私聊、群聊 @机器人和连续对话，由机器人当前 AI 配置生成回复。",
        "trigger_keyword": "私聊或 @机器人",
        "category": "AI 功能",
        "sort_order": 20,
    },
    {
        "code": "ai_trigger_keywords",
        "name": "AI 触发关键词",
        "description": "消息命中机器人配置的关键词后触发 AI 回复，并登记后续免触发次数。",
        "trigger_keyword": "匹配已配置 AI 触发关键词",
        "category": "AI 功能",
        "sort_order": 25,
    },
    {
        "code": "auto_reply",
        "name": "关键词自动回复",
        "description": "按机器人和关键词配置固定回复内容，命中后自动发送。",
        "trigger_keyword": "匹配已配置关键词",
        "category": "消息功能",
        "sort_order": 30,
    },
    {
        "code": "welcome_message",
        "name": "进群欢迎词",
        "description": "新成员加入群聊时发送预设欢迎词，也支持使用机器人 AI 生成。",
        "trigger_keyword": "新成员入群",
        "category": "消息功能",
        "sort_order": 40,
    },
    {
        "code": "forbidden_words",
        "name": "违禁词管理",
        "description": "命中群聊违禁词后自动对发言成员执行禁言动作。",
        "trigger_keyword": "匹配已配置违禁词",
        "category": "群管理",
        "sort_order": 50,
    },
    {
        "code": "api_center",
        "name": "接口中心",
        "description": "浏览 NapCat OneBot 动作目录并按机器人账号手动执行动作。",
        "trigger_keyword": "手动执行动作",
        "category": "工具",
        "sort_order": 60,
    },
)


class FeatureService:
    """维护系统内置功能说明，不提供后台编辑入口。"""

    async def initialize_defaults(self, session: AsyncSession) -> None:
        """幂等补齐内置功能，恢复历史软删除记录但不修改用户数据。"""
        for values in DEFAULT_FEATURES:
            row = await session.scalar(
                select(SystemFeature).where(SystemFeature.code == values["code"])
            )
            if row is None:
                session.add(SystemFeature(**values))
                continue
            if row.is_deleted:
                row.is_deleted = False
            for key, value in values.items():
                setattr(row, key, value)
        await session.commit()

    async def list_features(self, session: AsyncSession) -> list[dict[str, Any]]:
        """查询启用的内置功能列表，按展示顺序返回。"""
        rows = (
            await session.scalars(
                select(SystemFeature)
                .where(
                    SystemFeature.is_enabled.is_(True),
                    SystemFeature.is_deleted.is_(False),
                )
                .order_by(SystemFeature.sort_order, SystemFeature.id)
            )
        ).all()
        return [
            {
                "id": row.id,
                "code": row.code,
                "name": row.name,
                "description": row.description,
                "trigger_keyword": row.trigger_keyword,
                "category": row.category,
                "sort_order": row.sort_order,
                "is_enabled": row.is_enabled,
            }
            for row in rows
        ]
