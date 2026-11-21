"""跨期区域收入对照：按数据访问范围计算两期可比口径。

切片查询负责发起，数据范围模块负责回答“这一期到底能读哪些区域”。本模块只做
两件事：把两期的授权结果对齐成可比口径，并把结果连同当期授权依据一起留档，
保证报表共享和历史回查时都能回到“当时允许读取的数据”。
"""
from __future__ import annotations

import copy
import datetime
import itertools
from typing import Any

from app.revenue_facts import (
    ALL_REGIONS,
    DEFAULT_DATA_SOURCES,
    PERIOD_PRESETS,
    REVENUE_FACTS,
)

_ACCESS_RANK = {"只读": 1, "读写": 2}
_COMPARISONS: dict[int, dict[str, Any]] = {}
_SEQUENCE = itertools.count(1)


class PeriodCompareError(ValueError):
    """跨期对照的输入错误，路由层直接转成 400。"""


def build_comparison(
    principal: str,
    period_a: str,
    period_b: str,
    data_sources: list[str],
    scope_service: Any,
    template_id: int | None = None,
    template_name: str = "",
) -> dict[str, Any]:
    """计算两期区域收入对照，并保存当期授权快照。"""
    if period_a not in PERIOD_PRESETS:
        raise PeriodCompareError(f"未知统计期间：{period_a}")
    if period_b not in PERIOD_PRESETS:
        raise PeriodCompareError(f"未知统计期间：{period_b}")
    if period_a == period_b:
        raise PeriodCompareError("跨期对照需要两个不同的统计期间")
    if not data_sources:
        raise PeriodCompareError("至少需要一个数据域")

    period_scopes = {
        period_a: _resolve_period_scope(principal, period_a, data_sources, scope_service),
        period_b: _resolve_period_scope(principal, period_b, data_sources, scope_service),
    }
    regions_a = set(period_scopes[period_a]["readable_regions"])
    regions_b = set(period_scopes[period_b]["readable_regions"])
    comparable = sorted(regions_a & regions_b)
    new_regions = sorted(regions_b - regions_a)
    removed_regions = sorted(regions_a - regions_b)

    period_results: dict[str, dict[str, Any]] = {}
    for key, scope in period_scopes.items():
        period_results[key] = _period_revenue_view(
            period_key=key,
            scope=scope,
            comparable_regions=comparable,
            new_regions=new_regions,
            removed_regions=removed_regions,
        )

    revenue_a = _revenue_for(period_a, comparable)
    revenue_b = _revenue_for(period_b, comparable)
    delta = revenue_b - revenue_a
    delta_pct = round(delta / revenue_a * 100, 2) if revenue_a else None

    new_region_revenue = {
        region: _revenue_for(period_b, [region]) for region in new_regions
    }
    new_region_total = sum(new_region_revenue.values())

    comparison_id = next(_SEQUENCE)
    record = {
        "ok": True,
        "comparison_id": comparison_id,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "principal": principal,
        "template_id": template_id,
        "template_name": template_name,
        "data_sources": list(data_sources),
        "period_order": [period_a, period_b],
        "periods": period_results,
        "comparable_regions": comparable,
        "new_regions": new_regions,
        "removed_regions": removed_regions,
        "comparable_revenue": {
            "period_a": {"period": period_a, "revenue": round(revenue_a, 2)},
            "period_b": {"period": period_b, "revenue": round(revenue_b, 2)},
            "delta": round(delta, 2),
            "delta_pct": delta_pct,
            "caliber": "两期均可读区域，同口径对照",
        },
        "new_region_revenue": {
            "regions": new_region_revenue,
            "total": round(new_region_total, 2),
            "note": "新增归属区域在本期才可读，上期无授权，不计入可比口径变动",
        },
        "read_only_regions": {
            period_a: period_scopes[period_a]["read_only_regions"],
            period_b: period_scopes[period_b]["read_only_regions"],
        },
        "headline": _headline(period_a, period_b, delta, delta_pct, new_regions, new_region_total),
    }
    _COMPARISONS[comparison_id] = copy.deepcopy(record)
    return record


