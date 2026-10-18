"""SQLAlchemy 声明基类与公共字段。"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.common.time_utils import beijing_now


class Base(DeclarativeBase):
    """所有数据库模型的声明基类。"""


class TimestampMixin:
    """统一提供北京时间创建和更新时间字段。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=beijing_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=beijing_now, onupdate=beijing_now, nullable=False
    )


class SoftDeleteMixin:
    """统一提供软删除字段。"""

    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class IntegerPrimaryKeyMixin:
    """统一提供自增整型主键。"""

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

