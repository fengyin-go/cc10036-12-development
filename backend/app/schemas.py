"""通用接口契约；模块字段由领域服务持有，不再为每个模块复制一份模型类。"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageResult(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    size: int = 20


class ActionResult(BaseModel):
    ok: bool
    message: str
    entry: dict[str, Any] | None = None


class EntryPayload(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    remark: str | None = None


class ActionPayload(BaseModel):
    action: str
    remark: str | None = None


class PeriodComparePayload(BaseModel):
    """跨期收入对照入参：授权主体、两期和可选的查询模板/数据域。"""

    principal: str = "分析师A"
    period_a: str = "2026Q3"
    period_b: str = "2026Q4"
    template_id: int | None = None
    data_sources: list[str] = Field(default_factory=list)


class SharePayload(BaseModel):
    """共享报表查看者。"""

    viewer: str
