"""
插件运行期能力闸门。

功能：
1. 插件提出的每个动作请求都在主进程重新校验：动作名反查所需能力，再比对本次安装实际拥有的能力；
2. 出网请求走独立通道，只放行清单声明且命中域名白名单的地址，其余一律拒绝；
3. 无论放行、拒绝、失败还是超时，都写一条只追加的审计记录。

约定：
- 闸门是插件与真实机器人之间唯一的通路，插件无法绕过它直接调用 OneBot；
- 拒绝时不给插件任何执行机会，调用方只拿到中文原因；
- 审计写库失败只记日志，绝不影响事件主流程。
"""

import asyncio
import json
import logging
import time
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

import httpx

from app.common.config import Settings
from app.common.database import Database
from app.services.plugin_action_log_service import (
    RESULT_DENIED,
    RESULT_ERROR,
    RESULT_OK,
    RESULT_TIMEOUT,
    PluginActionLogService,
)
from app.services.plugin_capability import capability_for_action
from app.services.plugin_gate import OUTBOUND_CAPABILITY, is_domain_allowed
from app.services.plugin_protocol import HTTP_REQUEST_ACTION


logger = logging.getLogger(__name__)

# 单次出网响应的文本上限，避免插件把整站内容搬回主进程。
MAX_RESPONSE_CHARS = 20000
# 插件可以按需申请的更高上限。部分站点的数据内嵌在整页 HTML 里（例如抖音分享页
# 的关键数据在第 3 万字符之后），20000 字符会把它截断，因此允许插件显式申请更大的
# 响应体，但仍然封顶，避免出现「想拿多大就拿多大」的口子。
MAX_RESPONSE_CHARS_LIMIT = 200000
# 出网只允许 http 与 https，其他协议直接拒绝。
ALLOWED_SCHEMES = frozenset({"http", "https"})
# 出网响应体的下载上限。只截断「返回给插件的文本」是不够的——响应体是先整个
# 解码进内存再截断的，白名单内的域名返回超大内容就能把主进程撑爆，
# 因此必须在读取的过程中就断掉，而不是读完再砍。
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
# 插件申请超时的默认值与封顶。解析失败一律按默认值，绝不让畸形参数把异常抛回调用方。
DEFAULT_REQUEST_TIMEOUT = 10.0
MAX_REQUEST_TIMEOUT = 30.0

ActionCaller = Callable[..., Awaitable[dict[str, Any]]]


def _text(value: Any) -> str:
    """把任意值安全转成字符串。"""
    return "" if value is None else str(value)


def _response_chars(value: Any) -> int:
    """
    计算本次出网响应允许返回的字符数。

    Args:
        value: 插件申请的 max_chars，缺失或非法时按默认上限处理。
    Returns:
        介于 1000 与 MAX_RESPONSE_CHARS_LIMIT 之间的字符数。
    """
    try:
        requested = int(float(str(value)))
    except (TypeError, ValueError):
        return MAX_RESPONSE_CHARS
    return max(1000, min(requested, MAX_RESPONSE_CHARS_LIMIT))


def _as_dict(value: Any) -> dict[str, Any]:
    """把插件传入的容器安全转成字典，非字典一律按空处理。"""
    return value if isinstance(value, dict) else {}


def _request_timeout(value: Any) -> float:
    """
    解析插件申请的单次请求超时。
    """
    try:
        requested = float(value)
    except (TypeError, ValueError):
        return DEFAULT_REQUEST_TIMEOUT
    if requested != requested or requested <= 0:  # NaN 或非正数
        return DEFAULT_REQUEST_TIMEOUT
    return min(requested, MAX_REQUEST_TIMEOUT)


async def _read_limited(response: httpx.Response) -> bytes:
    """
    流式读取响应体并限制体积，读满上限立即中断连接。
    """
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        remaining = MAX_RESPONSE_BYTES - total
        if len(chunk) >= remaining:
            chunks.append(chunk[:remaining])
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


