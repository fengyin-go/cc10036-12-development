/** 纯前端数据层的公共类型：与全栈版后端返回的结构保持一致，换回后端时页面不用改。 */

export type EntryRow = {
  id: number
  status: string
  pending: boolean
  abnormal: boolean
  [field: string]: string | number | boolean
}

export type ModuleMeta = {
  key: string
  name: string
  entity: string
  desc: string
  fields: string[]
  statuses: string[]
  actions: string[]
  actionTargets: Record<string, string>
  metrics: string[]
}

export type PageResult = {
  items: EntryRow[]
  total: number
  page: number
  size: number
}

export type ActionResult = {
  ok: boolean
  message: string
}

export type OverviewResult = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

/** 跨期对照：每个区域都带上当期的可读性与纳入原因，报表据此标注参与计算的分区。 */
export type PeriodRegion = {
  region: string
  readable: boolean
  access_level: string
  reason: string
  sources: string[]
  rule_ids: number[]
  rule_names: string[]
  role?: string
  revenue?: number | null
  comparable?: boolean
  detail_visible?: boolean
  detail_note?: string
}

export type PeriodView = {
  period: string
  label: string
  start: string
  end: string
  regions: PeriodRegion[]
  participating_regions: string[]
  excluded_regions: string[]
  comparable_regions_in_period: string[]
  new_regions_in_period: string[]
  read_only_regions: string[]
  region_detail: { 期间: string; 地区: string; 城市: string; 收入: number; 订单数: number }[]
  revenue_total: number
  restricted?: boolean
}

export type ComparableRevenue = {
  period_a: { period: string; revenue: number }
  period_b: { period: string; revenue: number }
  delta: number
  delta_pct: number | null
  caliber?: string
  recalculated?: boolean
}

export type PeriodComparison = {
  ok: boolean
  comparison_id: number
  generated_at: string
  principal: string
  template_id: number | null
  template_name: string
  data_sources: string[]
  period_order: [string, string]
  periods: Record<string, PeriodView>
  comparable_regions: string[]
  new_regions: string[]
  removed_regions: string[]
  comparable_revenue: ComparableRevenue
  new_region_revenue: { regions: Record<string, number>; total: number; note?: string }
  read_only_regions: Record<string, string[]>
  headline: {
    summary: string
    comparable_delta: number
    comparable_delta_pct: number | null
    new_region_income: number
  }
  history?: boolean
  history_note?: string
  viewer?: string
  shared_from?: string
  restricted_regions?: string[]
  notes?: string
}
