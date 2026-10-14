
<script setup>
/**
 * 数据大屏（阶段 6 可选加分）
 * 一次性拉取 /api/stats/overview，用 ECharts 渲染：
 *   近 7 天消息趋势 / Agent 调用分布 / 文档类型分布
 * 顶部 KPI 卡片 + 最近会话与模型事件列表。每 10s 自动刷新。
 */
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import * as echarts from 'echarts'
import { getStatsOverview } from '../api'

const counts = reactive({
  documents: 0, chunks: 0, conversations: 0, messages: 0,
  users: 0, agent_logs: 0, images: 0,
})
const todayMessages = ref(0)
const recentConversations = ref([])
const modelEvents = ref([])
const updatedAt = ref('')
const loading = ref(false)

const trendEl = ref(null)
const agentEl = ref(null)
const typeEl = ref(null)
let trendChart = null
let agentChart = null
let typeChart = null

function initCharts() {
  if (trendEl.value) trendChart = echarts.init(trendEl.value)
  if (agentEl.value) agentChart = echarts.init(agentEl.value)
  if (typeEl.value) typeChart = echarts.init(typeEl.value)
}

function renderTrend(days) {
  if (!trendChart) return
  trendChart.setOption({
    tooltip: { trigger: 'axis' },
    grid: { left: 40, right: 20, top: 20, bottom: 28 },
    xAxis: { type: 'category', data: days.map((d) => d.date), boundaryGap: false },
    yAxis: { type: 'value', minInterval: 1 },
    series: [{
      name: '消息数', type: 'line', smooth: true, symbolSize: 6,
      data: days.map((d) => d.count),
      itemStyle: { color: '#409eff' },
      areaStyle: { color: 'rgba(64,158,255,0.18)' },
    }],
  })
}

function renderAgents(calls) {
  if (!agentChart) return
  const entries = Object.entries(calls || {}).sort((a, b) => a[1] - b[1])
  agentChart.setOption({
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 110, right: 24, top: 16, bottom: 24 },
    xAxis: { type: 'value', minInterval: 1 },
    yAxis: { type: 'category', data: entries.map((e) => e[0]) },
    series: [{
      type: 'bar', data: entries.map((e) => e[1]), barWidth: 14,
      itemStyle: { color: '#6a5cff', borderRadius: [0, 6, 6, 0] },
    }],
  })
}

function renderTypes(types) {
  if (!typeChart) return
  const data = Object.entries(types || {}).map(([name, value]) => ({ name, value }))
  typeChart.setOption({
    tooltip: { trigger: 'item' },
    legend: { bottom: 0, icon: 'circle' },
    series: [{
      type: 'pie', radius: ['42%', '68%'], center: ['50%', '44%'],
      avoidLabelOverlap: true, data,
      label: { formatter: '{b}: {c}' },
    }],
  })
}

async function load() {
  loading.value = true
  try {
    const data = await getStatsOverview(true)
    if (data && data.counts) {
      Object.assign(counts, data.counts)
      todayMessages.value = data.today_messages || 0
      recentConversations.value = data.recent_conversations || []
      modelEvents.value = data.model_events || []
      renderTrend(data.messages_by_day || [])
      renderAgents(data.agent_calls || {})
      renderTypes(data.doc_types || {})
      updatedAt.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
    }
  } catch (e) {
    // 后端未启动时静默，避免大屏页报错刷屏
  } finally {
    loading.value = false
  }
}

function resizeCharts() {
  trendChart && trendChart.resize()
  agentChart && agentChart.resize()
  typeChart && typeChart.resize()
}

let timer = null

onMounted(async () => {
  initCharts()
  await load()
  window.addEventListener('resize', resizeCharts)
  timer = setInterval(load, 10000)
})

onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
  window.removeEventListener('resize', resizeCharts)
  trendChart && trendChart.dispose()
  agentChart && agentChart.dispose()
  typeChart && typeChart.dispose()
})
</script>

