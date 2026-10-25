"""
插件清单（manifest）严格校验。

功能：
1. 对提交的清单做严格字段校验，出现未知字段直接拒收，避免夹带未审内容；
2. 校验能力声明的结构（非空、不重复）；不校验是否在本地白名单内——
   宿主由外部维护、能力会持续扩展，未登记的能力放行并记日志；
3. 校验入口文件、依赖声明与配置项结构，输出可直接入库的归一化清单；
4. 出网域名白名单与能力批准的判断复用 plugin_gate，保证审核与运行期依据一致。
"""

import logging
import re
from typing import Any

from app.common.responses import BusinessError
from app.services.plugin_capability import CAPABILITY_CODE_SET
from app.services.plugin_gate import validate_allowed_domains

logger = logging.getLogger("uvicorn.error")


MANIFEST_VERSION = 1

# 清单字段校验失败
CODE_MANIFEST_INVALID = 40090
# 能力声明校验失败
CODE_CAPABILITY_INVALID = 40091
# 版本号格式校验失败
CODE_VERSION_INVALID = 40093

ALLOWED_FIELDS: frozenset[str] = frozenset(
    {
        "manifest_version",
        "id",
        "name",
        "version",
        "description",
        "author",
        "license",
        "homepage",
        "entry",
        "icon",
        "capabilities",
        "allowed_domains",
        "min_host_version",
        "max_host_version",
        "dependencies",
        "config_schema",
    }
)

REQUIRED_FIELDS: tuple[str, ...] = (
    "manifest_version",
    "id",
    "name",
    "version",
    "description",
    "author",
    "license",
    "entry",
    "capabilities",
)

FIELD_LABELS: dict[str, str] = {
    "manifest_version": "清单版本",
    "id": "插件标识",
    "name": "插件名称",
    "version": "版本号",
    "description": "插件说明",
    "author": "作者信息",
    "license": "开源协议",
    "homepage": "插件主页",
    "entry": "入口文件",
    "icon": "插件图标",
    "capabilities": "能力声明",
    "allowed_domains": "出网域名白名单",
    "min_host_version": "最低宿主版本",
    "max_host_version": "最高宿主版本",
    "dependencies": "依赖声明",
    "config_schema": "配置项定义",
}

ALLOWED_AUTHOR_FIELDS: frozenset[str] = frozenset({"name", "url"})
ALLOWED_CONFIG_FIELDS: frozenset[str] = frozenset(
    {"key", "label", "type", "required", "sensitive", "options", "default"}
)

PLUGIN_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{2,99}$")
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
ENTRY_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]*\.py$")
# 依赖声明只允许包名加版本约束，杜绝在依赖串里夹带命令。
DEPENDENCY_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9,._-]+\])?([<>=!~]=?[A-Za-z0-9._*+!-]+)?$"
)
CONFIG_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,49}$")
WEB_URL_PATTERN = re.compile(r"^https?://[^\s]+$")

ALLOWED_ICON_EXTENSIONS: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".svg", ".webp"}
)
ALLOWED_CONFIG_TYPES: frozenset[str] = frozenset(
    {"text", "password", "number", "switch", "select"}
)

MAX_DEPENDENCY_COUNT = 30
MAX_CONFIG_ITEM_COUNT = 50
MAX_DESCRIPTION_LENGTH = 2000
MAX_SUMMARY_LENGTH = 255


def manifest_summary(description: str) -> str:
    """取插件说明的首段作为列表页简介，避免在清单里额外要求填写简介。"""
    first_line = str(description or "").strip().splitlines()[0] if description else ""
    return first_line[:MAX_SUMMARY_LENGTH]


def _raise(message: str, code: int) -> None:
    """统一抛出可展示给用户的业务异常。"""
    raise BusinessError(message, code)


def _require_text(
    payload: dict[str, Any], field: str, max_length: int
) -> str:
    """读取必填文本字段，缺失、非文本或超长时报出中文提示。"""
    label = FIELD_LABELS[field]
    value = payload.get(field)
    if value is None:
        _raise(f"插件清单缺少必填字段：{label}", CODE_MANIFEST_INVALID)
    if not isinstance(value, str):
        _raise(f"插件清单字段「{label}」必须是文本", CODE_MANIFEST_INVALID)
    text = value.strip()
    if not text:
        _raise(f"插件清单字段「{label}」不能为空", CODE_MANIFEST_INVALID)
    if len(text) > max_length:
        _raise(
            f"插件清单字段「{label}」不能超过 {max_length} 个字符",
            CODE_MANIFEST_INVALID,
        )
    return text


