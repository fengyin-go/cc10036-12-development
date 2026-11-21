<template>
  <section class="period-panel" data-testid="period-compare-panel">
    <header class="period-head">
      <div>
        <h3>跨期区域收入对照</h3>
        <p class="page-desc">
          按数据访问范围逐期解析可读区域，只对两期都能读的分区做同口径收入对照；
          新增授权区域单列，只读区域不展开城市明细。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="period-compare-run" type="button" :disabled="loading" @click="runCompare">
          {{ loading ? '计算中…' : '生成跨期对照' }}
        </button>
        <button class="btn" data-testid="period-compare-history" type="button" :disabled="!comparison" @click="reloadHistory">
          回查历史快照
        </button>
      </div>
    </header>

    <form class="period-form" @submit.prevent="runCompare">
      <label>
        <span>分析员</span>
        <select v-model="form.principal" data-testid="period-principal">
          <option v-for="name in principals" :key="name" :value="name">{{ name }}</option>
        </select>
      </label>
      <label>
        <span>上一期</span>
        <select v-model="form.period_a" data-testid="period-a">
          <option v-for="period in periods" :key="period.value" :value="period.value">
            {{ period.label }}
          </option>
        </select>
      </label>
      <label>
        <span>本期</span>
        <select v-model="form.period_b" data-testid="period-b">
          <option v-for="period in periods" :key="period.value" :value="period.value">
            {{ period.label }}
          </option>
        </select>
      </label>
      <label>
        <span>查询模板</span>
        <select v-model="form.template_id" data-testid="period-template">
          <option :value="null">不限模板（按默认数据域）</option>
          <option v-for="item in templateOptions" :key="item.id" :value="Number(item.id)">
            {{ item['模板名称'] }}
          </option>
        </select>
      </label>
    </form>

    <p v-if="errorMessage" class="error-text" data-testid="period-error">{{ errorMessage }}</p>

    <template v-if="comparison">
      <article class="period-headline" data-testid="period-headline">
        <strong>{{ comparison.headline.summary }}</strong>
        <span class="period-meta">
          对照编号 {{ comparison.comparison_id }} · 生成于 {{ comparison.generated_at }}
          <template v-if="comparison.history"> · 历史快照</template>
        </span>
      </article>

      <div class="period-grid">
        <article
          v-for="period in orderedPeriods"
          :key="period.period"
          class="period-card"
          :data-testid="`period-card-${period.period}`"
        >
          <header>
            <h4>{{ period.label }}</h4>
            <span class="period-range">{{ period.start }} ~ {{ period.end }}</span>
          </header>
          <p class="period-total">
            区域内收入合计 <strong>{{ formatMoney(period.revenue_total) }}</strong>
          </p>
          <p class="period-participate" :data-testid="`period-participating-${period.period}`">
            本期实际参与计算的分区：{{ period.participating_regions.join('、') || '无' }}
          </p>
          <table class="data-table compact">
            <thead>
              <tr><th>区域</th><th>可读性</th><th>授权级别</th><th>纳入原因</th><th>收入</th></tr>
            </thead>
            <tbody>
              <tr
                v-for="region in period.regions"
                :key="region.region"
                :data-testid="`period-region-${period.period}-${region.region}`"
                :data-readable="String(region.readable)"
              >
                <td>{{ region.region }}</td>
                <td>{{ region.readable ? (region.comparable ? '参与可比' : (region.role || '参与')) : '未参与' }}</td>
                <td>{{ region.access_level }}</td>
                <td>{{ region.reason }}</td>
                <td>{{ region.revenue === null || region.revenue === undefined ? '—' : formatMoney(region.revenue) }}</td>
              </tr>
            </tbody>
          </table>
          <p v-if="period.read_only_regions.length" class="period-note">
            只读区域：{{ period.read_only_regions.join('、') }}（仅展示区域汇总，不展开城市明细）
          </p>
        </article>
      </div>

      <div class="period-summary">
        <div class="stat-row">
          <article class="stat-card">
            <span class="stat-label">可比口径收入变动</span>
            <strong class="stat-value">{{ formatMoney(comparison.comparable_revenue.delta) }}</strong>
            <span class="period-meta">{{ formatPct(comparison.comparable_revenue.delta_pct) }}</span>
          </article>
          <article class="stat-card">
            <span class="stat-label">新增归属带入收入</span>
            <strong class="stat-value">{{ formatMoney(comparison.headline.new_region_income) }}</strong>
            <span class="period-meta">{{ comparison.new_regions.join('、') || '无新增区域' }}</span>
          </article>
          <article class="stat-card">
            <span class="stat-label">可比口径区域</span>
            <strong class="stat-value">{{ comparison.comparable_regions.join('、') || '无' }}</strong>
            <span class="period-meta">{{ comparison.comparable_revenue.caliber }}</span>
          </article>
        </div>
      </div>

      <section class="period-share">
        <label>
          <span>共享给</span>
          <select v-model="shareViewer" data-testid="period-share-viewer">
            <option v-for="name in viewers" :key="name" :value="name">{{ name }}</option>
          </select>
        </label>
        <button class="btn" data-testid="period-share-run" type="button" :disabled="sharing" @click="runShare">
          {{ sharing ? '生成中…' : '生成共享视图' }}
        </button>
        <span v-if="sharedMessage" class="period-meta" data-testid="period-share-note">{{ sharedMessage }}</span>
      </section>

      <table v-if="sharedView" class="data-table compact" data-testid="period-share-table">
        <thead>
          <tr><th>查看者可见区域</th><th>本期参与计算</th><th>区域明细</th></tr>
        </thead>
        <tbody>
          <tr v-for="period in sharedPeriods" :key="period.period">
            <td>{{ period.regions.map((item) => item.region).join('、') || '无可读区域' }}</td>
            <td>{{ period.participating_regions.join('、') || '无' }}</td>
            <td>{{ period.region_detail.length }} 条城市明细</td>
          </tr>
        </tbody>
      </table>

      <section v-if="grants.length" class="period-grants" data-testid="period-grants">
        <h4>历史授权区间（可回查当时允许读取的数据）</h4>
        <table class="data-table compact">
          <thead>
            <tr><th>规则</th><th>状态</th><th>级别</th><th>区域</th><th>生效区间</th></tr>
          </thead>
          <tbody>
            <tr v-for="grant in grants" :key="`${grant.rule_id}-${grant.data_source}`">
              <td>{{ grant.rule_name }}</td>
              <td>{{ grant.status }}</td>
              <td>{{ grant.access_level }}</td>
              <td>{{ grant.regions.join('、') }}</td>
              <td>{{ grant.period_start }} ~ {{ grant.period_end }}</td>
            </tr>
          </tbody>
        </table>
      </section>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import {
  comparePeriods,
  loadComparison,
  loadComparisonGrants,
  shareComparison,
  type HistoricalGrant,
} from '@/api/backend-service'
import type { PeriodComparison, PeriodView } from '@/data/types'

