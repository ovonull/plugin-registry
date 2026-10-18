"""
插件执行单元接口。

功能：
1. 把「插件代码跑在哪里、怎么跑」抽象成一个接口，事件主干只依赖本接口；
2. 提供空实现：未启用执行方式时只登记、不运行任何插件代码；
3. 按配置切换到独立子进程执行单元，事件主干、钩子与接口层无需改动；
4. 后续若要换成容器执行单元，沿用同一接口即可，对上层透明。

约定：
- 执行器不得直接发送消息或调用 OneBot 动作，所有能力必须经运行期闸门放行；
- 执行器失败不得影响主流程，异常由执行器内部兜底并记录日志。
"""

import logging
from typing import Any, Protocol

from app.common.config import Settings


logger = logging.getLogger("uvicorn.error")

# 只登记不执行
RUNTIME_NULL = "null"
# 由独立子进程执行插件代码
RUNTIME_SUBPROCESS = "subprocess"


class PluginRuntime(Protocol):
    """插件执行单元接口，所有实现都必须满足本协议。"""

    async def handle_event(
        self,
        event: dict[str, Any],
        installations: list[dict[str, Any]],
        source: str,
    ) -> None:
        """把一次事件分发给指定机器人上已启用的插件。"""

    async def close(self) -> None:
        """释放执行单元占用的资源。"""


class NullPluginRuntime:
    """尚未接入执行器时的占位实现，不加载也不执行任何插件代码。"""

    def __init__(self) -> None:
        """准备已提示过的插件集合，避免同一插件反复刷日志。"""
        self._notified: set[str] = set()

    async def handle_event(
        self,
        event: dict[str, Any],
        installations: list[dict[str, Any]],
        source: str,
    ) -> None:
        """记录一次未分发的启用插件，同一插件只提示一次。"""
        pending = [
            str(item.get("plugin_id") or "")
            for item in installations
            if item.get("plugin_id")
        ]
        fresh = [plugin_id for plugin_id in pending if plugin_id not in self._notified]
        if not fresh:
            return
        self._notified.update(fresh)
        logger.warning(
            "插件执行方式为 null，事件不执行插件代码，插件=%s，事件来源=%s",
            "、".join(fresh),
            source,
        )

    async def close(self) -> None:
        """空执行器无需释放资源。"""
        return None


def build_plugin_runtime(
    settings: Settings,
    database: Any = None,
    action_caller: Any = None,
) -> PluginRuntime:
    """
    构建插件执行单元。

    Args:
        settings: 应用配置。
        database: 数据库连接管理，子进程执行单元用它写审计。
        action_caller: OneBot 动作调用入口，子进程执行单元用它代调动作。
    Returns:
        配置为 subprocess 且依赖齐备时返回子进程执行单元，否则返回空实现。
    """
    mode = str(getattr(settings, "plugin_runtime", "") or "").strip().lower()
    if mode == RUNTIME_SUBPROCESS and database is not None and action_caller is not None:
        from app.services.plugin_runner import SubprocessPluginRuntime

        return SubprocessPluginRuntime(settings, database, action_caller)
    if mode and mode not in {RUNTIME_NULL, RUNTIME_SUBPROCESS}:
        logger.warning("未知的插件执行方式 %s，按 null 处理", mode)
    return NullPluginRuntime()