def _optional_text(
    payload: dict[str, Any], field: str, max_length: int
) -> str:
    """读取选填文本字段，缺失时返回空字符串。"""
    value = payload.get(field)
    if value is None:
        return ""
    if not isinstance(value, str):
        _raise(
            f"插件清单字段「{FIELD_LABELS[field]}」必须是文本",
            CODE_MANIFEST_INVALID,
        )
    text = value.strip()
    if len(text) > max_length:
        _raise(
            f"插件清单字段「{FIELD_LABELS[field]}」不能超过 {max_length} 个字符",
            CODE_MANIFEST_INVALID,
        )
    return text


def _require_web_url(value: str, label: str) -> str:
    """校验地址必须以 http:// 或 https:// 开头。"""
    if not WEB_URL_PATTERN.match(value):
        _raise(
            f"插件清单字段「{label}」必须是以 http:// 或 https:// 开头的地址",
            CODE_MANIFEST_INVALID,
        )
    return value


def _validate_version(value: str, label: str) -> str:
    """校验版本号必须为严格的三段式 x.y.z。"""
    if not VERSION_PATTERN.match(value):
        _raise(f"插件清单字段「{label}」必须是 x.y.z 三段式版本号", CODE_VERSION_INVALID)
    return value


def _validate_plugin_id(value: str) -> str:
    """校验插件标识只包含小写字母、数字、点、下划线与连字符。"""
    if not PLUGIN_ID_PATTERN.match(value):
        _raise(
            "插件标识只能由小写字母、数字、点、下划线和连字符组成，且以字母或数字开头",
            CODE_MANIFEST_INVALID,
        )
    if "." not in value and "-" not in value:
        _raise("插件标识必须包含点或连字符，例如 com.example.weather", CODE_MANIFEST_INVALID)
    return value


def _validate_entry(value: str) -> str:
    """校验入口文件为包内相对 Python 文件，禁止绝对路径与目录穿越。"""
    if "\\" in value or value.startswith("/") or ".." in value.split("/"):
        _raise(
            "入口文件必须是插件包内的相对路径，不能使用绝对路径或目录穿越",
            CODE_MANIFEST_INVALID,
        )
    if not ENTRY_PATTERN.match(value):
        _raise("入口文件必须是包内的 .py 文件，例如 plugin.py", CODE_MANIFEST_INVALID)
    return value


def _validate_icon(value: str) -> str:
    """校验图标为包内相对路径且扩展名受支持。"""
    if "\\" in value or value.startswith("/") or ".." in value.split("/"):
        _raise(
            "插件图标必须是插件包内的相对路径，不能使用绝对路径或目录穿越",
            CODE_MANIFEST_INVALID,
        )
    suffix = value[value.rfind(".") :].lower() if "." in value else ""
    if suffix not in ALLOWED_ICON_EXTENSIONS:
        _raise(
            "插件图标只支持 png、jpg、jpeg、svg、webp 格式",
            CODE_MANIFEST_INVALID,
        )
    return value


def _validate_author(payload: dict[str, Any]) -> dict[str, str]:
    """校验作者信息，仅允许 name 与 url 两个字段。"""
    value = payload.get("author")
    if not isinstance(value, dict):
        _raise("插件清单字段「作者信息」必须是包含 name 的对象", CODE_MANIFEST_INVALID)
    unknown = sorted(set(value) - ALLOWED_AUTHOR_FIELDS)
    if unknown:
        _raise(
            "插件清单「作者信息」存在不支持的字段：" + "、".join(unknown),
            CODE_MANIFEST_INVALID,
        )
    raw_name = value.get("name")
    if not isinstance(raw_name, str) or not raw_name.strip():
        _raise("插件清单「作者信息」缺少必填字段：作者名称", CODE_MANIFEST_INVALID)
    name = raw_name.strip()
    if len(name) > 100:
        _raise(
            "插件清单「作者信息」的作者名称不能超过 100 个字符",
            CODE_MANIFEST_INVALID,
        )
    raw_url = value.get("url") or ""
    if not isinstance(raw_url, str):
        _raise("插件清单「作者信息」的 url 必须是文本", CODE_MANIFEST_INVALID)
    url = raw_url.strip()
    if len(url) > 500:
        _raise(
            "插件清单「作者信息」的 url 不能超过 500 个字符",
            CODE_MANIFEST_INVALID,
        )
    if url:
        _require_web_url(url, "作者主页")
    return {"name": name, "url": url}


