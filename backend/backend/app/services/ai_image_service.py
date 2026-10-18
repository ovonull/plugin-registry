"""OneBot 图片提取、下载和多模态 AI 输入准备服务。"""

import asyncio
import base64
import binascii
import hashlib
import html
import logging
import mimetypes
import re
from pathlib import Path
from typing import Any, TypedDict
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx


logger = logging.getLogger("uvicorn.error")
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_COUNT = 4
MAX_PUBLIC_IMAGE_RETRIES = 3
IMAGE_RETRY_BACKOFF_SECONDS = 0.5
IMAGE_RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})
CQ_IMAGE_PATTERN = re.compile(
    r"\[CQ:image(?:,((?:[^\]]|\](?=,\s*[A-Za-z_][\w-]*=))*))?\]",
    re.IGNORECASE,
)


class AiImage(TypedDict):
    """供 AI 平台适配器使用的图片数据。"""

    media_type: str
    data: str


class ImageCandidate(TypedDict):
    """OneBot 图片候选来源及其去重信息。"""

    path: str
    url: str
    media_type: str
    name: str
    identity: str
    summary: str
    base64_data: str


class AiImageService:
    """从 OneBot 事件中提取图片并转换为 Base64。"""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self.http_client = http_client

    @staticmethod
    def has_candidates(event: dict[str, Any]) -> bool:
        """判断事件是否声明了图片来源，不执行文件读取或网络下载。"""
        return bool(AiImageService.candidates(event))

    @staticmethod
    def candidates(event: dict[str, Any]) -> list[ImageCandidate]:
        """提取事件中的去重图片候选，供缓存复用和多模态处理使用。"""
        candidates = _image_candidates(event)
        unique: list[ImageCandidate] = []
        by_identity: dict[str, ImageCandidate] = {}
        for candidate in candidates:
            identity = (
                candidate.get("identity")
                or candidate.get("path")
                or candidate.get("url")
                or candidate.get("name")
            )
            existing = by_identity.get(identity)
            if existing is None:
                by_identity[identity] = candidate
                unique.append(candidate)
                continue
            for key in (
                "path",
                "url",
                "media_type",
                "name",
                "summary",
                "base64_data",
            ):
                if not existing.get(key) and candidate.get(key):
                    existing[key] = candidate[key]
        return unique

    @staticmethod
    def animated_candidates(event: dict[str, Any]) -> list[ImageCandidate]:
        """提取摘要标记为动画表情的图片，并按图片身份去重。"""
        candidates: list[ImageCandidate] = []
        by_identity: dict[str, ImageCandidate] = {}
        for candidate in AiImageService.candidates(event):
            summary = html.unescape(candidate.get("summary", ""))
            if "动画表情" not in summary:
                continue
            identity = candidate.get("identity") or (
                candidate.get("path") or candidate.get("url") or candidate.get("name", "")
            )
            existing = by_identity.get(identity)
            if existing is not None:
                for key in (
                    "path",
                    "url",
                    "media_type",
                    "name",
                    "summary",
                    "base64_data",
                ):
                    if not existing.get(key) and candidate.get(key):
                        existing[key] = candidate[key]
                continue
            by_identity[identity] = candidate
            candidates.append(candidate)
        return candidates

    async def collect(
        self,
        event: dict[str, Any],
        http_url: str = "",
        http_token: str = "",
    ) -> list[AiImage]:
        """读取事件中的图片，失败的单张图片跳过并保留文字回复。"""
        images: list[AiImage] = []
        loaded_identities: set[str] = set()
        for candidate in AiImageService.candidates(event):
            if len(images) >= MAX_IMAGE_COUNT:
                break
            identity = candidate.get("identity", "")
            if identity and identity in loaded_identities:
                continue
            image = await self._load_candidate(candidate, http_url, http_token)
            if image:
                images.append(image)
                if identity:
                    loaded_identities.add(identity)
        return images

    async def load_candidate(
        self,
        candidate: dict[str, str],
        http_url: str = "",
        http_token: str = "",
    ) -> AiImage | None:
        """加载单个图片候选并转换为 Base64，供入库和分类任务复用。"""
        return await self._load_candidate(candidate, http_url, http_token)

    async def _load_candidate(
        self,
        candidate: dict[str, str],
        http_url: str,
        http_token: str,
    ) -> AiImage | None:
        media_type = _media_type(candidate)
        inline_data = str(candidate.get("base64_data") or "").strip()
        if inline_data:
            try:
                return _encode_image(_decode_inline_base64(inline_data), media_type)
            except (binascii.Error, ValueError) as exc:
                logger.warning("AI 图片内嵌 Base64 读取失败，错误=%s", exc)
                return None
        local_path = candidate.get("path", "")
        if local_path:
            try:
                path = Path(local_path)
                if path.is_file() and path.stat().st_size <= MAX_IMAGE_BYTES:
                    return _encode_image(path.read_bytes(), media_type)
            except OSError as exc:
                logger.warning("AI 图片本地文件读取失败，path=%s，错误=%s", local_path, exc)
        url = candidate.get("url", "")
        if not url:
            return None
        is_public_url = url.lower().startswith(("http://", "https://"))
        if not is_public_url and not http_url:
            return None
        if not is_public_url:
            url = urljoin(f"{http_url.rstrip('/')}/", url.lstrip("/"))
        headers = {"Authorization": f"Bearer {http_token}"} if http_token else {}
        max_retries = MAX_PUBLIC_IMAGE_RETRIES if is_public_url else 0
        for attempt in range(max_retries + 1):
            try:
                response = await self.http_client.get(url, headers=headers)
                if (
                    response.status_code in IMAGE_RETRYABLE_STATUS_CODES
                    and attempt < max_retries
                ):
                    await self._wait_before_retry(url, attempt, response.text)
                    continue
                response.raise_for_status()
                if len(response.content) > MAX_IMAGE_BYTES:
                    logger.warning("AI 图片超过大小限制，url=%s", url)
                    return None
                response_type = response.headers.get("content-type", "").split(";", 1)[0]
                if response_type.startswith("image/"):
                    media_type = response_type
                return _encode_image(response.content, media_type)
            except httpx.HTTPStatusError as exc:
                if (
                    is_public_url
                    and exc.response.status_code in IMAGE_RETRYABLE_STATUS_CODES
                    and attempt < max_retries
                ):
                    await self._wait_before_retry(url, attempt, str(exc))
                    continue
                logger.warning("AI 图片下载失败，url=%s，错误=%s", url, exc)
                return None
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if is_public_url and attempt < max_retries:
                    await self._wait_before_retry(url, attempt, str(exc))
                    continue
                logger.warning("AI 图片下载失败，url=%s，错误=%s", url, exc)
                return None
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning("AI 图片下载失败，url=%s，错误=%s", url, exc)
                return None
        return None

    @staticmethod
    async def _wait_before_retry(url: str, attempt: int, detail: str) -> None:
        """记录公网图片重试并按递增间隔等待。"""
        retry_number = attempt + 1
        logger.warning(
            "AI 公网图片下载失败，将进行第 %s/%s 次重试，url=%s，错误=%s",
            retry_number,
            MAX_PUBLIC_IMAGE_RETRIES,
            url,
            detail or "无附加错误信息",
        )
        await asyncio.sleep(IMAGE_RETRY_BACKOFF_SECONDS * retry_number)


