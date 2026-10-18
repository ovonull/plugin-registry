"""
插件系统的初始化数据字典。

功能：
1. 集中维护插件系统用到的枚举中文名称：来源类型、审核状态、审核动作、动作结果与能力；
2. 能力中文名称与风险等级直接取自 plugin_capability，避免两处维护出现不一致；
3. 由 PluginService.initialize_defaults 遍历幂等写入，已存在则跳过。

约定：前端展示枚举时一律查询数据字典，禁止在前端硬编码中文映射。
"""

from app.services.plugin_capability import CAPABILITY_SPECS, RISK_HIGH, RISK_LOW, RISK_MEDIUM


_SOURCE_TYPE_ITEMS: tuple[tuple[str, str, int], ...] = (
    ("repo", "代码仓库", 10),
    ("zip", "上传压缩包", 20),
)

_REVIEW_STATUS_ITEMS: tuple[tuple[str, str, int], ...] = (
    ("pending", "待审核", 10),
    ("approved", "已通过", 20),
    ("rejected", "已驳回", 30),
)

_REVIEW_ACTION_ITEMS: tuple[tuple[str, str, int], ...] = (
    ("submit", "提交审核", 10),
    ("approve", "审核通过", 20),
    ("reject", "审核驳回", 30),
    ("publish", "上架", 40),
    ("unpublish", "下架", 50),
    ("delete", "删除插件", 60),
)

_ACTION_RESULT_ITEMS: tuple[tuple[str, str, int], ...] = (
    ("ok", "成功", 10),
    ("denied", "未授权", 20),
    ("error", "执行失败", 30),
    ("timeout", "执行超时", 40),
)

_CAPABILITY_RISK_ITEMS: tuple[tuple[str, str, int], ...] = (
    (RISK_LOW, "低风险", 10),
    (RISK_MEDIUM, "中风险", 20),
    (RISK_HIGH, "高风险", 30),
)


def _capability_items() -> tuple[tuple[str, str, int], ...]:
    """由能力清单派生能力字典项，能力码即数据字典的 item_code。"""
    return tuple(
        (spec["code"], spec["name"], int(spec["sort_order"])) for spec in CAPABILITY_SPECS
    )


# 初始化数据字典项：(字典类型, 编码, 中文名称, 排序)。
PLUGIN_DICTIONARY_DEFAULTS: tuple[tuple[str, str, str, int], ...] = (
    *(
        ("plugin_source_type", code, name, order)
        for code, name, order in _SOURCE_TYPE_ITEMS
    ),
    *(
        ("plugin_review_status", code, name, order)
        for code, name, order in _REVIEW_STATUS_ITEMS
    ),
    *(
        ("plugin_review_action", code, name, order)
        for code, name, order in _REVIEW_ACTION_ITEMS
    ),
    *(
        ("plugin_action_result", code, name, order)
        for code, name, order in _ACTION_RESULT_ITEMS
    ),
    *(
        ("plugin_capability_risk", code, name, order)
        for code, name, order in _CAPABILITY_RISK_ITEMS
    ),
    *(
        ("plugin_capability", code, name, order)
        for code, name, order in _capability_items()
    ),
)
