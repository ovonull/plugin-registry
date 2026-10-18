"""
应用环境配置。

集中管理数据库、认证、OneBot 网络和服务监听参数，避免业务代码硬编码地址。
"""

from dataclasses import dataclass
from functools import lru_cache
import os
from pathlib import Path


def _load_local_env() -> None:
    """读取项目根目录 .env，显式环境变量优先。"""
    env_path = Path(__file__).resolve().parents[3] / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_local_env()


# 极验凭据固定写入代码，部署容器无需额外配置环境变量。
GEETEST_CAPTCHA_ID = "a30cdbb466e9349385762477cb2c7df6"
GEETEST_PRIVATE_KEY = "6f70322308eb29ae0d85516a14a32d2c"
GEETEST_USER_ID = "napcat_qq_service"
# 应用加密密钥：加密 SMTP 密码和 AI API Key，并参与邮箱验证码哈希；修改会影响历史数据。
SETTINGS_ENCRYPTION_SECRET = os.getenv("SETTINGS_ENCRYPTION_SECRET", "").strip()


def _read_int(name: str, default: int, minimum: int = 1) -> int:
    """读取并校验整数环境变量。"""
    raw_value = os.getenv(name, str(default))
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"环境变量 {name} 必须是整数") from exc
    if value < minimum:
        raise ValueError(f"环境变量 {name} 不能小于 {minimum}")
    return value


def _read_bool(name: str, default: bool) -> bool:
    """读取并校验布尔环境变量，接受 true/false、1/0、yes/no。"""
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        return default
    normalized = raw_value.strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    raise ValueError(f"环境变量 {name} 必须是 true 或 false")


def _read_public_service_url() -> str:
    """读取并校验对外服务基址。

    Returns:
        去除末尾斜杠的 HTTP/HTTPS 服务基址，未配置时返回空字符串。
    """
    value = os.getenv("PUBLIC_SERVICE_URL", "").strip().rstrip("/")
    if value and not value.lower().startswith(("http://", "https://")):
        raise ValueError("环境变量 PUBLIC_SERVICE_URL 必须以 http:// 或 https:// 开头")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    """应用运行参数。"""

    host: str = "0.0.0.0"
    port: int = 8120
    database_url: str = "sqlite+aiosqlite:///./data/plugin_registry.db"
    database_retry_count: int = 10
    database_retry_interval: int = 3
    database_connect_timeout: int = 10
    session_hours: int = 168
    admin_username: str = "admin"
    admin_password: str = ""
    napcat_token: str = ""
    service_token: str = ""
    public_service_url: str = ""
    onebot_action_timeout: int = 90
    event_queue_size: int = 500
    redis_url: str = ""
    ai_followup_ttl: int = 259200
    cors_origins: tuple[str, ...] = ("*",)
    log_level: str = "info"
    log_file: str = "./data/logs/plugin_registry.log"
    log_backup_count: int = 30
    geetest_api_url: str = "https://api.geetest.com"
    geetest_timeout: float = 5.0
    # 插件系统总开关，默认开启；需要整体停用时显式设置 PLUGIN_ENABLED=false。
    plugin_enabled: bool = True
    plugin_data_dir: str = "./data/plugins"
    plugin_package_max_size: int = 10485760
    plugin_repo_timeout: int = 60
    # 可选的 GitHub 令牌，只用于「自动探测最新版本」时提高接口调用频率上限，
    # 不填也能用（未认证调用每小时 60 次，够单机用）。
    plugin_repo_token: str = ""
    # 插件执行方式：null 只登记与审核、不执行代码；subprocess 由独立子进程运行插件。
    plugin_runtime: str = "subprocess"
    # 单条事件交给插件的处理超时，以及插件出网请求的超时，单位均为秒。
    plugin_event_timeout: int = 10
    plugin_http_timeout: int = 10

    @classmethod
    def from_env(cls) -> "Settings":
        """从环境变量构造配置并完成基础校验。"""
        origins = tuple(
            value.strip()
            for value in os.getenv("CORS_ORIGINS", "*").split(",")
            if value.strip()
        )
        return cls(
            host=os.getenv("HOST", "0.0.0.0"),
            port=_read_int("PORT", 8120),
            database_url=os.getenv(
                "DATABASE_URL", "sqlite+aiosqlite:///./data/plugin_registry.db"
            ),
            database_retry_count=_read_int("DATABASE_RETRY_COUNT", 10),
            database_retry_interval=_read_int("DATABASE_RETRY_INTERVAL", 3),
            database_connect_timeout=_read_int("DATABASE_CONNECT_TIMEOUT", 10),
            session_hours=_read_int("SESSION_HOURS", 168),
            admin_username=os.getenv("ADMIN_USERNAME", "admin").strip(),
            admin_password=os.getenv("ADMIN_PASSWORD", ""),
            napcat_token=os.getenv("NAPCAT_TOKEN", ""),
            service_token=os.getenv("SERVICE_TOKEN", ""),
            public_service_url=_read_public_service_url(),
            onebot_action_timeout=_read_int("ONEBOT_ACTION_TIMEOUT", 90),
            event_queue_size=_read_int("EVENT_QUEUE_SIZE", 500),
            redis_url=os.getenv("REDIS_URL", "").strip(),
            ai_followup_ttl=_read_int("AI_FOLLOWUP_TTL", 259200),
            cors_origins=origins or ("*",),
            log_level=os.getenv("LOG_LEVEL", "info").lower(),
            log_file=os.getenv(
                "LOG_FILE", "./data/logs/plugin_registry.log"
            ).strip(),
            log_backup_count=_read_int("LOG_BACKUP_COUNT", 30),
            geetest_api_url="https://api.geetest.com",
            geetest_timeout=5.0,
            plugin_enabled=_read_bool("PLUGIN_ENABLED", True),
            plugin_data_dir=os.getenv("PLUGIN_DATA_DIR", "./data/plugins").strip()
            or "./data/plugins",
            plugin_package_max_size=_read_int("PLUGIN_PACKAGE_MAX_SIZE", 10485760),
            plugin_repo_timeout=_read_int("PLUGIN_REPO_TIMEOUT", 60),
            plugin_repo_token=os.getenv("PLUGIN_REPO_TOKEN", "").strip(),
            plugin_runtime=os.getenv("PLUGIN_RUNTIME", "subprocess").strip().lower()
            or "subprocess",
            plugin_event_timeout=_read_int("PLUGIN_EVENT_TIMEOUT", 10),
            plugin_http_timeout=_read_int("PLUGIN_HTTP_TIMEOUT", 10),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """返回进程级配置单例。"""
    return Settings.from_env()
