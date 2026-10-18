"""
主进程与插件子进程之间的报文协议。

功能：
1. 定义两个方向的报文：主进程下发事件 / 动作结果 / 退出指令，子进程上报就绪 / 日志 / 动作请求 / 事件完成；
2. 报文统一为单行 JSON，UTF-8 编码，以换行符分隔，避免长度前缀与粘包处理；
3. 解析函数对非法内容一律返回空值，绝不抛异常——子进程的输出属于不可信输入。

约定：
- 报文不加密，进程边界由操作系统保证，主进程只信任自己拉起的子进程；
- 插件代码可以伪造任意报文，因此每个动作请求都必须在主进程重新校验能力与白名单；
- 单行报文的长度上限为 MAX_LINE_BYTES，超长即视为非法，避免子进程用巨行拖垮主进程。
"""

import json
from typing import Any


# 主进程 → 子进程
TYPE_EVENT = "event"
TYPE_ACTION_RESULT = "action_result"
TYPE_SHUTDOWN = "shutdown"
# 子进程 → 主进程
TYPE_READY = "ready"
TYPE_LOG = "log"
TYPE_ACTION = "action"
TYPE_DONE = "done"

# 单行报文上限，超出即丢弃，防止子进程用超长行占用主进程内存。
MAX_LINE_BYTES = 4 * 1024 * 1024

# 出网请求的虚拟动作名，与 OneBot 动作名区分开，避免与真实动作混淆。
HTTP_REQUEST_ACTION = "http.request"


def encode_message(payload: dict[str, Any]) -> bytes:
    """
    把报文编码成可直接写入管道的字节行。

    Args:
        payload: 报文内容，必须可 JSON 序列化。
    Returns:
        UTF-8 字节行，以换行符结尾。
    """
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return (text + "\n").encode("utf-8")


def decode_message(line: bytes | str) -> dict[str, Any] | None:
    """
    解析一行报文。

    Args:
        line: 从管道读到的原始行。
    Returns:
        解析成功时返回报文内容，内容不是 JSON 对象或超长时返回空值。
    """
    if isinstance(line, bytes):
        if len(line) > MAX_LINE_BYTES:
            return None
        try:
            text = line.decode("utf-8")
        except UnicodeDecodeError:
            return None
    else:
        text = line
    text = text.strip()
    if not text or len(text.encode("utf-8")) > MAX_LINE_BYTES:
        return None
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def build_event_message(
    event_id: str,
    installation: dict[str, Any],
    event: dict[str, Any],
    source: str,
) -> dict[str, Any]:
    """
    构造下发事件报文。

    Args:
        event_id: 本次事件的处理编号，子进程完成后原样回传。
        installation: 运行期安装信息，含插件包目录、配置与已批准能力。
        event: OneBot 事件内容。
        source: 事件来源，用于插件侧日志定位。
    Returns:
        事件报文。
    """
    return {
        "t": TYPE_EVENT,
        "id": event_id,
        "source": source,
        "plugin_id": str(installation.get("plugin_id") or ""),
        "installation_id": installation.get("installation_id"),
        "bot_qq": str(installation.get("bot_qq") or ""),
        "entry": str(installation.get("entry") or ""),
        "package_dir": str(installation.get("package_dir") or ""),
        "granted": list(installation.get("granted_capabilities") or []),
        "domains": list(installation.get("allowed_domains") or []),
        "config": dict(installation.get("config") or {}),
        "event": event,
    }


def build_action_result(
    action_id: str,
    ok: bool,
    data: dict[str, Any] | None = None,
    error: str = "",
) -> dict[str, Any]:
    """构造动作执行结果报文，回给子进程里等待中的 host 调用。"""
    return {
        "t": TYPE_ACTION_RESULT,
        "id": action_id,
        "ok": bool(ok),
        "data": data or {},
        "error": error,
    }


def build_action_request(
    action_id: str,
    plugin_id: str,
    action: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    """构造子进程发往主进程的动作请求报文。"""
    return {
        "t": TYPE_ACTION,
        "id": action_id,
        "plugin_id": plugin_id,
        "action": action,
        "params": params,
    }


def build_done(
    event_id: str, ok: bool = True, error: str = ""
) -> dict[str, Any]:
    """构造事件处理完成报文。"""
    return {"t": TYPE_DONE, "id": event_id, "ok": bool(ok), "error": error}


def build_log(plugin_id: str, message: str, level: str = "info") -> dict[str, Any]:
    """构造日志报文，由主进程写进统一日志文件。"""
    return {
        "t": TYPE_LOG,
        "plugin_id": plugin_id,
        "level": level,
        "message": message[:2000],
    }


def build_ready() -> dict[str, Any]:
    """构造子进程就绪报文。"""
    return {"t": TYPE_READY}


def build_shutdown() -> dict[str, Any]:
    """构造退出指令，子进程收到后立即结束。"""
    return {"t": TYPE_SHUTDOWN}


def message_type(payload: dict[str, Any]) -> str:
    """返回报文类型，未声明类型时返回空字符串。"""
    return str(payload.get("t") or "")
