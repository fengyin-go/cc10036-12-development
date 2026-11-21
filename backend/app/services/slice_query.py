"""切片查询领域服务：多维立方消费，按维度切片、过滤和排序，跨期收入对照。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store
from app import period_compare
from app.revenue_facts import DEFAULT_DATA_SOURCES


class SliceQueryService(BaseModuleService):
    MODULE = "slice_query"
    ENTITY = "查询模板"
    OPERATION_TERM = "聚合"
    LIST_FIELDS = ["模板名称", "数据立方", "查询维值", "过滤条件", "排序规则", "返回行数", "缓存TTL"]
    REQUIRED_FIELDS = ["模板名称", "数据立方", "查询维值"]
    STATUS_ORDER = ["草稿", "已保存", "已发布", "已过期"]
    ACTION_RULES = {"保存模板": "已保存", "发布模板": "已发布", "标记过期": "已过期"}
    NEGATIVE_ACTIONS = ["标记过期"]

    # ---- 业务方法 ----

    def execute_slice_query(
        self, template_id: int, data: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """执行切片查询：根据模板配置对数据筛选、排序和截断。"""
        template = store.find(self.MODULE, template_id)
        if template is None:
            return {"ok": False, "message": f"查询模板 {template_id} 不存在"}

        dim_values_raw = str(template.get("查询维值", ""))
        filter_expr = str(template.get("过滤条件", ""))
        sort_expr = str(template.get("排序规则", ""))
        row_limit = _parse_int(template.get("返回行数"), 100)
        cache_ttl = _parse_int(template.get("缓存TTL"), 300)

        # 按维值筛选
        if dim_values_raw:
            conditions = [
                cond.strip().split("=", 1)
                for cond in dim_values_raw.split(";") if "=" in cond
            ]
            filtered: list[dict[str, Any]] = []
            for row in data:
                if all(
                    str(row.get(field.strip(), "")) == val.strip()
                    for field, val in conditions
                ):
                    filtered.append(row)
        else:
            filtered = list(data)

        # 应用过滤条件
        if filter_expr and filtered:
            filtered = _apply_slice_filter(filtered, filter_expr)

        # 排序
        if sort_expr and filtered:
            sort_parts = sort_expr.rsplit(":", 1)
            field = sort_parts[0].strip()
            reverse = sort_parts[1].strip().upper() == "DESC" if len(sort_parts) > 1 else False
            try:
                filtered.sort(
                    key=lambda r: float(r.get(field, 0)),
                    reverse=reverse,
                )
            except (ValueError, TypeError):
                filtered.sort(key=lambda r: str(r.get(field, "")), reverse=reverse)

        total = len(filtered)
        result = filtered[:row_limit]

        return {
            "ok": True,
            "template_name": str(template.get("模板名称", "")),
            "result_rows": len(result),
            "total_rows": total,
            "row_limit": row_limit,
            "cache_ttl": cache_ttl,
            "data": result,
        }

    def get_templates_for_cube(self, cube_name: str) -> list[dict[str, Any]]:
        """获取某立方关联的所有查询模板（供即席导出消费）。"""
        return [
            row for row in store.rows(self.MODULE)
            if row.get("数据立方") == cube_name and row.get("status") == "已发布"
        ]

    # ---- 按数据访问范围做跨期收入对照 ----

    def compare_periods(
        self,
        principal: str,
        period_a: str,
        period_b: str,
        template_id: int | None = None,
        data_sources: list[str] | None = None,
    ) -> dict[str, Any]:
        """由切片查询发起跨期对照，授权结果直接取数据范围模块。

        切片查询只负责确定统计对象（查询模板/数据立方），区域能不能读、读的是
        只读还是读写，统一交给数据范围模块按期间回查，避免两处授权口径漂移。
        """
        from app.services import service_for

        scope_svc = service_for("olap_scope")
        if scope_svc is None:
            return {"ok": False, "message": "数据范围服务未就绪"}

        template_name = ""
        selected_sources = list(data_sources or [])
        if template_id is not None:
            template = store.find(self.MODULE, template_id)
            if template is None:
                return {"ok": False, "message": f"查询模板 {template_id} 不存在"}
            template_name = str(template.get("模板名称", ""))
            if not selected_sources:
                selected_sources = _sources_for_template(template)
        if not selected_sources:
            selected_sources = list(DEFAULT_DATA_SOURCES)

        try:
            return period_compare.build_comparison(
                principal=principal,
                period_a=period_a,
                period_b=period_b,
                data_sources=selected_sources,
                scope_service=scope_svc,
                template_id=template_id,
                template_name=template_name,
            )
        except period_compare.PeriodCompareError as error:
            return {"ok": False, "message": str(error)}

    def get_period_comparison(self, comparison_id: int) -> dict[str, Any]:
        """回查历史对照：返回生成当时的授权快照。"""
        record = period_compare.get_comparison(comparison_id)
        if record is None:
            return {"ok": False, "message": f"跨期对照 {comparison_id} 不存在"}
        return record

    def list_period_comparisons(self, principal: str | None = None) -> dict[str, Any]:
        """列出留档的跨期对照。"""
        return {"ok": True, "items": period_compare.list_comparisons(principal)}

    def share_period_comparison(self, comparison_id: int, viewer: str) -> dict[str, Any]:
        """按查看者实时授权生成共享视图，不放开未授权区域。"""
        from app.services import service_for

        scope_svc = service_for("olap_scope")
        if scope_svc is None:
            return {"ok": False, "message": "数据范围服务未就绪"}
        view = period_compare.shared_view(comparison_id, viewer, scope_svc)
        if view is None:
            return {"ok": False, "message": f"跨期对照 {comparison_id} 不存在"}
        return view

    def get_comparison_grants(self, comparison_id: int) -> dict[str, Any]:
        """汇总某次对照用到的历史授权区间，便于回查当时允许读取的数据。"""
        from app.services import service_for

        record = period_compare.get_comparison(comparison_id)
        if record is None:
            return {"ok": False, "message": f"跨期对照 {comparison_id} 不存在"}
        scope_svc = service_for("olap_scope")
        if scope_svc is None:
            return {"ok": False, "message": "数据范围服务未就绪"}

        principal = str(record.get("principal", ""))
        grants: dict[str, list[dict[str, Any]]] = {}
        for source in record.get("data_sources", []):
            grants[source] = scope_svc.list_historical_grants(principal, source)
        return {
            "ok": True,
            "comparison_id": comparison_id,
            "principal": principal,
            "periods": record.get("period_order"),
            "data_sources": record.get("data_sources"),
            "grants": grants,
            "notes": "返回该分析员在各数据域上的全部历史授权区间，包含已冻结/已撤销的旧规则",
        }

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        base["artifact"] = "查询模板数据集"
        base["published_templates"] = sum(
            1 for row in store.rows(self.MODULE) if row.get("status") == "已发布"
        )
        return base


def _parse_int(val: Any, default: int) -> int:
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


# 收入由交易域提供金额、由行为域提供区域归属，两域缺一不能算可比口径。
# 模板上用 `数据域=cube_trade,cube_user_behavior` 可以显式覆盖默认的两个域。
def _sources_for_template(template: dict[str, Any]) -> list[str]:
    """从查询模板推导需要校验的数据域；模板写明 `数据域=` 时优先采用。"""
    dim_values = str(template.get("查询维值", ""))
    for cond in dim_values.split(";"):
        if "=" not in cond:
            continue
        field, value = cond.split("=", 1)
        if field.strip() == "数据域":
            declared = [item.strip() for item in value.split(",") if item.strip()]
            if declared:
                return declared
    return list(DEFAULT_DATA_SOURCES)


def _apply_slice_filter(
    rows: list[dict[str, Any]], filter_expr: str,
) -> list[dict[str, Any]]:
    """解析过滤表达式并应用。支持 field>value; field<value 等。"""
    results = list(rows)
    for cond_part in filter_expr.split(";"):
        cond_part = cond_part.strip()
        if not cond_part:
            continue
        for op in (">=", "<=", "!=", ">", "<", "="):
            if op in cond_part:
                field, val = cond_part.split(op, 1)
                field = field.strip()
                val = val.strip()
                results = _filter_by_op(results, field, op, val)
                break
    return results


def _filter_by_op(
    rows: list[dict[str, Any]], field: str, op: str, val: str,
) -> list[dict[str, Any]]:
    try:
        val_num = float(val)
        is_numeric = True
    except ValueError:
        is_numeric = False

    if is_numeric:
        return [
            r for r in rows
            if _compare_num(
                float(r.get(field, 0)) if r.get(field) is not None else 0,
                val_num, op,
            )
        ]
    val_lower = val.lower()
    return [
        r for r in rows
        if _compare_str(str(r.get(field, "")).lower(), val_lower, op)
    ]


def _compare_num(a: float, b: float, op: str) -> bool:
    if op == ">":
        return a > b
    if op == "<":
        return a < b
    if op == ">=":
        return a >= b
    if op == "<=":
        return a <= b
    if op == "!=":
        return a != b
    return a == b


def _compare_str(a: str, b: str, op: str) -> bool:
    if op == "!=":
        return a != b
    if op == ">":
        return a > b
    if op == "<":
        return a < b
    return b in a