def _validate_capabilities(payload: dict[str, Any]) -> list[str]:
    """校验能力声明：非空数组、每项非空文本、不重复。

    **不校验是否在本地白名单内**。插件库只负责分发，能力是否可用由宿主决定；
    宿主是外部在改的，能力会持续扩展，这里硬卡只会把新能力挡在门外。
    本地未登记的能力按原样放行，只记一条日志，方便发现清单笔误。
    """
    value = payload.get("capabilities")
    if not isinstance(value, list) or not value:
        _raise("插件清单「能力声明」必须是非空数组", CODE_CAPABILITY_INVALID)
    declared: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            _raise("插件清单「能力声明」的每一项都必须是非空文本", CODE_CAPABILITY_INVALID)
        code = item.strip()
        if code not in CAPABILITY_CODE_SET:
            logger.warning("插件清单声明了本地未登记的能力，按原样放行：%s", code)
        if code in declared:
            _raise(f"插件清单「能力声明」重复出现：{code}", CODE_CAPABILITY_INVALID)
        declared.append(code)
    return declared


def _validate_dependencies(payload: dict[str, Any]) -> list[str]:
    """校验依赖声明，只允许包名加版本约束，禁止夹带任何命令。"""
    value = payload.get("dependencies")
    if value is None:
        value = []
    if not isinstance(value, list):
        _raise("插件清单「依赖声明」必须是数组", CODE_MANIFEST_INVALID)
    dependencies: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            _raise("插件清单「依赖声明」的每一项都必须是非空文本", CODE_MANIFEST_INVALID)
        requirement = item.strip()
        if not DEPENDENCY_PATTERN.match(requirement):
            _raise(
                f"依赖声明不合法：{requirement}，只允许包名和版本约束，例如 httpx>=0.27",
                CODE_MANIFEST_INVALID,
            )
        dependencies.append(requirement)
    if len(dependencies) > MAX_DEPENDENCY_COUNT:
        _raise(
            f"依赖声明最多 {MAX_DEPENDENCY_COUNT} 条，当前 {len(dependencies)} 条",
            CODE_MANIFEST_INVALID,
        )
    return dependencies


def _normalize_default(
    key: str, item_type: str, value: Any, options: list[str]
) -> Any:
    """把配置项的默认值收敛成与类型匹配的标量，非法时退回「无默认值」。

    面板用它预填输入框；没有默认值时前端留空，由插件自己的兜底逻辑决定实际行为。
    """
    if value is None:
        return None
    if item_type == "switch":
        return (
            bool(value)
            if isinstance(value, bool)
            else str(value).strip().lower() in {"1", "true", "yes", "on"}
        )
    if item_type == "number":
        try:
            return int(float(str(value).strip()))
        except (TypeError, ValueError):
            return None
    text = str(value).strip()
    if item_type == "select":
        # 默认值必须是可选项之一，否则用户一打开面板就是非法状态。
        return text if text in options else None
    return text[:5000] if text else None


