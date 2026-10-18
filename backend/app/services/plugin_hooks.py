"""
事件主干到插件运行时的唯一委托点。

功能：
1. 为 HTTP 上报与反向 WebSocket 两条主干提供同一个入口，保证两种接入方式行为一致；
2. 总开关关闭时立即返回，不产生任何查询与副作用，新功能默认不影响现有行为；
3. 按机器人读取已启用的插件安装记录，交给插件执行单元处理，不阻塞回复主流程。

约定：
- 本模块只做「查询 + 委托」，不实现任何插件能力，也不直接发送消息或调用 OneBot 动作；
- 事件在主流程通过白名单与屏蔽校验之后再委托，插件看到的范围与系统配置一致。
"""

import asyncio
import logging
from typing import Any

from app.common.config import Settings
from app.common.database import Database
from app.services.plugin_install_service import PluginInstallService
from app.services.plugin_runtime import (
    NullPluginRuntime,
    PluginRuntime,
    build_plugin_runtime,
)


logger = logging.getLogger("uvicorn.error")

# 只把真实业务事件委托给插件，心跳与生命周期事件不进入插件链路。
SUPPORTED_POST_TYPES = frozenset({"message", "notice", "request"})


class PluginHooks:
    """插件事件钩子的分发入口。"""

    def __init__(
        self,
        settings: Settings,
        database: Database,
        runtime: PluginRuntime | None = None,
    ) -> None:
        """
        记录配置与执行单元。

        Args:
            settings: 应用配置。
            database: 数据库连接管理，用于读取已启用的插件安装。
            runtime: 插件执行单元，未传入时按配置构建。
        """
        self.settings = settings
        self.database = database
        self.runtime = runtime or build_plugin_runtime(settings)
        self._tasks: set[asyncio.Task] = set()

    def attach_runtime(self, runtime: PluginRuntime) -> None:
        """
        替换插件执行单元。

        OneBot 服务晚于钩子构建，动作代调入口只能在两者都就绪后接入，
        因此保留一个替换点，避免钩子反向依赖 OneBot 服务。

        Args:
            runtime: 新的执行单元实例。
        """
        previous = self.runtime
        self.runtime = runtime
        if isinstance(previous, NullPluginRuntime):
            return
        asyncio.create_task(previous.close())

    async def dispatch_event(self, event: dict[str, Any], source: str) -> None:
        """
        把一条事件委托给插件执行单元。

        Args:
            event: 已完成机器人白名单与屏蔽校验的事件。
            source: 事件来源，用于日志与审计定位。
        """
        if not self.settings.plugin_enabled:
            return
        if str(event.get("post_type") or "") not in SUPPORTED_POST_TYPES:
            return
        bot_qq = str(event.get("_bot_qq") or event.get("self_id") or "")
        if not bot_qq:
            return
        async with self.database.session_factory() as session:
            installations = await PluginInstallService.enabled_installations(
                session, bot_qq
            )
        if not installations:
            return
        task = asyncio.create_task(
            self.runtime.handle_event(dict(event), installations, source)
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def close(self) -> None:
        """取消未完成的插件分发任务并释放执行单元。"""
        for task in tuple(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.runtime.close()

    async def wait_pending(self) -> None:
        """等待已派发的插件分发任务结束，供优雅退出与自动化测试使用。"""
        if not self._tasks:
            return
        await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
