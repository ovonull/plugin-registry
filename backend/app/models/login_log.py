"""登录日志模型。

功能：
1. 定义登录日志表 login_logs，记录每一次登录的成功与失败；
2. 日志只追加，禁止 UPDATE / DELETE，因此不提供软删除字段。
"""

from sqlalchemy import Boolean, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IntegerPrimaryKeyMixin, TimestampMixin


class LoginLog(Base, IntegerPrimaryKeyMixin, TimestampMixin):
    """用户登录日志：登录成功与失败都必须落库，只追加、不允许修改和删除。"""

    __tablename__ = "login_logs"
    __table_args__ = (
        Index("ix_login_log_owner_time", "owner_id", "created_at"),
        {"comment": "登录日志"},
    )

    owner_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True, comment="用户标识，登录失败时为空"
    )
    login_account: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="登录账号（用户名或邮箱）"
    )
    login_type: Mapped[str] = mapped_column(
        String(30), nullable=False, index=True, comment="登录方式编码（数据字典 login_type）"
    )
    is_success: Mapped[bool] = mapped_column(
        Boolean, nullable=False, index=True, comment="是否登录成功"
    )
    client_ip: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="客户端 IP（优先取 X-Forwarded-For）"
    )
    result_message: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="结果说明（失败原因）"
    )