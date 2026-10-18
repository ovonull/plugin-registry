"""
插件能力的批准与出网白名单放行判断。

功能：
1. 校验并归一化清单中的出网域名白名单，声明访问外网能力时该白名单必填；
2. 按管理员勾选计算实际批准的能力，高风险能力默认不批准；
3. 提供运行期出网域名匹配，白名单外的域名一律不放行。

说明：本模块是「放行层」，审核面板与运行期能力闸门都复用这里的判断，
避免审核依据与运行期依据出现两套实现。
"""

import re
from typing import Any

from app.common.responses import BusinessError
from app.services.plugin_capability import default_granted_capabilities


# 出网域名白名单校验失败
CODE_DOMAIN_INVALID = 40092
# 能力声明校验失败
CODE_CAPABILITY_INVALID = 40091

OUTBOUND_CAPABILITY = "http.outbound"

# 出网域名只允许裸域名或通配前缀，禁止出现协议、端口与路径。
DOMAIN_PATTERN = re.compile(
    r"^(\*\.)?([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$"
)

MAX_DOMAIN_COUNT = 20


def validate_allowed_domains(payload: dict[str, Any], declared: list[str]) -> list[str]:
    """
    校验出网域名白名单。

    Args:
        payload: 清单原始内容。
        declared: 已校验通过的能力声明。
    Returns:
        归一化后的域名白名单。
    Raises:
        BusinessError: 域名格式不合法、数量超限，或与出网能力声明不匹配。
    """
    value = payload.get("allowed_domains")
    if value is None:
        value = []
    if not isinstance(value, list):
        raise BusinessError("插件清单「出网域名白名单」必须是数组", CODE_DOMAIN_INVALID)
    domains: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise BusinessError(
                "插件清单「出网域名白名单」的每一项都必须是非空文本",
                CODE_DOMAIN_INVALID,
            )
        domain = item.strip().lower()
        if not DOMAIN_PATTERN.match(domain):
            raise BusinessError(
                f"出网域名不合法：{domain}，只允许填写裸域名或通配前缀，"
                "例如 api.example.com 或 *.example.com",
                CODE_DOMAIN_INVALID,
            )
        if domain in domains:
            raise BusinessError(f"出网域名重复出现：{domain}", CODE_DOMAIN_INVALID)
        domains.append(domain)
    if len(domains) > MAX_DOMAIN_COUNT:
        raise BusinessError(
            f"出网域名白名单最多 {MAX_DOMAIN_COUNT} 条，当前 {len(domains)} 条",
            CODE_DOMAIN_INVALID,
        )
    if OUTBOUND_CAPABILITY in declared and not domains:
        raise BusinessError(
            "声明访问外网能力时必须同时填写出网域名白名单，否则视为未通过校验",
            CODE_DOMAIN_INVALID,
        )
    if OUTBOUND_CAPABILITY not in declared and domains:
        raise BusinessError(
            "未声明访问外网能力时不能填写出网域名白名单",
            CODE_DOMAIN_INVALID,
        )
    return domains


def resolve_granted_capabilities(
    declared: list[str], requested: list[str] | None
) -> list[str]:
    """
    计算管理员实际批准的能力。

    Args:
        declared: 插件在清单中申请的能力。
        requested: 管理员在审核面板勾选的能力；为空表示只批准非高风险能力。
    Returns:
        批准的能力列表，顺序与申请顺序保持一致。
    Raises:
        BusinessError: 勾选了插件未申请的能力。
    """
    if requested is None:
        return default_granted_capabilities(declared)
    selected: list[str] = []
    for code in requested:
        if code not in declared:
            raise BusinessError(
                f"不能批准插件未申请的能力：{code}",
                CODE_CAPABILITY_INVALID,
            )
        if code not in selected:
            selected.append(code)
    return [code for code in declared if code in selected]


def is_domain_allowed(domain: str, allowed_domains: list[str]) -> bool:
    """
    判断出网域名是否在插件声明的白名单内。

    Args:
        domain: 插件实际请求的域名。
        allowed_domains: 清单中声明的白名单，支持 *.example.com 通配前缀。
    Returns:
        True 表示放行。
    """
    target = str(domain or "").strip().lower()
    if not target:
        return False
    for rule in allowed_domains:
        normalized = str(rule or "").strip().lower()
        if not normalized:
            continue
        if normalized.startswith("*."):
            if target.endswith(normalized[1:]) and target != normalized[2:]:
                return True
        elif target == normalized:
            return True
    return False
