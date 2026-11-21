import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type {
  ActionResult,
  EntryRow,
  OverviewResult,
  PageResult,
  PeriodComparison,
} from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

export type PeriodCompareRequest = {
  principal: string
  period_a: string
  period_b: string
  template_id?: number | null
}

/** 跨期收入对照：授权由后端数据范围模块按期间解析，前端只提交统计口径。 */
export async function comparePeriods(
  payload: PeriodCompareRequest,
): Promise<PeriodComparison> {
  const response = await request('/api/slice_query/period-compare', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  const data = await response.json()
  if (!response.ok || !data.ok) throw new Error(data.message || data.detail || '跨期对照生成失败')
  return data as PeriodComparison
}

/** 回查历史对照：返回生成当时的授权快照，不受后续授权变更影响。 */
export async function loadComparison(comparisonId: number): Promise<PeriodComparison> {
  const response = await request(`/api/slice_query/period-compare/${comparisonId}`)
  const data = await response.json()
  if (!response.ok || !data.ok) throw new Error(data.message || data.detail || '历史对照读取失败')
  return data as PeriodComparison
}

/** 共享给其他角色：后端按查看者实时授权裁剪，未授权区域不下发。 */
export async function shareComparison(
  comparisonId: number,
  viewer: string,
): Promise<PeriodComparison> {
  const response = await request(`/api/slice_query/period-compare/${comparisonId}/share`, {
    method: 'POST',
    body: JSON.stringify({ viewer }),
  })
  const data = await response.json()
  if (!response.ok || !data.ok) throw new Error(data.message || data.detail || '共享视图生成失败')
  return data as PeriodComparison
}

export type HistoricalGrant = {
  rule_id: number
  rule_name: string
  status: string
  data_source: string
  principal: string
  access_level: string
  period_start: string
  period_end: string
  regions: string[]
  visible_fields: string[]
}

export async function loadComparisonGrants(
  comparisonId: number,
): Promise<{ grants: Record<string, HistoricalGrant[]> }> {
  const response = await request(`/api/slice_query/period-compare/${comparisonId}/grants`)
  const data = await response.json()
  if (!response.ok || !data.ok) throw new Error(data.message || data.detail || '历史授权读取失败')
  return data
}