<template>
  <div class="page">
    <div class="page-scroll">
      <div class="dash-head">
        <div class="dash-title">数据大屏</div>
        <div class="muted">
          数据截至 {{ updatedAt || '—' }}
          <el-tag v-if="loading" size="small" type="info" effect="plain">刷新中</el-tag>
          <el-button size="small" text type="primary" @click="load">立即刷新</el-button>
        </div>
      </div>

      <!-- KPI 卡片 -->
      <div class="stat-grid mb">
        <div class="stat-card">
          <div class="label">知识库文档</div>
          <div class="value">{{ counts.documents }}</div>
        </div>
        <div class="stat-card">
          <div class="label">文本块 Chunks</div>
          <div class="value">{{ counts.chunks }}</div>
        </div>
        <div class="stat-card">
          <div class="label">会话数</div>
          <div class="value">{{ counts.conversations }}</div>
        </div>
        <div class="stat-card">
          <div class="label">消息总数</div>
          <div class="value">{{ counts.messages }}</div>
        </div>
        <div class="stat-card">
          <div class="label">今日消息</div>
          <div class="value">{{ todayMessages }}</div>
        </div>
        <div class="stat-card">
          <div class="label">Agent 日志</div>
          <div class="value">{{ counts.agent_logs }}</div>
        </div>
        <div class="stat-card">
          <div class="label">绘图记录</div>
          <div class="value">{{ counts.images }}</div>
        </div>
        <div class="stat-card">
          <div class="label">注册用户</div>
          <div class="value">{{ counts.users }}</div>
        </div>
      </div>

      <!-- 图表 -->
      <div class="chart-grid mb">
        <div class="panel">
          <div class="panel-title">近 7 天消息量 <span class="sub">messages / day</span></div>
          <div ref="trendEl" class="chart"></div>
        </div>
        <div class="panel">
          <div class="panel-title">Agent 调用次数 <span class="sub">by agent</span></div>
          <div ref="agentEl" class="chart"></div>
        </div>
      </div>

      <div class="chart-grid mb">
        <div class="panel">
          <div class="panel-title">文档类型分布 <span class="sub">by extension</span></div>
          <div ref="typeEl" class="chart"></div>
        </div>
        <div class="panel">
          <div class="panel-title">最近会话 <span class="sub">recent</span></div>
          <div v-if="!recentConversations.length" class="empty-hint">暂无会话</div>
          <ul v-else class="mini-list">
            <li v-for="c in recentConversations" :key="c.conversation_id">
              <span class="mini-title">{{ c.title || '新对话' }}</span>
              <span class="muted">{{ c.created_at }}</span>
            </li>
          </ul>
        </div>
      </div>

      <div class="panel">
        <div class="panel-title">模型加载 / 卸载事件 <span class="sub">model_logs</span></div>
        <div v-if="!modelEvents.length" class="empty-hint">暂无记录</div>
        <ul v-else class="mini-list">
          <li v-for="(e, i) in modelEvents" :key="i">
            <span class="mini-title">{{ e.event }}</span>
            <span class="muted">{{ e.memory_mb ? e.memory_mb + ' MB' : '' }} · {{ e.created_at }}</span>
          </li>
        </ul>
      </div>
    </div>
  </div>
</template>

<style scoped>
.mb {
  margin-bottom: 14px;
}

.dash-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}

.dash-title {
  font-size: 18px;
  font-weight: 600;
}

.chart-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
}

.chart {
  width: 100%;
  height: 260px;
}

.mini-list {
  list-style: none;
  margin: 0;
  padding: 0;
  max-height: 260px;
  overflow-y: auto;
}

.mini-list li {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 2px;
  border-bottom: 1px dashed var(--app-border);
}

.mini-list li:last-child {
  border-bottom: none;
}

.mini-title {
  font-size: 13px;
  color: var(--app-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (max-width: 900px) {
  .chart-grid {
    grid-template-columns: 1fr;
  }
}
</style>