const props = defineProps<{ templates: { id: number | string; [key: string]: unknown }[] }>()

const principals = ['分析师A', '分析师B', '管理组', '实习生C']
const viewers = ['分析师A', '分析师B', '管理组', '实习生C']
// 与后端 PERIOD_PRESETS 对齐的相邻两期；统计区间由后端复核。
const periods = [
  { value: '2026Q3', label: '2026Q3（上一季）' },
  { value: '2026Q4', label: '2026Q4（本季）' },
]
const templateOptions = computed(() => props.templates)

const form = ref<{
  principal: string
  period_a: string
  period_b: string
  template_id: number | null
}>({
  principal: '分析师A',
  period_a: '2026Q3',
  period_b: '2026Q4',
  template_id: props.templates[0] ? Number(props.templates[0].id) : null,
})
const comparison = ref<PeriodComparison | null>(null)
const sharedView = ref<PeriodComparison | null>(null)
const shareViewer = ref('分析师B')
const sharedMessage = ref('')
const grants = ref<HistoricalGrant[]>([])
const loading = ref(false)
const sharing = ref(false)
const errorMessage = ref('')

const orderedPeriods = computed<PeriodView[]>(() => {
  if (!comparison.value) return []
  return comparison.value.period_order
    .map((key) => comparison.value?.periods[key])
    .filter((item): item is PeriodView => Boolean(item))
})

const sharedPeriods = computed<PeriodView[]>(() => {
  if (!sharedView.value) return []
  return sharedView.value.period_order
    .map((key) => sharedView.value?.periods[key])
    .filter((item): item is PeriodView => Boolean(item))
})

watch(
  () => props.templates,
  (list) => {
    if (!form.value.template_id && list.length) {
      form.value.template_id = Number(list[0].id)
    }
  },
)

function formatMoney(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

function formatPct(value: number | null | undefined): string {
  if (value === null || value === undefined) return '基期无可比收入'
  const sign = value > 0 ? '+' : ''
  return `${sign}${value.toFixed(2)}%`
}

async function runCompare() {
  loading.value = true
  errorMessage.value = ''
  sharedView.value = null
  sharedMessage.value = ''
  try {
    comparison.value = await comparePeriods({
      principal: form.value.principal,
      period_a: form.value.period_a,
      period_b: form.value.period_b,
      template_id: form.value.template_id,
    })
    await reloadGrants()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '跨期对照生成失败'
  } finally {
    loading.value = false
  }
}

async function reloadHistory() {
  if (!comparison.value) return
  errorMessage.value = ''
  try {
    comparison.value = await loadComparison(comparison.value.comparison_id)
    await reloadGrants()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '历史对照读取失败'
  }
}

async function reloadGrants() {
  if (!comparison.value) return
  try {
    const payload = await loadComparisonGrants(comparison.value.comparison_id)
    const merged: HistoricalGrant[] = []
    for (const list of Object.values(payload.grants)) merged.push(...list)
    grants.value = merged
  } catch {
    grants.value = []
  }
}

async function runShare() {
  if (!comparison.value) return
  sharing.value = true
  errorMessage.value = ''
  try {
    sharedView.value = await shareComparison(comparison.value.comparison_id, shareViewer.value)
    sharedMessage.value = sharedView.value.notes || ''
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '共享视图生成失败'
  } finally {
    sharing.value = false
  }
}
</script>
