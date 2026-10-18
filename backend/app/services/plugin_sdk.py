"""
插件 SDK：插件代码在子进程内使用的能力入口。

功能：
1. 提供 host 对象：读安装配置、写日志、请求动作、发起白名单内的 HTTP 请求；
2. 提供 on_message / on_notice / on_request / on_event 装饰器，把处理函数登记到当前加载的插件；
3. 动作与出网请求一律转成报文交给主进程，由主进程的能力闸门决定放行或拒绝。

约定：
- 插件不直接调用 OneBot，绕过 host 的调用不会被放行，也不会留下审计记录；
- 子进程内不持有任何凭据，令牌与数据库连接始终留在主进程；
- 处理函数签名推荐 ``async def handle(event, host)``，第二个参数是绑定到本插件的视图，
  比共享的模块级 ``host`` 更安全（模块级 host 每次事件都会切换上下文）。
"""

import asyncio
import json
import sys
import threading
import uuid
from typing import Any, Awaitable, Callable

from app.services.plugin_protocol import (
    HTTP_REQUEST_ACTION,
    build_action_request,
    build_log,
    encode_message,
)


# 等待主进程返回动作结果的兜底超时，主进程侧另有事件级超时。
ACTION_TIMEOUT = 30.0

# 处理函数类型
Handler = Callable[..., Awaitable[Any]]

_write_lock = threading.Lock()
_pending: dict[str, asyncio.Future] = {}
_handlers: dict[str, dict[str, list[Handler]]] = {}
_loading_plugin = ""
_output: Any = None


class PluginCallError(Exception):
    """动作被拒绝或执行失败时抛出，插件可捕获后自行降级。"""


def set_output(stream: Any) -> None:
    """指定协议报文的输出流，插件自己 print 的内容不会污染这条通道。"""
    global _output
    _output = stream


def emit(payload: dict[str, Any]) -> None:
    """把一帧报文写到协议输出流，写入失败时静默丢弃，避免插件进程崩溃。"""
    data = encode_message(payload)
    stream = _output if _output is not None else getattr(sys.stdout, "buffer", None)
    if stream is None:
        return None
    try:
        with _write_lock:
            stream.write(data)
            stream.flush()
    except (BrokenPipeError, ValueError, OSError):
        return None


def begin_loading(plugin_id: str) -> None:
    """标记当前正在加载的插件，装饰器会把处理函数登记到它名下。"""
    global _loading_plugin
    _loading_plugin = plugin_id


def end_loading() -> None:
    """结束加载标记。"""
    global _loading_plugin
    _loading_plugin = ""


def _register(kind: str) -> Callable[[Handler], Handler]:
    """生成某一类事件的注册装饰器。"""

    def decorator(func: Handler) -> Handler:
        owner = _loading_plugin
        if not owner:
            raise RuntimeError("插件处理函数必须在加载阶段注册")
        _handlers.setdefault(owner, {}).setdefault(kind, []).append(func)
        return func

    return decorator


on_message = _register("message")
on_notice = _register("notice")
on_request = _register("request")
on_event = _register("event")


def reset_plugin(plugin_id: str) -> None:
    """
    清空某个插件已注册的处理函数。

    插件升级后包目录会变，子进程需要重新加载入口文件。旧版本注册的处理函数
    必须先清掉，否则同一个事件会被新旧两份处理函数各执行一次（表现为回两遍）。
    """
    _handlers.pop(plugin_id, None)


def handlers_for(plugin_id: str, event: dict[str, Any]) -> list[Handler]:
    """返回某插件对本次事件感兴趣的处理函数，按声明顺序排列。"""
    registered = _handlers.get(plugin_id) or {}
    post_type = str(event.get("post_type") or "")
    result: list[Handler] = []
    for kind in (post_type, "event"):
        result.extend(registered.get(kind) or [])
    return result


def resolve_action(action_id: str, ok: bool, data: dict[str, Any], error: str) -> None:
    """主进程返回动作结果时唤醒等待中的调用。"""
    future = _pending.get(action_id)
    if future is None or future.done():
        return
    if ok:
        future.set_result(data or {})
    else:
        future.set_exception(PluginCallError(error or "动作执行失败"))