def _validate_config_schema(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """校验配置项定义，保证 key 唯一且类型在白名单内。"""
    value = payload.get("config_schema")
    if value is None:
        value = []
    if not isinstance(value, list):
        _raise("插件清单「配置项定义」必须是数组", CODE_MANIFEST_INVALID)
    if len(value) > MAX_CONFIG_ITEM_COUNT:
        _raise(
            f"配置项最多 {MAX_CONFIG_ITEM_COUNT} 项，当前 {len(value)} 项",
            CODE_MANIFEST_INVALID,
        )
    items: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict):
            _raise("插件清单「配置项定义」的每一项都必须是对象", CODE_MANIFEST_INVALID)
        unknown = sorted(set(raw) - ALLOWED_CONFIG_FIELDS)
        if unknown:
            _raise(
                "配置项存在不支持的字段：" + "、".join(unknown),
                CODE_MANIFEST_INVALID,
            )
        key = raw.get("key")
        if not isinstance(key, str) or not CONFIG_KEY_PATTERN.match(key.strip()):
            _raise(
                "配置项 key 只能由小写字母、数字和下划线组成，且以字母开头",
                CODE_MANIFEST_INVALID,
            )
        key = key.strip()
        if key in seen_keys:
            _raise(f"配置项 key 重复出现：{key}", CODE_MANIFEST_INVALID)
        seen_keys.add(key)
        label = raw.get("label")
        if not isinstance(label, str) or not label.strip():
            _raise(f"配置项 {key} 缺少必填字段：显示名称", CODE_MANIFEST_INVALID)
        item_type = str(raw.get("type") or "text").strip()
        if item_type not in ALLOWED_CONFIG_TYPES:
            _raise(
                f"配置项 {key} 的类型不支持：{item_type}，可选类型为："
                + "、".join(sorted(ALLOWED_CONFIG_TYPES)),
                CODE_MANIFEST_INVALID,
            )
        options = raw.get("options") or []
        if item_type == "select":
            if not isinstance(options, list) or not options:
                _raise(f"下拉型配置项 {key} 必须提供可选项", CODE_MANIFEST_INVALID)
            options = [str(option).strip() for option in options if str(option).strip()]
            if not options:
                _raise(f"下拉型配置项 {key} 必须提供可选项", CODE_MANIFEST_INVALID)
        else:
            options = []
        items.append(
            {
                "key": key,
                "label": label.strip()[:100],
                "type": item_type,
                "required": bool(raw.get("required", False)),
                "sensitive": bool(raw.get("sensitive", False)),
                "options": options,
                "default": _normalize_default(key, item_type, raw.get("default"), options),
            }
        )
    return items


def normalize_manifest(payload: Any) -> dict[str, Any]:
    """
    严格校验并归一化插件清单。

    Args:
        payload: 提交上来的清单原始内容，必须是 JSON 对象。
    Returns:
        归一化后的清单，包含全部 16 个字段，缺省字段以空值或空数组补齐。
    Raises:
        BusinessError: 清单缺字段、含未知字段或任一取值不合法。
    """
    if not isinstance(payload, dict):
        _raise("插件清单必须是 JSON 对象", CODE_MANIFEST_INVALID)

    unknown = sorted(set(payload) - ALLOWED_FIELDS)
    if unknown:
        _raise(
            "插件清单存在不支持的字段：" + "、".join(unknown),
            CODE_MANIFEST_INVALID,
        )

    manifest_version = payload.get("manifest_version")
    if manifest_version != MANIFEST_VERSION:
        _raise(
            f"插件清单版本必须为 {MANIFEST_VERSION}，当前为：{manifest_version}",
            CODE_MANIFEST_INVALID,
        )

    plugin_id = _validate_plugin_id(_require_text(payload, "id", 100))
    name = _require_text(payload, "name", 100)
    version = _validate_version(_require_text(payload, "version", 30), "版本号")
    description = _require_text(payload, "description", MAX_DESCRIPTION_LENGTH)
    author = _validate_author(payload)
    license_name = _require_text(payload, "license", 50)
    homepage = _optional_text(payload, "homepage", 500)
    if homepage:
        _require_web_url(homepage, "插件主页")
    entry = _validate_entry(_require_text(payload, "entry", 255))
    icon = _optional_text(payload, "icon", 255)
    if icon:
        _validate_icon(icon)
    declared = _validate_capabilities(payload)
    domains = validate_allowed_domains(payload, declared)
    min_host_version = _optional_text(payload, "min_host_version", 30)
    if min_host_version:
        _validate_version(min_host_version, "最低宿主版本")
    max_host_version = _optional_text(payload, "max_host_version", 30)
    if max_host_version:
        _validate_version(max_host_version, "最高宿主版本")
    dependencies = _validate_dependencies(payload)
    config_schema = _validate_config_schema(payload)

    return {
        "manifest_version": MANIFEST_VERSION,
        "id": plugin_id,
        "name": name,
        "version": version,
        "description": description,
        "author": author,
        "license": license_name,
        "homepage": homepage,
        "entry": entry,
        "icon": icon,
        "capabilities": declared,
        "allowed_domains": domains,
        "min_host_version": min_host_version,
        "max_host_version": max_host_version,
        "dependencies": dependencies,
        "config_schema": config_schema,
    }