def get_comparison(comparison_id: int) -> dict[str, Any] | None:
    """按编号回查历史对照；返回的是生成当时的授权快照。"""
    record = _COMPARISONS.get(comparison_id)
    if record is None:
        return None
    snapshot = copy.deepcopy(record)
    snapshot["history"] = True
    snapshot["history_note"] = "历史对照保留生成当时的可读区域与授权依据，不受后续授权变更影响"
    return snapshot


def list_comparisons(principal: str | None = None) -> list[dict[str, Any]]:
    """列出留档的对照记录，可按分析员过滤。"""
    items = []
    for comparison_id in sorted(_COMPARISONS):
        record = _COMPARISONS[comparison_id]
        if principal and record.get("principal") != principal:
            continue
        items.append({
            "comparison_id": comparison_id,
            "generated_at": record.get("generated_at"),
            "principal": record.get("principal"),
            "periods": record.get("period_order"),
            "comparable_regions": record.get("comparable_regions"),
            "new_regions": record.get("new_regions"),
            "comparable_delta": record.get("comparable_revenue", {}).get("delta"),
        })
    return items


def shared_view(
    comparison_id: int,
    viewer: str,
    scope_service: Any,
) -> dict[str, Any] | None:
    """生成共享给其他角色的报表视图，逐区域校验查看者的实时授权。"""
    record = _COMPARISONS.get(comparison_id)
    if record is None:
        return None

    data_sources = record.get("data_sources", DEFAULT_DATA_SOURCES)
    all_regions = sorted({
        str(item["region"])
        for period in record.get("periods", {}).values()
        for item in period.get("regions", [])
    })
    allowed: list[str] = []
    denied: list[str] = []
    for region in all_regions:
        # 区域明细要求查看者在每个数据域上都有该区域的授权，缺一即拒绝。
        granted = True
        for source in data_sources:
            check = scope_service.verify_shared_access(viewer, source, [region])
            if region in check["denied_regions"]:
                granted = False
                break
        if granted:
            allowed.append(region)
        else:
            denied.append(region)

    allowed_set = set(allowed)
    periods: dict[str, Any] = {}
    for key, period in record.get("periods", {}).items():
        visible_regions = [
            item for item in period.get("regions", [])
            if item["region"] in allowed_set
        ]
        detail = [
            row for row in period.get("region_detail", [])
            if row["地区"] in allowed_set
        ]
        # 查看者对某区域只有只读权时，仍不能借共享报表展开城市明细。
        viewer_read_only: set[str] = set()
        for region in allowed_set:
            for source in data_sources:
                for grant in scope_service.resolve_region_grants(viewer, source, region):
                    if grant["access_level"] == "只读":
                        viewer_read_only.add(region)
        detail = [row for row in detail if row["地区"] not in viewer_read_only]
        visible_regions = [
            {**item, "detail_visible": False,
             "detail_note": "查看者仅有只读授权，共享视图不展开城市明细"}
            if item["region"] in viewer_read_only else item
            for item in visible_regions
        ]
        periods[key] = {
            "period": period.get("period"),
            "label": period.get("label"),
            "start": period.get("start"),
            "end": period.get("end"),
            "regions": visible_regions,
            "participating_regions": [
                region for region in period.get("participating_regions", [])
                if region in allowed_set
            ],
            # 未授权区域连名字也不回传给查看者，避免借共享报表侧写区域结构。
            "excluded_regions": [
                region for region in period.get("excluded_regions", [])
                if region in allowed_set
            ],
            "read_only_regions": [
                region for region in period.get("read_only_regions", [])
                if region in allowed_set
            ],
            "region_detail": detail,
            "revenue_total": round(
                sum(
                    float(item["revenue"])
                    for item in visible_regions
                    if item.get("revenue") is not None
                ),
                2,
            ),
            "restricted": bool(denied),
        }

    comparable = [r for r in record.get("comparable_regions", []) if r in allowed_set]
    new_regions = [r for r in record.get("new_regions", []) if r in allowed_set]
    comparable_a = _revenue_for(record["period_order"][0], comparable)
    comparable_b = _revenue_for(record["period_order"][1], comparable)
    new_region_revenue = {
        region: value for region, value in record.get("new_region_revenue", {}).get("regions", {}).items()
        if region in allowed_set
    }
    # 只有可见区域真的缩小时，口径才算被改写；否则与原对照完全一致。
    caliber_changed = (
        set(comparable) != set(record.get("comparable_regions", []))
        or set(new_regions) != set(record.get("new_regions", []))
    )
    restricted = bool(denied)

    return {
        "ok": True,
        "comparison_id": comparison_id,
        "viewer": viewer,
        "shared_from": record.get("principal"),
        "periods": periods,
        "comparable_regions": comparable,
        "new_regions": new_regions,
        "comparable_revenue": {
            "period_a": {"period": record["period_order"][0], "revenue": round(comparable_a, 2)},
            "period_b": {"period": record["period_order"][1], "revenue": round(comparable_b, 2)},
            "delta": round(comparable_b - comparable_a, 2),
            "delta_pct": (
                round((comparable_b - comparable_a) / comparable_a * 100, 2)
                if comparable_a else None
            ),
            "recalculated": caliber_changed,
            "caliber": (
                "查看者授权区域内重算的可比口径，与原始对照口径不同"
                if caliber_changed else "与原对照口径一致"
            ),
        },
        "new_region_revenue": {
            "regions": new_region_revenue,
            "total": round(sum(new_region_revenue.values()), 2),
        },
        "restricted_regions": denied,
        "notes": (
            f"共享视图按 {viewer} 当前授权重算，已隐藏 {len(denied)} 个未授权区域；"
            "未授权区域不呈现区域名、收入明细或参与计算说明。"
            if restricted
            else "查看者对各区域均有授权，共享视图与原始对照一致。"
        ),
    }


