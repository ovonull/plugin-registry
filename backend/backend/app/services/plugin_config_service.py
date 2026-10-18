"""
插件安装配置的读取、校验与敏感项加密。

功能：
1. 从版本清单中解析配置项定义，作为插件配置的唯一依据；
2. 按定义校验提交值（文本、密码、数字、开关、下拉），非法取值给出中文提示；
3. 敏感项加密后入库，读取时解密；提交空值或占位符表示保持原值不变；
4. 未在清单中声明的配置项一律丢弃，避免插件读到未审核的参数。

约定：本模块只处理配置数据本身，不写数据库，也不做权限判断。
"""

import json
import logging
from typing import Any

from app.common.responses import BusinessError
from app.models.plugin import PluginInstallation, PluginVersion
from app.services.plugin_common import CODE_INSTALL_REQUEST_INVALID
from app.services.setting_service import decrypt_secret, encrypt_secret


logger = logging.getLogger("uvicorn.error")

# 敏感项在提交时使用该占位符表示「保持原值不变」。
SECRET_MASK = "******"
MAX_CONFIG_VALUE_LENGTH = 5000


def config_schema(version: PluginVersion | None) -> list[dict[str, Any]]:
    """
    从版本清单中取出配置项定义。

    Args:
        version: 插件版本记录，为空表示没有可用清单。
    Returns:
        配置项定义列表，清单缺失或解析失败时返回空列表。
    """
    if version is None:
        return []
    try:
        manifest = json.loads(version.manifest_json or "{}")
    except json.JSONDecodeError:
        logger.warning("插件版本清单不是合法 JSON，配置项按空处理，版本=%s", version.id)
        return []
    schema = manifest.get("config_schema") if isinstance(manifest, dict) else None
    return schema if isinstance(schema, list) else []


def load_config(
    installation: PluginInstallation, version: PluginVersion | None
) -> dict[str, Any]:
    """
    读取安装配置，敏感项解密后返回，未声明的配置项不返回。

    Args:
        installation: 安装记录。
        version: 安装时对应的版本记录。
    Returns:
        配置键值对。
    """
    raw = load_raw_config(installation.config_json)
    config: dict[str, Any] = {}
    for item in config_schema(version):
        key = str(item.get("key") or "")
        if not key or key not in raw:
            continue
        value = raw[key]
        if item.get("sensitive"):
            value = decrypt_secret(str(value or ""))
        config[key] = value
    return config


def default_config(schema: list[dict[str, Any]]) -> dict[str, Any]:
    """
    按配置项定义生成初始空配置。

    Args:
        schema: 配置项定义列表。
    Returns:
        开关默认关闭、数字默认 0、其余默认空文本的配置。
    """
    config: dict[str, Any] = {}
    for item in schema:
        key = str(item.get("key") or "")
        if not key:
            continue
        config[key] = _single_default(item)
    return config


def load_raw_config(value: str) -> dict[str, Any]:
    """
    容错解析已保存的配置 JSON。

    Args:
        value: 数据库中保存的配置字符串。
    Returns:
        配置字典，内容不合法时返回空字典。
    """
    try:
        loaded = json.loads(value or "{}")
    except json.JSONDecodeError:
        logger.warning("插件安装配置不是合法 JSON，已按空配置处理")
        return {}
    return loaded if isinstance(loaded, dict) else {}


def missing_required(
    schema: list[dict[str, Any]], stored: dict[str, Any]
) -> list[str]:
    """
    找出必填但尚未填写的配置项。

    Args:
        schema: 配置项定义列表。
        stored: 已保存的原始配置（敏感项为密文）。
    Returns:
        缺失配置项的显示名称列表，全部填写时返回空列表。
    """
    missing: list[str] = []
    for item in schema:
        if not item.get("required"):
            continue
        key = str(item.get("key") or "")
        if not key:
            continue
        value = stored.get(key)
        if value is None or str(value).strip() == "":
            missing.append(str(item.get("label") or key))
    return missing


def apply_config(
    schema: list[dict[str, Any]],
    submitted: dict[str, Any],
    existing: dict[str, Any],
) -> dict[str, Any]:
    """
    按清单声明校验并合并插件配置。

    Args:
        schema: 配置项定义列表。
        submitted: 本次提交的配置，未出现的键表示不修改。
        existing: 已保存的原始配置。
    Returns:
        可直接入库的新配置。
    Raises:
        BusinessError: 提交值类型不合法或必填项为空。
    """
    if not isinstance(submitted, dict):
        raise BusinessError("插件配置格式不正确", CODE_INSTALL_REQUEST_INVALID)
    result: dict[str, Any] = {}
    for item in schema:
        key = str(item.get("key") or "")
        if not key:
            continue
        label = str(item.get("label") or key)
        item_type = str(item.get("type") or "text")
        required = bool(item.get("required"))
        previous = existing.get(key)
        if key not in submitted:
            if previous is not None:
                result[key] = previous
            elif required:
                raise BusinessError(
                    f"配置项「{label}」为必填项", CODE_INSTALL_REQUEST_INVALID
                )
            else:
                result[key] = _single_default(item)
            continue
        value = _coerce_value(item, label, item_type, submitted.get(key))
        if item.get("sensitive"):
            if value in ("", SECRET_MASK):
                result[key] = previous if previous is not None else ""
                continue
            result[key] = encrypt_secret(str(value))
            continue
        if value in ("", None) and required:
            raise BusinessError(
                f"配置项「{label}」为必填项", CODE_INSTALL_REQUEST_INVALID
            )
        result[key] = value
    return result


def _single_default(item: dict[str, Any]) -> Any:
    """返回单个配置项的默认值。"""
    item_type = str(item.get("type") or "text")
    if item_type == "switch":
        return False
    if item_type == "number":
        return 0
    return ""


def _coerce_value(
    item: dict[str, Any], label: str, item_type: str, raw: Any
) -> Any:
    """
    把提交值转换为清单声明的类型。

    Args:
        item: 配置项定义。
        label: 配置项显示名称，用于中文提示。
        item_type: 配置项类型。
        raw: 前端提交的原始值。
    Returns:
        转换后的值。
    Raises:
        BusinessError: 取值不符合声明类型时拒绝。
    """
    if item_type == "switch":
        if isinstance(raw, bool):
            return raw
        text = str(raw if raw is not None else "").strip().lower()
        if text in {"true", "1", "yes", "on"}:
            return True
        if text in {"false", "0", "no", "off", ""}:
            return False
        raise BusinessError(
            f"配置项「{label}」只能填写是或否", CODE_INSTALL_REQUEST_INVALID
        )
    if item_type == "number":
        try:
            return int(float(str(raw).strip() or 0))
        except (TypeError, ValueError):
            raise BusinessError(
                f"配置项「{label}」必须填写数字", CODE_INSTALL_REQUEST_INVALID
            )
    text = "" if raw is None else str(raw).strip()
    if len(text) > MAX_CONFIG_VALUE_LENGTH:
        raise BusinessError(
            f"配置项「{label}」不能超过 {MAX_CONFIG_VALUE_LENGTH} 个字符",
            CODE_INSTALL_REQUEST_INVALID,
        )
    if item_type == "select" and text:
        options = [str(option) for option in (item.get("options") or [])]
        if text not in options:
            raise BusinessError(
                f"配置项「{label}」只能填写：" + "、".join(options),
                CODE_INSTALL_REQUEST_INVALID,
            )
    return text
