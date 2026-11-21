"""跨期区域收入对照的核心逻辑测试（stdlib unittest，直接调用领域服务）。

运行：cd backend && .venv/bin/python -m unittest discover -s tests -v
"""
from __future__ import annotations

import unittest

from app import period_compare
from app.services import service_for
from app.services.olap_scope import OlapScopeService
from app.services.slice_query import SliceQueryService


class PeriodGrantTest(unittest.TestCase):
    def setUp(self) -> None:
        self.scope = OlapScopeService()

    def test_q3_regions_resolved_from_historical_rule(self) -> None:
        grant = self.scope.resolve_period_grants(
            "分析师A", "cube_trade", "2026-07-01", "2026-09-30")
        self.assertEqual(set(grant["readable_regions"]), {"华北", "华东"})
        self.assertEqual(grant["read_only_regions"], [])

    def test_q4_adds_read_only_southwest(self) -> None:
        grant = self.scope.resolve_period_grants(
            "分析师A", "cube_trade", "2026-10-01", "2026-12-31")
        self.assertEqual(set(grant["readable_regions"]), {"华北", "华东", "西南"})
        self.assertEqual(grant["read_only_regions"], ["西南"])
        southwest = next(r for r in grant["regions"] if r["region"] == "西南")
        self.assertEqual(southwest["access_level"], "只读")
        self.assertIn(6, southwest["rule_ids"])

    def test_frozen_rule_still_counted_for_its_period(self) -> None:
        # 规则 4 现在已冻结，但 Q3 期间确实生效过，历史口径不能丢。
        grant = self.scope.resolve_period_grants(
            "分析师A", "cube_trade", "2026-07-01", "2026-09-30")
        self.assertIn(4, grant["rule_ids"])
        self.assertNotIn(5, grant["rule_ids"])

    def test_wildcard_rule_grants_all_regions(self) -> None:
        grant = self.scope.resolve_period_grants(
            "管理组", "cube_trade", "2026-10-01", "2026-12-31")
        self.assertEqual(len(grant["readable_regions"]), 4)

    def test_city_filter_maps_to_region(self) -> None:
        # 规则 1 按华东城市过滤，要折算成华东区域口径。
        grant = self.scope.resolve_period_grants(
            "分析师A", "cube_trade", "2026-07-01", "2026-09-30")
        east = next(r for r in grant["regions"] if r["region"] == "华东")
        self.assertIn(1, east["rule_ids"])


