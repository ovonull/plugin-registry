"""北京时间生成和序列化工具。"""

from datetime import datetime
from zoneinfo import ZoneInfo


SHANGHAI_TIMEZONE = ZoneInfo("Asia/Shanghai")


def beijing_now() -> datetime:
    """返回不带时区标记的北京时间，便于 MySQL DATETIME 一致存储。"""
    return datetime.now(SHANGHAI_TIMEZONE).replace(tzinfo=None)


def format_beijing(value: datetime | None) -> str | None:
    """将数据库时间格式化为前端直接展示的北京时间字符串。"""
    if value is None:
        return None
    return value.strftime("%Y-%m-%d %H:%M:%S")

