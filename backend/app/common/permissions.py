"""集中定义角色代码、数据范围和记录操作权限。"""

from app.common.responses import BusinessError
from app.models.entities import AutoReplyRule, User


ROLE_ADMIN = "admin"
ROLE_USER = "user"


def is_admin(user: User) -> bool:
    """判断用户是否为管理员。"""
    return user.role_code == ROLE_ADMIN


def owner_scope(user: User) -> int | None:
    """返回查询数据范围，管理员返回 None 表示可查看全部。"""
    return None if is_admin(user) else user.id


def ensure_rule_access(user: User, rule: AutoReplyRule) -> None:
    """确保用户有权修改指定自动回复规则。"""
    if not is_admin(user) and rule.owner_id != user.id:
        raise BusinessError("无权操作该自动回复规则", 40301)

