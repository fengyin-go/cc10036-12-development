"""统一资源接口：查询、导出、登记和动作按模块 key 分派到领域服务。"""
from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas import (
    ActionPayload,
    ActionResult,
    EntryPayload,
    PageResult,
    PeriodComparePayload,
    SharePayload,
)
from app.services import service_for

router = APIRouter(prefix="/api", tags=["业务资源"])


def require_service(module: str):
    service = service_for(module)
    if service is None:
        raise HTTPException(status_code=404, detail=f"未知业务模块：{module}")
    return service


# ---- 切片查询 → 数据范围：跨期区域收入对照 ----
# 必须在 /{module}/{entry_id} 之前注册，否则 "period-compare" 会被当成 entry_id。

@router.post("/slice_query/period-compare")
def create_period_comparison(payload: PeriodComparePayload) -> dict[str, Any]:
    """按数据访问范围计算两期区域收入对照，并留档当期可读区域。"""
    service = require_service("slice_query")
    result = service.compare_periods(
        principal=payload.principal,
        period_a=payload.period_a,
        period_b=payload.period_b,
        template_id=payload.template_id,
        data_sources=payload.data_sources or None,
    )
    if not result.get("ok") and "不存在" not in str(result.get("message", "")):
        raise HTTPException(status_code=400, detail=result.get("message"))
    return result


@router.get("/slice_query/period-compare")
def list_period_comparisons(principal: str | None = Query(default=None)) -> dict[str, Any]:
    """列出已留档的跨期对照。"""
    return require_service("slice_query").list_period_comparisons(principal)


@router.get("/slice_query/period-compare/{comparison_id}")
def get_period_comparison(comparison_id: int) -> dict[str, Any]:
    """回查历史对照：返回生成当时的授权快照。"""
    result = require_service("slice_query").get_period_comparison(comparison_id)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("message"))
    return result


@router.get("/slice_query/period-compare/{comparison_id}/grants")
def get_period_comparison_grants(comparison_id: int) -> dict[str, Any]:
    """回查对照用到的历史授权区间（含已冻结规则），支撑历史口径复核。"""
    result = require_service("slice_query").get_comparison_grants(comparison_id)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("message"))
    return result


@router.post("/slice_query/period-compare/{comparison_id}/share")
def share_period_comparison(comparison_id: int, payload: SharePayload) -> dict[str, Any]:
    """共享给其他角色：按查看者实时授权裁剪区域，未授权区域不呈现明细。"""
    result = require_service("slice_query").share_period_comparison(
        comparison_id, payload.viewer)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("message"))
    return result


@router.get("/{module}", response_model=PageResult[dict])
def list_entries(
    module: str,
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> PageResult[dict]:
    service = require_service(module)
    filters = {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in service.LIST_FIELDS and value.strip()
    }
    items, total = service.list_entries(
        filters=filters, keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{module}/export")
def export_entries(
    module: str,
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> StreamingResponse:
    service = require_service(module)
    filters = {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in service.LIST_FIELDS and value.strip()
    }
    items, _ = service.list_entries(
        filters=filters, keyword=keyword, status=status, page=1, size=1_000_000)
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["id", *service.LIST_FIELDS, "status"])
    for item in items:
        values = [item.get("id"), *(item.get(field, "") for field in service.LIST_FIELDS),
                  item.get("status", "")]
        writer.writerow([
            "'" + str(value) if str(value or "").startswith(("=", "+", "-", "@")) else value
            for value in values
        ])
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{module}.csv"'},
    )


@router.get("/{module}/{entry_id}", response_model=dict)
def get_entry(module: str, entry_id: int) -> dict[str, Any]:
    service = require_service(module)
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"{service.ENTITY} {entry_id} 不存在或已归档")
    return entry


@router.post("/{module}", response_model=ActionResult)
def create_entry(module: str, payload: EntryPayload) -> ActionResult:
    service = require_service(module)
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message=f"{service.ENTITY}已登记", entry=entry)


@router.post("/{module}/{entry_id}/actions", response_model=ActionResult)
def run_action(module: str, entry_id: int, payload: ActionPayload) -> ActionResult:
    service = require_service(module)
    entry, message = service.run_action(entry_id, payload.action.strip())
    return ActionResult(ok=entry is not None, message=message, entry=entry)