# ---------------------------------------------------------------------------
# 内部计算
# ---------------------------------------------------------------------------

def _resolve_period_scope(
    principal: str,
    period_key: str,
    data_sources: list[str],
    scope_service: Any,
) -> dict[str, Any]:
    """把多个数据域的期间授权合并成一份区域清单。"""
    preset = PERIOD_PRESETS[period_key]
    per_source: dict[str, dict[str, Any]] = {}
    for source in data_sources:
        per_source[source] = scope_service.resolve_period_grants(
            principal, source, preset["start"], preset["end"],
        )

    region_sets = [set(grant["readable_regions"]) for grant in per_source.values()]
    common_regions = set.intersection(*region_sets) if region_sets else set()
    union_regions = set().union(*region_sets) if region_sets else set()

    region_details: dict[str, dict[str, Any]] = {}
    for region in sorted(union_regions | set(ALL_REGIONS)):
        sources_granting: list[str] = []
        levels: list[str] = []
        rule_ids: set[int] = set()
        rule_names: list[str] = []
        for source, grant in per_source.items():
            item = next(
                (r for r in grant["regions"] if r["region"] == region),
                None,
            )
            if item is None:
                continue
            sources_granting.append(source)
            levels.append(str(item["access_level"]))
            rule_ids.update(int(rid) for rid in item.get("rule_ids", []))
            for name in item.get("rule_names", []):
                if name not in rule_names:
                    rule_names.append(name)

        access_level = "只读" if any(level == "只读" for level in levels) else "读写"
        readable = region in common_regions
        if readable:
            reason = (
                f"{len(sources_granting)}个数据域均有授权"
                if len(sources_granting) == len(per_source)
                else "部分数据域缺授权"
            )
        elif sources_granting:
            missing = [
                source for source in per_source
                if region not in per_source[source]["readable_regions"]
            ]
            reason = f"部分数据域已授权，但缺少：{'、'.join(missing)}"
        else:
            reason = "本期未获该区域授权"
        if not readable and not rule_ids:
            # 没有任何规则覆盖该区域时 level 无意义，避免误标成可读写。
            access_level = "无授权"

        region_details[region] = {
            "region": region,
            "readable": readable,
            "access_level": access_level,
            "reason": reason,
            "sources": sources_granting,
            "rule_ids": sorted(rule_ids),
            "rule_names": rule_names,
        }

    readable_regions = [r for r in sorted(common_regions)]
    return {
        "period": period_key,
        "label": preset["label"],
        "start": preset["start"],
        "end": preset["end"],
        "regions": [region_details[r] for r in sorted(region_details)],
        "readable_regions": readable_regions,
        "read_only_regions": [
            r for r in readable_regions
            if region_details[r]["access_level"] == "只读"
        ],
        "per_source": per_source,
        "source_rule_ids": {
            source: grant["rule_ids"] for source, grant in per_source.items()
        },
    }