def _image_candidates(event: dict[str, Any]) -> list[ImageCandidate]:
    """递归提取消息段、CQ 文本和 NapCat raw 结构中的图片来源。"""
    candidates: list[ImageCandidate] = []
    seen: set[tuple[str, str, str]] = set()

    def add(
        path: Any = "",
        url: Any = "",
        media_type: Any = "",
        name: Any = "",
        summary: Any = "",
        base64_data: Any = "",
    ) -> None:
        path_text = str(path or "")
        url_text = str(url or "")
        media_type_text = str(media_type or "")
        name_text = str(name or "")
        summary_text = str(summary or "")
        inline_data = str(base64_data or "").strip()
        # OneBot 通常把内嵌图片放在 file=base64://...，也兼容 data:image/... URL。
        for source_name, source_value in (
            ("inline", inline_data),
            ("name", name_text),
            ("url", url_text),
            ("path", path_text),
        ):
            payload, inline_media_type = _parse_inline_base64(source_value)
            if not payload:
                continue
            inline_data = payload
            if inline_media_type and not media_type_text:
                media_type_text = inline_media_type
            if source_name == "name":
                name_text = ""
            elif source_name == "url":
                url_text = ""
            elif source_name == "path":
                path_text = ""
            break
        if not path_text and not url_text and not inline_data:
            return
        identity = (path_text, url_text, inline_data)
        if identity in seen:
            return
        seen.add(identity)
        candidates.append(
            {
                "path": path_text,
                "url": url_text,
                "media_type": media_type_text,
                "name": name_text,
                "identity": _candidate_identity(
                    path_text, url_text, name_text, inline_data
                ),
                "summary": summary_text,
                "base64_data": inline_data,
            }
        )

    def walk(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                walk(item)
            return
        if not isinstance(value, dict):
            return
        if value.get("type") == "image":
            raw_data = value.get("data")
            data = raw_data if isinstance(raw_data, dict) else value
            nested_data = data.get("data")
            inline_data = (
                raw_data
                if isinstance(raw_data, str)
                else data.get("base64")
                or data.get("base64_data")
                or (nested_data if isinstance(nested_data, str) else "")
            )
            add(
                data.get("path"),
                data.get("url"),
                data.get("mime") or data.get("content_type"),
                data.get("file") or data.get("file_name"),
                data.get("summary"),
                inline_data,
            )
        pic = value.get("picElement")
        if isinstance(pic, dict):
            add(
                pic.get("sourcePath"),
                pic.get("originImageUrl") or pic.get("url"),
                pic.get("mimeType") or pic.get("contentType"),
                pic.get("fileName") or pic.get("md5HexStr"),
                pic.get("summary"),
                pic.get("base64") or pic.get("base64Data"),
            )
        for item in value.values():
            walk(item)

    walk(event)
    # message_format=string 时，OneBot 只提供 CQ 码文本，补充解析其中的图片段。
    for field in (event.get("message"), event.get("raw_message")):
        if not isinstance(field, str):
            continue
        for match in CQ_IMAGE_PATTERN.finditer(field):
            params = _parse_cq_params(match.group(1) or "")
            add(
                params.get("path", ""),
                params.get("url", ""),
                params.get("mime", "") or params.get("content_type", ""),
                params.get("file", "") or params.get("file_name", ""),
                params.get("summary", ""),
                params.get("base64", "") or params.get("base64_data", ""),
            )
    return candidates


def _parse_cq_params(value: str) -> dict[str, str]:
    """解析 CQ 图片段参数，并还原 URL 编码值。"""
    params: dict[str, str] = {}
    for item in value.split(","):
        key, separator, raw_value = item.partition("=")
        if not separator:
            continue
        params[key.strip().lower()] = html.unescape(unquote(raw_value.strip()))
    return params


def _media_type(candidate: dict[str, str]) -> str:
    """根据事件元数据或文件扩展名确定图片 MIME 类型。"""
    value = candidate.get("media_type", "")
    if value.startswith("image/"):
        return value
    guessed = mimetypes.guess_type(
        candidate.get("name")
        or candidate.get("path")
        or candidate.get("url", "")
    )[0]
    return guessed if guessed and guessed.startswith("image/") else "image/jpeg"


def _candidate_identity(path: str, url: str, name: str, base64_data: str = "") -> str:
    """提取同一图片不同来源之间可复用的稳定标识。"""
    if name:
        return f"name:{name.lower()}"
    if path:
        return f"name:{Path(path).name.lower()}"
    file_id = parse_qs(urlparse(url).query).get("fileid", [""])[0]
    if file_id:
        return f"fileid:{file_id}"
    if base64_data:
        digest = hashlib.sha1(base64_data.encode("utf-8")).hexdigest()
        return f"base64:{digest}"
    return ""


def _parse_inline_base64(value: str) -> tuple[str, str]:
    """识别 OneBot base64:// 或 data:image/...;base64,... 图片引用。"""
    text = str(value or "").strip()
    if text.lower().startswith("base64://"):
        return text[len("base64://") :].strip(), ""
    if not text.lower().startswith("data:") or "," not in text:
        return "", ""
    header, payload = text.split(",", 1)
    if ";base64" not in header.lower():
        return "", ""
    media_type = header[5:].split(";", 1)[0].strip().lower()
    return payload.strip(), media_type if media_type.startswith("image/") else ""


def _decode_inline_base64(value: str) -> bytes:
    """校验并解码内嵌 Base64，拒绝超出单图限制的数据。"""
    payload, _ = _parse_inline_base64(value)
    if not payload:
        # 候选内部已保存去掉前缀的纯 Base64 字符串。
        payload = str(value or "").strip()
    if not payload:
        raise ValueError("Base64 图片内容为空")
    payload = re.sub(r"\s+", "", unquote(payload))
    if len(payload) > (MAX_IMAGE_BYTES * 4 // 3 + 4):
        raise ValueError("Base64 图片超过大小限制")
    try:
        content = base64.b64decode(
            payload + "=" * (-len(payload) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Base64 图片内容无效") from exc
    if len(content) > MAX_IMAGE_BYTES:
        raise ValueError("Base64 图片超过大小限制")
    return content


def _encode_image(content: bytes, media_type: str) -> AiImage:
    """将图片二进制转换为平台无关的 Base64 数据。"""
    return {"media_type": media_type, "data": base64.b64encode(content).decode("ascii")}
