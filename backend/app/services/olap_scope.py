"""数据范围领域服务：行级/列级范围控制、数据领域隔离、按期间回查授权。"""
from __future__ import annotations

import re
from typing import Any

from app.services.base import BaseModuleService
from app.store import store

# 授权级别强弱：读写可以覆盖只读，反向不行。
_ACCESS_RANK = {"只读": 1, "读写": 2}
# 历史回查时仍承认的规则状态：冻结/撤销代表“现在不再授权”，但当时确实授权过。
_HISTORICAL_STATUSES = {"已生效", "已冻结", "已撤销"}
_IN_FILTER = re.compile(
    r"^\s*(?P<field>[\w一-龥]+)\s+in\s*\((?P<values>[^)]*)\)\s*$",
    re.IGNORECASE,
)
_EQ_FILTER = re.compile(
    r"^\s*(?P<field>[\w一-龥]+)\s*=\s*(?P<value>.+?)\s*$",
)


class OlapScopeService(BaseModuleService):
    MODULE = "olap_scope"
    ENTITY = "范围规则"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["规则名称", "数据范围", "授权对象", "可见字段", "行过滤条件", "生效时间", "失效时间"]
    REQUIRED_FIELDS = ["规则名称", "数据范围", "授权对象"]
    STATUS_ORDER = ["草稿", "已生效", "已冻结", "已撤销"]
    ACTION_RULES = {"生效规则": "已生效", "冻结规则": "已冻结", "撤销规则": "已撤销"}
    NEGATIVE_ACTIONS = ["撤销规则"]

    # ---- 业务方法 ----

    def apply_scope_filters(
        self, rule_ids: list[int], data: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """对数据应用多条范围规则的并集过滤。"""
        visible_columns: set[str] = set()
        row_filters: list[str] = []
        matched_rules: list[str] = []

        for rid in rule_ids:
            rule = store.find(self.MODULE, rid)
            if rule is None or rule.get("status") != "已生效":
                continue
            matched_rules.append(str(rule.get("规则名称", "")))
            cols = str(rule.get("可见字段", "")).split(",")
            for c in cols:
                c = c.strip()
                if c:
                    visible_columns.add(c)
            rf = str(rule.get("行过滤条件", "")).strip()
            if rf:
                row_filters.append(rf)

        # 应用列过滤
        if visible_columns:
            filtered_data = [
                {k: v for k, v in row.items() if k in visible_columns}
                for row in data
            ]
        else:
            filtered_data = list(data)

        # 应用行过滤
        if row_filters:
            for rf in row_filters:
                filtered_data = _apply_row_filter(filtered_data, rf)

        return {
            "ok": True,
            "applied_rules": matched_rules,
            "rule_count": len(matched_rules),
            "original_rows": len(data),
            "filtered_rows": len(filtered_data),
            "visible_columns": sorted(visible_columns),
            "sample": filtered_data[:5],
        }

    def check_access(
        self, principal: str, data_source: str,
    ) -> dict[str, Any]:
        """检查主体对数据源的访问范围。"""
        rows = store.rows(self.MODULE)
        applicable: list[dict[str, Any]] = []
        for row in rows:
            if row.get("status") != "已生效":
                continue
            auth_obj = str(row.get("授权对象", ""))
            data_range = str(row.get("数据范围", ""))
            if principal in auth_obj.split(",") and data_source in data_range:
                applicable.append({
                    "rule_name": row.get("规则名称"),
                    "visible_fields": str(row.get("可见字段", "")).split(","),
                    "row_filter": row.get("行过滤条件"),
                })
        return {
            "ok": True,
            "principal": principal,
            "data_source": data_source,
            "has_access": len(applicable) > 0,
            "applicable_rules": applicable,
        }

    def get_effective_scopes(self) -> list[dict[str, Any]]:
        """获取所有生效的数据范围规则（供语义层消费）。"""
        return [
            row for row in store.rows(self.MODULE)
            if row.get("status") == "已生效"
        ]

    # ---- 按数据访问范围回查授权（跨期对照消费） ----

    def resolve_period_grants(
        self,
        principal: str,
        data_source: str,
        period_start: str,
        period_end: str,
    ) -> dict[str, Any]:
        """解析主体在某一期间对数据源的实际可读区域，并给出纳入原因。

        跨期对照必须回答“当时能读什么”，因此按规则的生效/失效区间做时间切片，
        而不是只看规则当前状态：Q3 的授权即使现在已冻结，只要在 Q3 区间内生效过，
        就仍然进入 Q3 的可读区域。规则当前状态只影响是否还能继续授权，不影响历史口径。
        """
        rules = self._period_applicable_rules(principal, data_source, period_start, period_end)
        regions: dict[str, dict[str, Any]] = {}
        for rule in rules:
            level = str(rule.get("授权级别") or "读写")
            rule_regions = _regions_from_filter(str(rule.get("行过滤条件", "")))
            # 没有区域条件代表全区域授权（如管理者全量范围），展开到全部区域。
            scope_regions = rule_regions or _all_known_regions()
            for region in scope_regions:
                current = regions.get(region)
                if current is None:
                    regions[region] = {
                        "region": region,
                        "access_level": level,
                        "reason": "区域内授权",
                        "rule_ids": [int(rule["id"])],
                        "rule_names": [str(rule.get("规则名称", ""))],
                    }
                    continue
                current["rule_ids"].append(int(rule["id"]))
                current["rule_names"].append(str(rule.get("规则名称", "")))
                # 同一区域被多条规则覆盖时取最强授权，但保留只读补充的来源说明。
                if _ACCESS_RANK.get(level, 0) > _ACCESS_RANK.get(current["access_level"], 0):
                    current["access_level"] = level
                    current["reason"] = "区域内授权"
                if level == "只读":
                    current["reason"] = "区域内授权（含只读补充）"

        ordered = [regions[name] for name in sorted(regions)]
        read_only = [item["region"] for item in ordered if item["access_level"] == "只读"]
        return {
            "ok": True,
            "principal": principal,
            "data_source": data_source,
            "period_start": period_start,
            "period_end": period_end,
            "regions": ordered,
            "readable_regions": [item["region"] for item in ordered],
            "read_only_regions": read_only,
            "rule_ids": [int(rule["id"]) for rule in rules],
            "rule_names": [str(rule.get("规则名称", "")) for rule in rules],
            "granted": bool(ordered),
            "notes": "按规则生效区间回查，冻结的规则只要曾在该期间生效仍计入当时可读区域",
        }

    def list_historical_grants(
        self, principal: str, data_source: str,
    ) -> list[dict[str, Any]]:
        """列出主体历史上所有涉及该数据源的授权区间（历史对照可查）。"""
        rows = []
        for row in store.rows(self.MODULE):
            if row.get("status") not in _HISTORICAL_STATUSES:
                continue
            if principal not in str(row.get("授权对象", "")).split(","):
                continue
            if data_source not in str(row.get("数据范围", "")):
                continue
            rows.append({
                "rule_id": int(row["id"]),
                "rule_name": str(row.get("规则名称", "")),
                "status": row.get("status"),
                "data_source": str(row.get("数据范围", "")),
                "principal": principal,
                "access_level": str(row.get("授权级别") or "读写"),
                "period_start": str(row.get("生效时间", "")),
                "period_end": str(row.get("失效时间", "")),
                "regions": _regions_from_filter(str(row.get("行过滤条件", ""))),
                "row_filter": str(row.get("行过滤条件", "")),
                "visible_fields": [c.strip() for c in str(row.get("可见字段", "")).split(",") if c.strip()],
            })
        return sorted(rows, key=lambda item: (item["period_start"], item["rule_id"]))

    def verify_shared_access(
        self, viewer: str, data_source: str, regions: list[str],
    ) -> dict[str, Any]:
        """校验共享报表的查看者是否仍能看这些区域明细，防止借共享越权。"""
        allowed: list[str] = []
        denied: list[str] = []
        reasons: list[str] = []
        for region in regions:
            grants = self.resolve_region_grants(viewer, data_source, region)
            if grants:
                allowed.append(region)
            else:
                denied.append(region)
                reasons.append(f"{viewer} 未被授权访问 {region} 明细")
        return {
            "ok": not denied,
            "viewer": viewer,
            "data_source": data_source,
            "allowed_regions": allowed,
            "denied_regions": denied,
            "reasons": reasons,
        }

    def resolve_region_grants(
        self, principal: str, data_source: str, region: str,
    ) -> list[dict[str, Any]]:
        """取主体当前对某区域的授权规则（共享校验用实时状态）。"""
        matched: list[dict[str, Any]] = []
        for row in store.rows(self.MODULE):
            if row.get("status") != "已生效":
                continue
            if principal not in str(row.get("授权对象", "")).split(","):
                continue
            if data_source not in str(row.get("数据范围", "")):
                continue
            region_list = _regions_from_filter(str(row.get("行过滤条件", "")))
            if region_list and region not in region_list:
                continue
            if not region_list and not _grants_all_regions(row):
                continue
            matched.append({
                    "rule_id": int(row["id"]),
                    "rule_name": row.get("规则名称"),
                    "access_level": str(row.get("授权级别") or "读写"),
                })
        return matched

    def _period_applicable_rules(
        self, principal: str, data_source: str, period_start: str, period_end: str,
    ) -> list[dict[str, Any]]:
        matched = []
        for row in store.rows(self.MODULE):
            if row.get("status") not in _HISTORICAL_STATUSES:
                continue
            if principal not in str(row.get("授权对象", "")).split(","):
                continue
            if data_source not in str(row.get("数据范围", "")):
                continue
            rule_start = str(row.get("生效时间", ""))
            rule_end = str(row.get("失效时间", ""))
            if _interval_overlaps(rule_start, rule_end, period_start, period_end):
                matched.append(row)
        return sorted(matched, key=lambda item: int(item["id"]))

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        base["artifact"] = "数据范围规则数据集"
        base["effective_rules"] = sum(1 for row in store.rows(self.MODULE) if row.get("status") == "已生效")
        return base


def _apply_row_filter(
    data: list[dict[str, Any]], filter_expr: str,
) -> list[dict[str, Any]]:
    """应用行级过滤表达式。"""
    results: list[dict[str, Any]] = []
    for row in data:
        try:
            row_context = {**row}
            if eval(filter_expr, {"__builtins__": {}}, row_context):
                results.append(row)
        except Exception:
            results.append(row)
    return results


def _regions_from_filter(filter_expr: str) -> list[str]:
    """从行过滤表达式里抽取区域清单。

    支持 `地区 in ('华北','华东')`、`地区 = '西南'`，以及按城市过滤的
    `城市 in ('上海','杭州')` —— 城市条件按其所属区域折算，保证 9 月的
    华东城市授权在跨期对照里仍能落到区域口径上。
    无法识别区域条件时返回空列表，表示该规则没有按区域收窄。
    """
    if not filter_expr.strip():
        return []
    from app.revenue_facts import CITY_REGION

    for clause in filter_expr.split(" and "):
        in_match = _IN_FILTER.match(clause)
        if in_match:
            field = in_match.group("field")
            values = [
                value.strip().strip("'\"")
                for value in re.split(r"[,，]", in_match.group("values"))
                if value.strip().strip("'\"")
            ]
            if field in ("地区", "region"):
                return values
            if field == "城市":
                return sorted({CITY_REGION[c] for c in values if c in CITY_REGION})
        eq_match = _EQ_FILTER.match(clause)
        if eq_match:
            field = eq_match.group("field")
            value = eq_match.group("value").strip().strip("'\"")
            if field in ("地区", "region"):
                return [value] if value else []
            if field == "城市":
                return [CITY_REGION[value]] if value in CITY_REGION else []
    return []


def _grants_all_regions(rule: dict[str, Any]) -> bool:
    """判断规则是否代表全区域授权：无区域条件且可见字段为“全部”。"""
    return str(rule.get("可见字段", "")).strip() == "全部"


def _all_known_regions() -> list[str]:
    """全区域授权时展开使用的区域全集（取自收入事实）。"""
    from app.revenue_facts import ALL_REGIONS
    return list(ALL_REGIONS)


def _interval_overlaps(
    rule_start: str, rule_end: str, period_start: str, period_end: str,
) -> bool:
    """判断授权区间与统计期间是否相交；缺失边界视为不限。"""
    if rule_end and rule_end < period_start:
        return False
    if rule_start and rule_start > period_end:
        return False
    return True