def _period_revenue_view(
    period_key: str,
    scope: dict[str, Any],
    comparable_regions: list[str],
    new_regions: list[str],
    removed_regions: list[str],
) -> dict[str, Any]:
    """单期的收入视图：只汇总本期实际可读区域，标注参与计算的分区。"""
    readable = set(scope["readable_regions"])
    comparable_set = set(comparable_regions)
    new_set = set(new_regions)
    removed_set = set(removed_regions)

    regions: list[dict[str, Any]] = []
    for item in scope["regions"]:
        region = str(item["region"])
        if not item["readable"]:
            entry = {
                **item,
                "role": "未参与（无授权）",
                "revenue": None,
                "comparable": False,
            }
            regions.append(entry)
            continue
        revenue = _revenue_for(period_key, [region])
        if region in comparable_set:
            role = "可比口径"
        elif region in new_set:
            role = "新增可读（本期新增归属）"
        elif region in removed_set:
            role = "本期不可读"
        else:
            role = "仅本期可读（不可比）"
        entry = {
            **item,
            "role": role,
            "revenue": round(revenue, 2),
            "comparable": region in comparable_set,
        }
        if item["access_level"] == "只读":
            entry["detail_visible"] = False
            entry["detail_note"] = "只读授权，仅展示区域汇总，不展开城市明细"
        else:
            entry["detail_visible"] = True
        regions.append(entry)

    participating = sorted(readable)
    detail_rows: list[dict[str, Any]] = []
    for row in REVENUE_FACTS:
        if row["期间"] != period_key:
            continue
        if row["地区"] not in readable:
            continue
        info = next((item for item in scope["regions"] if item["region"] == row["地区"]), None)
        if info and info["access_level"] == "只读":
            continue
        detail_rows.append(dict(row))

    return {
        "period": period_key,
        "label": scope["label"],
        "start": scope["start"],
        "end": scope["end"],
        "regions": regions,
        "readable_regions": sorted(readable),
        "participating_regions": participating,
        "excluded_regions": sorted(
            str(item["region"]) for item in scope["regions"] if not item["readable"]
        ),
        "comparable_regions_in_period": sorted(readable & comparable_set),
        "new_regions_in_period": sorted(readable & new_set),
        "read_only_regions": scope["read_only_regions"],
        "region_detail": detail_rows,
        "revenue_total": round(sum(_revenue_for(period_key, [r]) for r in participating), 2),
        "source_rule_ids": scope["source_rule_ids"],
    }


def _revenue_for(period_key: str, regions: list[str]) -> float:
    region_set = set(regions)
    return sum(
        float(row["收入"])
        for row in REVENUE_FACTS
        if row["期间"] == period_key and row["地区"] in region_set
    )


def _headline(
    period_a: str,
    period_b: str,
    delta: float,
    delta_pct: float | None,
    new_regions: list[str],
    new_region_total: float,
) -> dict[str, Any]:
    change = f"{delta:+.2f}"
    pct = "基期无可比收入" if delta_pct is None else f"{delta_pct:+.2f}%"
    parts = [f"{period_a}→{period_b} 可比口径收入变动 {change}（{pct}）"]
    if new_regions:
        parts.append(
            f"新增归属区域 {'、'.join(new_regions)} 带入收入 {new_region_total:.2f}，单列不计入可比口径"
        )
    else:
        parts.append("本期无新增归属区域")
    return {
        "summary": "；".join(parts),
        "comparable_delta": round(delta, 2),
        "comparable_delta_pct": delta_pct,
        "new_region_income": round(new_region_total, 2),
    }


def reset_comparisons() -> None:
    """测试辅助：清空留档并重置编号。"""
    global _SEQUENCE
    _COMPARISONS.clear()
    _SEQUENCE = itertools.count(1)
