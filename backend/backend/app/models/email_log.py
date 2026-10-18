"""邮件日志模型。

功能：
1. 定义邮件日志表 email_logs，记录每一次触发邮件发送的成功与失败；
2. 日志只追加，禁止 UPDATE / DELETE，因此不提供软删除字段。
"""

from sqlalchemy import Boolean, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IntegerPrimaryKeyMixin, TimestampMixin


class EmailLog(Base, IntegerPrimaryKeyMixin, TimestampMixin):
    """邮件发送日志：任何一次触发发信都必须落库，只追加、不允许修改和删除。"""

    __tablename__ = "email_logs"
    __table_args__ = (
        Index("ix_email_log_email_time", "email", "created_at"),
        Index("ix_email_log_owner_time", "owner_id", "created_at"),
        {"comment": "邮件日志"},
    )

    owner_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True, comment="触发用户标识，找不到对应用户时为空"
    )
    email: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True, comment="收件邮箱"
    )
    action: Mapped[str] = mapped_column(
        String(30), nullable=False, index=True, comment="触发操作编码（数据字典 email_log_action）"
    )
    is_success: Mapped[bool] = mapped_column(
        Boolean, nullable=False, index=True, comment="是否发送成功"
    )
    client_ip: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="客户端 IP（优先取 X-Forwarded-For）"
    )
    result_message: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="结果说明（成功结果或失败原因）"
    )