class PeriodCompareTest(unittest.TestCase):
    def setUp(self) -> None:
        period_compare.reset_comparisons()
        self.service: SliceQueryService = service_for("slice_query")  # type: ignore[assignment]

    def _compare(self, principal: str = "分析师A") -> dict:
        result = self.service.compare_periods(
            principal, "2026Q3", "2026Q4", template_id=2)
        self.assertTrue(result["ok"], result)
        return result

    def test_comparable_revenue_excludes_new_region(self) -> None:
        result = self._compare()
        self.assertEqual(result["comparable_regions"], ["华东", "华北"])
        self.assertEqual(result["new_regions"], ["西南"])
        # 可比口径只算华东 + 华北
        self.assertEqual(result["comparable_revenue"]["period_a"]["revenue"], 588000.0)
        self.assertEqual(result["comparable_revenue"]["period_b"]["revenue"], 640000.0)
        self.assertEqual(result["comparable_revenue"]["delta"], 52000.0)

    def test_new_region_revenue_reported_separately(self) -> None:
        result = self._compare()
        self.assertEqual(result["new_region_revenue"]["regions"], {"西南": 139000.0})
        self.assertEqual(result["headline"]["new_region_income"], 139000.0)

    def test_participating_regions_marked_per_period(self) -> None:
        result = self._compare()
        self.assertEqual(
            result["periods"]["2026Q3"]["participating_regions"], ["华东", "华北"])
        self.assertEqual(
            result["periods"]["2026Q4"]["participating_regions"], ["华东", "华北", "西南"])
        self.assertIn("华南", result["periods"]["2026Q4"]["excluded_regions"])

    def test_read_only_region_hides_city_detail(self) -> None:
        result = self._compare()
        q4 = result["periods"]["2026Q4"]
        self.assertEqual(q4["read_only_regions"], ["西南"])
        self.assertFalse(any(row["地区"] == "西南" for row in q4["region_detail"]))
        self.assertTrue(any(row["地区"] == "华东" for row in q4["region_detail"]))

    def test_every_region_carries_inclusion_reason(self) -> None:
        result = self._compare()
        for period in result["periods"].values():
            for region in period["regions"]:
                self.assertTrue(region["reason"])
                self.assertIn("rule_ids", region)

    def test_history_snapshot_is_stable(self) -> None:
        created = self._compare()
        stored = self.service.get_period_comparison(created["comparison_id"])
        self.assertTrue(stored["history"])
        self.assertEqual(
            stored["periods"]["2026Q4"]["source_rule_ids"],
            created["periods"]["2026Q4"]["source_rule_ids"],
        )
        # 修改返回对象不应污染留档。
        stored["comparable_regions"].append("污染")
        again = self.service.get_period_comparison(created["comparison_id"])
        self.assertNotIn("污染", again["comparable_regions"])

    def test_share_does_not_leak_unauthorized_regions(self) -> None:
        created = self._compare()
        shared = self.service.share_period_comparison(
            created["comparison_id"], "分析师B")
        # 分析师B 只有华北、华东授权：区域清单、收入明细、参与计算说明都不得出现其他区域。
        self.assertEqual(shared["comparable_regions"], ["华东", "华北"])
        self.assertEqual(shared["new_regions"], [])
        self.assertEqual(set(shared["restricted_regions"]), {"华南", "西南"})
        for period in shared["periods"].values():
            self.assertEqual(
                {r["region"] for r in period["regions"]}, {"华东", "华北"})
            self.assertTrue(
                all(row["地区"] in {"华东", "华北"} for row in period["region_detail"]))
        self.assertNotIn("西南", str(shared["periods"]))

    def test_share_respects_viewer_read_only(self) -> None:
        created = self._compare()
        # 分析师A 自己看共享视图：西南只有只读权，不能展开城市明细。
        shared = self.service.share_period_comparison(
            created["comparison_id"], "分析师A")
        q4 = shared["periods"]["2026Q4"]
        self.assertFalse(any(row["地区"] == "西南" for row in q4["region_detail"]))
        southwest = next(r for r in q4["regions"] if r["region"] == "西南")
        self.assertFalse(southwest["detail_visible"])

    def test_share_recalculates_comparable_caliber(self) -> None:
        created = self._compare()
        # 分析师A 自己看：可比区域仍是华东+华北，口径不变。
        same = self.service.share_period_comparison(
            created["comparison_id"], "分析师A")
        self.assertFalse(same["comparable_revenue"]["recalculated"])
        self.assertEqual(same["restricted_regions"], ["华南"])

        # 分析师B 看不到西南，新增归属区域被裁掉，共享口径与原始对照不同。
        narrowed = self.service.share_period_comparison(
            created["comparison_id"], "分析师B")
        self.assertTrue(narrowed["comparable_revenue"]["recalculated"])
        self.assertEqual(narrowed["new_regions"], [])
        self.assertEqual(narrowed["comparable_revenue"]["delta"], 52000.0)

    def test_share_respects_viewer_read_only(self) -> None:
        created = self._compare()
        # 分析师A 自己看共享视图：西南只有只读权，不能展开城市明细。
        shared = self.service.share_period_comparison(
            created["comparison_id"], "分析师A")
        q4 = shared["periods"]["2026Q4"]
        self.assertFalse(any(row["地区"] == "西南" for row in q4["region_detail"]))
        southwest = next(r for r in q4["regions"] if r["region"] == "西南")
        self.assertFalse(southwest["detail_visible"])

    def test_invalid_inputs_rejected(self) -> None:
        bad_period = self.service.compare_periods("分析师A", "2026Q2", "2026Q4")
        self.assertFalse(bad_period["ok"])
        same_period = self.service.compare_periods("分析师A", "2026Q3", "2026Q3")
        self.assertFalse(same_period["ok"])
        missing_template = self.service.compare_periods(
            "分析师A", "2026Q3", "2026Q4", template_id=999)
        self.assertFalse(missing_template["ok"])

    def test_comparison_listing_filters_by_principal(self) -> None:
        self._compare("分析师A")
        self.service.compare_periods("分析师B", "2026Q3", "2026Q4")
        items = self.service.list_period_comparisons("分析师A")["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["principal"], "分析师A")


class SliceQueryRegressionTest(unittest.TestCase):
    def test_existing_slice_query_still_works(self) -> None:
        service = SliceQueryService()
        data = [
            {"日期": "本周", "GMV": 52000},
            {"日期": "本周", "GMV": 8000},
            {"日期": "上周", "GMV": 99000},
        ]
        # 模板 2：日期=本周，GMV>=10000，按 GMV 倒序。
        result = service.execute_slice_query(2, data)
        self.assertTrue(result["ok"])
        self.assertEqual(result["total_rows"], 1)
        self.assertEqual(result["data"][0]["GMV"], 52000)


if __name__ == "__main__":
    unittest.main()
