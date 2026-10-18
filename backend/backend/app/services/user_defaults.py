"""
用户与认证相关的初始化数据字典。

功能：
1. 集中维护服务启动时需要幂等写入的数据字典项：角色、登录方式、AI 提供方、好友性别和邮件日志触发操作；
2. 由 UserService.initialize_defaults 遍历写入，已存在则跳过，被软删除则恢复。
"""

from app.common.permissions import ROLE_ADMIN, ROLE_USER


# 初始化数据字典项：(字典类型, 编码, 中文名称, 排序)。
USER_DICTIONARY_DEFAULTS = (
    ("user_role", ROLE_ADMIN, "管理员", 10),
    ("user_role", ROLE_USER, "普通用户", 20),
    ("login_type", "username_password", "账号密码", 10),
    ("login_type", "email_password", "邮箱密码", 20),
    ("login_type", "email_code", "邮箱验证码", 30),
    ("ai_provider", "openai_compatible", "OpenAI 兼容", 10),
    ("ai_provider", "anthropic", "Anthropic Claude", 20),
    ("ai_provider", "gemini", "Google Gemini", 30),
    ("ai_provider", "dashscope_app", "DashScope 应用", 40),
    ("friend_sex", "male", "男", 10),
    ("friend_sex", "female", "女", 20),
    ("friend_sex", "unknown", "未知", 30),
    ("email_log_action", "register_code", "注册验证码", 10),
    ("email_log_action", "login_code", "邮箱验证码登录", 20),
    ("email_log_action", "bind_email", "绑定邮箱验证码", 30),
)