class PluginHost:
    """绑定到某个插件安装实例的能力视图。"""

    def __init__(
        self,
        plugin_id: str,
        bot_qq: str,
        config: dict[str, Any],
        granted: list[str],
        domains: list[str],
    ) -> None:
        """记录本次事件的安装上下文。"""
        self.plugin_id = plugin_id
        self.bot_qq = bot_qq
        self.config = config
        self.granted = granted
        self.domains = domains

    def has(self, capability: str) -> bool:
        """判断本插件在当前机器人上是否具备某项能力。"""
        return capability in self.granted

    def log(self, message: Any, level: str = "info") -> None:
        """写一条插件日志，由主进程落到统一日志文件。"""
        emit(build_log(self.plugin_id, str(message), level))

    async def action(self, name: str, params: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        """
        请求主进程代调一个 OneBot 动作。

        Args:
            name: OneBot 动作名，例如 send_group_msg。
            params: 参数表，与 kwargs 合并，kwargs 优先。
        Returns:
            动作返回的数据。
        Raises:
            PluginCallError: 能力未批准、参数非法或调用失败。
        """
        payload = dict(params or {})
        payload.update(kwargs)
        action_id = uuid.uuid4().hex
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        _pending[action_id] = future
        emit(build_action_request(action_id, self.plugin_id, str(name), payload))
        try:
            return await asyncio.wait_for(future, timeout=ACTION_TIMEOUT)
        except asyncio.TimeoutError as error:
            raise PluginCallError("等待动作结果超时") from error
        finally:
            _pending.pop(action_id, None)

    async def send_group_msg(
        self, group_id: Any, message: Any, auto_escape: bool = False
    ) -> dict[str, Any]:
        """发送群消息。"""
        return await self.action(
            "send_group_msg",
            {
                "group_id": str(group_id),
                "message": message,
                "auto_escape": bool(auto_escape),
            },
        )

    async def send_private_msg(
        self, user_id: Any, message: Any, auto_escape: bool = False
    ) -> dict[str, Any]:
        """发送私聊消息。"""
        return await self.action(
            "send_private_msg",
            {
                "user_id": str(user_id),
                "message": message,
                "auto_escape": bool(auto_escape),
            },
        )

    async def delete_msg(self, message_id: Any) -> dict[str, Any]:
        """撤回消息。"""
        return await self.action("delete_msg", {"message_id": message_id})

    async def http_request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: Any = None,
        query: dict[str, Any] | None = None,
        timeout: float = 10.0,
        max_chars: int | None = None,
    ) -> dict[str, Any]:
        """
        发起一次出网请求，域名必须在清单声明的白名单内。

        Args:
            method: HTTP 方法，如 GET 或 POST。
            url: 完整地址，主进程会解析域名并比对白名单。
            headers: 请求头。
            json_body: 以 JSON 发送的请求体。
            query: 查询参数。
            timeout: 超时秒数，主进程上限为 30 秒。
            max_chars: 期望拿到的响应字符数。默认 20000，最大 200000；
                数据内嵌在整页 HTML 里时（例如分享页）需要显式调大。
        Returns:
            含 status、headers、text、json 的字典。
        Raises:
            PluginCallError: 未声明出网能力、域名不在白名单或请求失败。
        """
        params = {
            "method": str(method or "GET").upper(),
            "url": str(url),
            "headers": headers or {},
            "query": query or {},
            "timeout": min(float(timeout or 10.0), 30.0),
        }
        if json_body is not None:
            params["json"] = json_body
        if max_chars:
            params["max_chars"] = int(max_chars)
        return await self.action(HTTP_REQUEST_ACTION, params)


class HostProxy:
    """模块级 host 代理，始终指向当前正在分发事件的插件视图。"""

    def __init__(self) -> None:
        """初始化空上下文。"""
        self._current: PluginHost | None = None

    def bind(self, value: PluginHost | None) -> None:
        """切换当前上下文。"""
        self._current = value

    def _view(self) -> PluginHost:
        """返回当前上下文，未绑定时抛出明确错误。"""
        if self._current is None:
            raise PluginCallError("当前没有正在处理的插件事件，不能调用 host")
        return self._current

    @property
    def plugin_id(self) -> str:
        """当前插件标识。"""
        return self._view().plugin_id

    @property
    def bot_qq(self) -> str:
        """当前机器人 QQ。"""
        return self._view().bot_qq

    @property
    def config(self) -> dict[str, Any]:
        """当前安装的配置。"""
        return self._view().config

    @property
    def granted(self) -> list[str]:
        """当前安装已批准的能力。"""
        return self._view().granted

    def has(self, capability: str) -> bool:
        """代理到当前视图的能力判断。"""
        return self._view().has(capability)

    def log(self, message: Any, level: str = "info") -> None:
        """代理到当前视图的日志写入。"""
        self._view().log(message, level)

    async def action(self, name: str, params: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        """代理到当前视图的动作请求。"""
        return await self._view().action(name, params, **kwargs)

    async def http_request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        """代理到当前视图的出网请求。"""
        return await self._view().http_request(method, url, **kwargs)


host = HostProxy()


def dumps(value: Any) -> str:
    """把任意值转成便于写日志的紧凑 JSON。"""
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return repr(value)