class RuntimeGate:
    """插件动作请求的校验与代调执行器。"""

    def __init__(
        self,
        settings: Settings,
        database: Database,
        action_caller: ActionCaller,
    ) -> None:
        """
        记录配置、数据库与 OneBot 动作调用入口。

        Args:
            settings: 应用配置。
            database: 数据库连接管理，用于写审计。
            action_caller: OneBot 动作调用函数，签名与 OneBotService.call_action 一致。
        """
        self.settings = settings
        self.database = database
        self.action_caller = action_caller
        self._http_client: httpx.AsyncClient | None = None

    def _client(self) -> httpx.AsyncClient:
        """惰性创建出网客户端，未使用出网能力时不建立连接池。"""
        if self._http_client is None:
            # 这里同时封顶，让「单次请求上限 30 秒」对插件不传 timeout 的默认
            # 请求也成立，而不是只约束插件显式传入的那个值。
            self._http_client = httpx.AsyncClient(
                timeout=min(self.settings.plugin_http_timeout, MAX_REQUEST_TIMEOUT)
            )
        return self._http_client

    async def execute(
        self,
        installation: dict[str, Any],
        action: str,
        params: dict[str, Any],
        source: str = "",
    ) -> tuple[bool, dict[str, Any], str]:
        """
        校验并执行一个动作请求。

        Args:
            installation: 运行期安装信息，含已批准能力与域名白名单。
            action: 动作名，或出网请求的虚拟动作名。
            params: 动作参数。
            source: 事件来源，仅用于日志。
        Returns:
            (是否成功, 返回数据, 错误原因)。
        """
        if action == HTTP_REQUEST_ACTION:
            return await self._handle_http(installation, params, source)
        capability = capability_for_action(action)
        if not capability:
            reason = f"动作 {action} 未纳入插件能力体系，已拒绝"
            await self._audit(
                installation, action, "", params, RESULT_DENIED, reason, 0
            )
            return False, {}, reason
        granted = [str(item) for item in installation.get("granted_capabilities") or []]
        if capability not in granted:
            reason = f"未获得能力 {capability}，已拒绝执行 {action}"
            await self._audit(
                installation, action, capability, params, RESULT_DENIED, reason, 0
            )
            return False, {}, reason

        started = time.perf_counter()
        try:
            result = await self.action_caller(
                action, params, self_id=_text(installation.get("bot_qq"))
            )
        except asyncio.TimeoutError:
            elapsed = int((time.perf_counter() - started) * 1000)
            reason = f"动作 {action} 调用超时"
            await self._audit(
                installation, action, capability, params, RESULT_TIMEOUT, reason, elapsed
            )
            return False, {}, reason
        except Exception as error:  # OneBot 侧异常不能穿透到事件主流程
            elapsed = int((time.perf_counter() - started) * 1000)
            reason = f"动作 {action} 执行失败：{error}"
            logger.warning("插件动作执行失败，插件=%s，动作=%s，错误=%s", installation.get("plugin_id"), action, error)
            await self._audit(
                installation, action, capability, params, RESULT_ERROR, reason, elapsed
            )
            return False, {}, reason
        elapsed = int((time.perf_counter() - started) * 1000)
        if not isinstance(result, dict):
            result = {"data": result}
        ok = result.get("status") in (None, "ok", "async") and not result.get("retcode")
        message = "" if ok else _text(result.get("message") or result.get("wording"))
        await self._audit(
            installation,
            action,
            capability,
            params,
            RESULT_OK if ok else RESULT_ERROR,
            message,
            elapsed,
        )
        return ok, result, message

    async def _handle_http(
        self, installation: dict[str, Any], params: dict[str, Any], source: str
    ) -> tuple[bool, dict[str, Any], str]:
        """校验域名白名单后代理一次出网请求。"""
        granted = [str(item) for item in installation.get("granted_capabilities") or []]
        if OUTBOUND_CAPABILITY not in granted:
            reason = "未获得访问外网能力，已拒绝该请求"
            await self._audit(
                installation, HTTP_REQUEST_ACTION, OUTBOUND_CAPABILITY, params,
                RESULT_DENIED, reason, 0,
            )
            return False, {}, reason
        url = _text(params.get("url"))
        parsed = urlparse(url)
        if parsed.scheme not in ALLOWED_SCHEMES or not parsed.hostname:
            reason = f"出网地址不合法：{url}"
            await self._audit(
                installation, HTTP_REQUEST_ACTION, OUTBOUND_CAPABILITY, params,
                RESULT_DENIED, reason, 0,
            )
            return False, {}, reason
        domains = [str(item) for item in installation.get("allowed_domains") or []]
        if not is_domain_allowed(parsed.hostname, domains):
            reason = f"域名 {parsed.hostname} 不在白名单内，已拒绝该请求"
            await self._audit(
                installation, HTTP_REQUEST_ACTION, OUTBOUND_CAPABILITY, params,
                RESULT_DENIED, reason, 0,
            )
            return False, {}, reason

        method = _text(params.get("method") or "GET").upper()
        timeout = _request_timeout(params.get("timeout"))
        headers = _as_dict(params.get("headers"))
        query = _as_dict(params.get("query"))
        started = time.perf_counter()
        try:
            async with self._client().stream(
                method,
                url,
                headers=headers,
                params=query,
                json=params.get("json"),
                timeout=timeout,
            ) as response:
                body = await _read_limited(response)
                status_code = response.status_code
                response_headers = dict(response.headers)
                content_type = response_headers.get("content-type", "")
                encoding = response.charset_encoding or "utf-8"
        except Exception as error:
            elapsed = int((time.perf_counter() - started) * 1000)
            reason = f"出网请求失败：{error}"
            await self._audit(
                installation, HTTP_REQUEST_ACTION, OUTBOUND_CAPABILITY, params,
                RESULT_ERROR, reason, elapsed,
            )
            return False, {}, reason
        elapsed = int((time.perf_counter() - started) * 1000)
        text = body.decode(encoding, errors="replace")[: _response_chars(params.get("max_chars"))]
        payload: dict[str, Any] = {
            "status": status_code,
            "headers": response_headers,
            "text": text,
        }
        if "application/json" in content_type:
            try:
                # 从完整响应体解析，不用已截断的 text，否则大 JSON 会解析失败。
                payload["json"] = json.loads(body.decode(encoding, errors="replace"))
            except ValueError:
                payload["json"] = None
        ok = status_code < 400
        await self._audit(
            installation, HTTP_REQUEST_ACTION, OUTBOUND_CAPABILITY, params,
            RESULT_OK if ok else RESULT_ERROR,
            "" if ok else f"HTTP {status_code}", elapsed, source,
        )
        return ok, payload, "" if ok else f"HTTP {status_code}"

    async def _audit(
        self,
        installation: dict[str, Any],
        action: str,
        capability: str,
        params: dict[str, Any],
        result_code: str,
        message: str,
        duration_ms: int,
        source: str = "",
    ) -> None:
        """写一条审计记录，任何失败都只落日志。"""
        try:
            async with self.database.session_factory() as session:
                await PluginActionLogService.append(
                    session,
                    plugin_id=_text(installation.get("plugin_id")),
                    version_id=installation.get("version_id"),
                    owner_id=installation.get("owner_id"),
                    bot_qq=_text(installation.get("bot_qq")),
                    group_id=_text(params.get("group_id")),
                    user_id=_text(params.get("user_id")),
                    action_name=action,
                    capability=capability,
                    params=params,
                    result_code=result_code,
                    result_message=message,
                    duration_ms=duration_ms,
                )
                await session.commit()
        except Exception as error:
            logger.warning(
                "插件审计写入失败，插件=%s，动作=%s，来源=%s，错误=%s",
                installation.get("plugin_id"),
                action,
                source,
                error,
            )

    async def close(self) -> None:
        """释放出网连接池。"""
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None
