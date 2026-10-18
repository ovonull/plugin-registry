"""
HTTP 请求公共工具。

功能：
1. 统一提取客户端真实 IP，供登录日志、验证码记录等审计场景复用；
2. 优先使用反向代理透传的 X-Forwarded-For，避免记录到代理地址。
"""

from fastapi import Request


# 客户端 IP 字段长度上限，与数据库列长度保持一致。
CLIENT_IP_MAX_LENGTH = 64


def extract_client_ip(request: Request) -> str | None:
    """
    提取当前请求的客户端 IP。

    取值优先级：X-Forwarded-For 的第一个地址 > X-Real-IP > 直连对端地址。

    Args:
        request: 当前 HTTP 请求。
    Returns:
        客户端 IP 字符串，取不到时返回 None。
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        first = forwarded.split(",", 1)[0].strip()
        if first:
            return first[:CLIENT_IP_MAX_LENGTH]
    real_ip = (request.headers.get("x-real-ip", "") or "").strip()
    if real_ip:
        return real_ip[:CLIENT_IP_MAX_LENGTH]
    if request.client and request.client.host:
        return request.client.host[:CLIENT_IP_MAX_LENGTH]
    return None