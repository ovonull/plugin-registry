"""PlugHub 插件库后端统一启动入口。"""

import uvicorn

from app.application import create_app
from app.common.config import get_settings
from app.common.log_config import configure_logging


settings = get_settings()
configure_logging(settings)
app = create_app(settings)


def run() -> None:
    """读取环境配置并启动 ASGI 服务。"""
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
        reload=False,
    )


if __name__ == "__main__":
    run()
