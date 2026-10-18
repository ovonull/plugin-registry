"""应用日志配置。

统一将应用、Uvicorn、OneBot、AI 和数据库日志输出到控制台及 UTF-8 文件，
文件按北京时间每天轮转，避免服务重启或日志过大后丢失排障信息。
"""

from datetime import datetime
import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from zoneinfo import ZoneInfo

from app.common.config import Settings


BEIJING_TZ = ZoneInfo("Asia/Shanghai")
_FILE_HANDLER_MARKER = "_napcat_file_handler"
_CONSOLE_HANDLER_MARKER = "_napcat_console_handler"


class BeijingFormatter(logging.Formatter):
    """使用北京时间输出日志时间。"""

    def formatTime(
        self, record: logging.LogRecord, datefmt: str | None = None
    ) -> str:
        current = datetime.fromtimestamp(record.created, BEIJING_TZ)
        return current.strftime(datefmt or "%Y-%m-%d %H:%M:%S.%f")[:-3]


def _log_level(value: str) -> int:
    """将配置中的日志级别转换为 logging 常量。"""
    level = getattr(logging, str(value or "INFO").upper(), logging.INFO)
    return level if isinstance(level, int) else logging.INFO


def _file_path(value: str) -> Path:
    """解析日志文件路径并确保父目录存在。"""
    path = Path(value or "./data/logs/napcat_service.log").expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _find_file_handler(
    loggers: tuple[logging.Logger, ...], path: Path
) -> logging.Handler | None:
    """在多个日志器中查找可共享的指定文件 handler。"""
    resolved = str(path.resolve())
    for logger in loggers:
        for handler in logger.handlers:
            if (
                getattr(handler, _FILE_HANDLER_MARKER, False)
                and str(getattr(handler, "baseFilename", "")) == resolved
            ):
                return handler
    return None


def _find_console_handler(
    loggers: tuple[logging.Logger, ...],
) -> logging.Handler | None:
    """在多个日志器中查找可共享的控制台处理器。"""
    for logger in loggers:
        for handler in logger.handlers:
            if getattr(handler, _CONSOLE_HANDLER_MARKER, False):
                return handler
    return None


def configure_logging(settings: Settings) -> Path:
    """配置控制台和滚动文件日志，重复调用保持幂等。

    Args:
        settings: 应用配置，包含日志级别、文件位置和保留天数。
    Returns:
        实际使用的日志文件绝对路径。
    """
    path = _file_path(settings.log_file)
    level = _log_level(settings.log_level)
    formatter = BeijingFormatter(
        fmt=(
            "%(asctime)s | %(levelname)s | %(name)s | "
            "pid=%(process)d | thread=%(threadName)s | "
            "%(filename)s:%(lineno)d | %(message)s"
        ),
        datefmt="%Y-%m-%d %H:%M:%S.%f",
    )
    file_loggers = (
        logging.getLogger(),
        logging.getLogger("uvicorn.error"),
        logging.getLogger("uvicorn.access"),
    )
    shared_handler = _find_file_handler(file_loggers, path)
    if shared_handler is None:
        shared_handler = TimedRotatingFileHandler(
            filename=path,
            when="midnight",
            interval=1,
            backupCount=settings.log_backup_count,
            encoding="utf-8",
            delay=True,
            utc=False,
        )
        shared_handler.setLevel(level)
        shared_handler.setFormatter(formatter)
        setattr(shared_handler, _FILE_HANDLER_MARKER, True)

    console_handler = _find_console_handler(file_loggers)
    if console_handler is None:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        setattr(console_handler, _CONSOLE_HANDLER_MARKER, True)

    stale_handlers: set[logging.Handler] = set()
    for logger in file_loggers:
        logger.setLevel(level)
        if logger.name in {"uvicorn.error", "uvicorn.access"}:
            logger.propagate = False
        for handler in tuple(logger.handlers):
            if not getattr(handler, _FILE_HANDLER_MARKER, False):
                continue
            if handler is shared_handler:
                continue
            logger.removeHandler(handler)
            stale_handlers.add(handler)
        for handler in tuple(logger.handlers):
            if not getattr(handler, _CONSOLE_HANDLER_MARKER, False):
                continue
            if handler is console_handler:
                continue
            logger.removeHandler(handler)
            stale_handlers.add(handler)
        if logger.name in {"uvicorn.error", "uvicorn.access"}:
            # Uvicorn 自带处理器会与统一控制台处理器重复输出。
            for handler in tuple(logger.handlers):
                if handler in {shared_handler, console_handler}:
                    continue
                logger.removeHandler(handler)
        if shared_handler not in logger.handlers:
            logger.addHandler(shared_handler)
        if console_handler not in logger.handlers:
            logger.addHandler(console_handler)
    for handler in stale_handlers:
        handler.close()
    return path.resolve()
