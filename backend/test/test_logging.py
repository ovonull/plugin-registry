"""后端文件日志、时间格式和完整参数输出测试。"""

import io
import logging
from pathlib import Path
import sys

from app.common.config import Settings
from app.common.log_config import configure_logging


def _close_logging_handlers() -> None:
    """关闭测试配置创建的文件和控制台 handler，避免资源残留。"""
    for logger in (
        logging.getLogger(),
        logging.getLogger("uvicorn.error"),
        logging.getLogger("uvicorn.access"),
    ):
        for handler in tuple(logger.handlers):
            if not (
                getattr(handler, "_napcat_file_handler", False)
                or getattr(handler, "_napcat_console_handler", False)
            ):
                continue
            logger.removeHandler(handler)
            handler.close()


def test_detailed_file_logging_preserves_full_data(tmp_path: Path) -> None:
    """日志写入 UTF-8 文件，包含完整上下文和原始参数。"""
    path = tmp_path / "logs" / "service.log"
    try:
        configure_logging(Settings(log_file=str(path), log_backup_count=2))
        logging.getLogger("app.logging_test").info(
            "token=secret-token authorization=Bearer another-secret 详细日志"
        )
        logging.getLogger("uvicorn.error").info("OneBot 详细动作日志")
        console_handler = next(
            handler
            for logger in (
                logging.getLogger(),
                logging.getLogger("uvicorn.error"),
                logging.getLogger("uvicorn.access"),
            )
            for handler in logger.handlers
            if getattr(handler, "_napcat_console_handler", False)
        )
        console_output = io.StringIO()
        console_handler.setStream(console_output)
        try:
            logging.getLogger("app.logging_test").info("控制台同步日志")
        finally:
            console_handler.setStream(sys.stderr)
        for logger in (
            logging.getLogger(),
            logging.getLogger("uvicorn.error"),
            logging.getLogger("uvicorn.access"),
        ):
            for handler in logger.handlers:
                handler.flush()
        content = path.read_text(encoding="utf-8")
        assert "详细日志" in content
        assert "pid=" in content and "thread=" in content
        assert "secret-token" in content
        assert "Bearer another-secret" in content
        assert content.count("OneBot 详细动作日志") == 1
        assert "控制台同步日志" in console_output.getvalue()
    finally:
        _close_logging_handlers()
