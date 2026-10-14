<script setup>
/**
 * 系统状态页
 * - 模型是否加载 / 是否生成中 / worker 是否存活
 * - 内存占用（MB）、worker_pid、空闲秒数、距离自动卸载剩余秒数
 * - 手动加载 / 卸载模型
 * - ECharts 仪表盘展示内存 + 内存趋势折线
 * - 仪表盘统计（文档数、消息数、会话数、今日消息、各 Agent 调用次数）
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import * as echarts from 'echarts'
import { getDashboard, getModelStatus, loadModel, unloadModel } from '../api'
import { session, setModelStatus } from '../store/session'

defineOptions({ name: 'SystemStatusView' })

const IDLE_TIMEOUT = 180 // 与后端 config.yaml 的模型空闲卸载秒数保持一致
const POLL_INTERVAL = 3000
const MAX_SAMPLES = 60

const status = ref({
  loaded: false,
  generating: false,
  worker_alive: false,
  memory_mb: 0,
  worker_pid: null,
  idle_seconds: null,
  remaining_before_unload: null,
  error: null,
  active_provider: 'local',
  remote: false,
})
const dashboard = ref({
  documents: 0,
  messages: 0,
  conversations: 0,
  today_messages: 0,
  agent_calls: {},
})
const autoRefresh = ref(true)
const loadingStatus = ref(false)
const loadingDashboard = ref(false)
const acting = ref('')
const lastUpdated = ref('')
const statusError = ref('')
const dashboardError = ref('')
const memorySamples = ref([])

const gaugeEl = ref(null)
const trendEl = ref(null)
const agentEl = ref(null)
let gaugeChart = null
let trendChart = null
let agentChart = null
let pollTimer = null

/* ------------------------------------------------------------------ 计算 */

const memoryMb = computed(() => Number(status.value.memory_mb || 0))

const gaugeMax = computed(() => {
  const target = Math.max(memoryMb.value * 1.6, 1024)
  return Math.ceil(target / 512) * 512
})

const idleSeconds = computed(() => {
  const v = status.value.idle_seconds
  return v === null || v === undefined ? null : Number(v)
})

const remainingSeconds = computed(() => {
  const v = status.value.remaining_before_unload
  if (v === null || v === undefined) {
    if (status.value.loaded && idleSeconds.value !== null) {
      return Math.max(0, Number((IDLE_TIMEOUT - idleSeconds.value).toFixed(1)))
    }
    return null
  }
  return Number(v)
})

const unloadPercent = computed(() => {
  if (!status.value.loaded || remainingSeconds.value === null) return 0
  return Math.max(0, Math.min(100, Math.round((remainingSeconds.value / IDLE_TIMEOUT) * 100)))
})

const agentCalls = computed(() => {
  const raw = dashboard.value.agent_calls
  if (raw === null || raw === undefined) return []
  if (typeof raw === 'number') return [{ name: '全部 Agent', count: raw }]
  if (Array.isArray(raw)) {
    return raw
      .map((item) => ({ name: item.agent_name || item.name || '未知', count: Number(item.count || item.calls || 0) }))
      .sort((a, b) => b.count - a.count)
  }
  return Object.keys(raw)
    .map((key) => ({ name: key, count: Number(raw[key]) || 0 }))
    .sort((a, b) => b.count - a.count)
})

const agentCallsTotal = computed(() => agentCalls.value.reduce((sum, item) => sum + item.count, 0))

const loadedTag = computed(() => {
  if (status.value.active_provider === 'openai') return { text: '远程模型（无需本地加载）', type: 'primary' }
  if (status.value.generating) return { text: '生成中', type: 'warning' }
  if (status.value.loaded) return { text: '已加载', type: 'success' }
  if (status.value.worker_alive) return { text: '未加载（子进程就绪）', type: 'info' }
  return { text: '未加载（子进程离线）', type: 'danger' }
})

/* -------------------------------------------------------------- ECharts */

/**
 * 取得（或重建）绑定到指定 DOM 的 ECharts 实例
 * 容器带 v-if 时 DOM 会被销毁重建，必须重新 init
 */
function ensureChart(instance, el) {
  if (!el) {
    if (instance) instance.dispose()
    return null
  }
  if (instance && instance.getDom() !== el) {
    instance.dispose()
    instance = null
  }
  return instance || echarts.init(el)
}

function renderGauge() {
  if (!gaugeEl.value) return
  gaugeChart = ensureChart(gaugeChart, gaugeEl.value)
  gaugeChart.setOption(
    {
      backgroundColor: 'transparent',
      series: [
        {
          type: 'gauge',
          min: 0,
          max: gaugeMax.value,
          splitNumber: 4,
          radius: '95%',
          center: ['50%', '58%'],
          startAngle: 210,
          endAngle: -30,
          progress: { show: true, width: 14, roundCap: true },
          axisLine: {
            lineStyle: {
              width: 14,
              color: [
                [0.5, '#95d475'],
                [0.8, '#eebe52'],
                [1, '#f89898'],
              ],
            },
          },
          pointer: { width: 5, length: '62%', itemStyle: { color: '#5a6473' } },
          anchor: { show: true, size: 12, itemStyle: { color: '#5a6473' } },
          axisTick: { distance: -14, length: 4, lineStyle: { color: '#fff', width: 1 } },
          splitLine: { distance: -14, length: 14, lineStyle: { color: '#fff', width: 2 } },
          axisLabel: {
            distance: 18,
            fontSize: 10,
            color: '#909399',
            formatter: (value) => (value >= 1024 ? `${(value / 1024).toFixed(1)}G` : `${value}M`),
          },
          title: { offsetCenter: [0, '88%'], fontSize: 12, color: '#7a8699' },
          detail: {
            valueAnimation: true,
            offsetCenter: [0, '52%'],
            fontSize: 24,
            fontWeight: 600,
            color: '#1f2733',
            formatter: (value) => `${Number(value).toFixed(0)} MB`,
          },
          data: [{ value: Number(memoryMb.value.toFixed(1)), name: '模型子进程内存' }],
        },
      ],
    },
    true,
  )
}

function renderTrend() {
  if (!trendEl.value) return
  trendChart = ensureChart(trendChart, trendEl.value)
  const samples = memorySamples.value
  trendChart.setOption(
    {
      backgroundColor: 'transparent',
      grid: { left: 8, right: 16, top: 24, bottom: 8, containLabel: true },
      tooltip: { trigger: 'axis', formatter: (params) => `${params[0].axisValue}<br>内存：${params[0].data} MB` },
      xAxis: {
        type: 'category',
        data: samples.map((s) => s.time),
        boundaryGap: false,
        axisLabel: { fontSize: 10, color: '#909399' },
        axisLine: { lineStyle: { color: '#e6e9f0' } },
      },
      yAxis: {
        type: 'value',
        name: 'MB',
        nameTextStyle: { fontSize: 10, color: '#909399' },
        axisLabel: { fontSize: 10, color: '#909399' },
        splitLine: { lineStyle: { color: '#eef0f5' } },
      },
      series: [
        {
          type: 'line',
          smooth: true,
          showSymbol: false,
          data: samples.map((s) => s.mb),
          lineStyle: { width: 2, color: '#409eff' },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: 'rgba(64,158,255,0.32)' },
              { offset: 1, color: 'rgba(64,158,255,0.02)' },
            ]),
          },
        },
      ],
    },
    true,
  )
}

function renderAgentChart() {
  if (!agentEl.value) {
    if (agentChart) {
      agentChart.dispose()
      agentChart = null
    }
    return
  }
  agentChart = ensureChart(agentChart, agentEl.value)
  const items = agentCalls.value.slice(0, 8)
  agentChart.setOption(
    {
      backgroundColor: 'transparent',
      grid: { left: 8, right: 36, top: 12, bottom: 8, containLabel: true },
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      xAxis: {
        type: 'value',
        minInterval: 1,
        axisLabel: { fontSize: 10, color: '#909399' },
        splitLine: { lineStyle: { color: '#eef0f5' } },
      },
      yAxis: {
        type: 'category',
        data: items.map((i) => i.name),
        inverse: true,
        axisLabel: { fontSize: 11, color: '#5a6473' },
        axisTick: { show: false },
        axisLine: { lineStyle: { color: '#e6e9f0' } },
      },
      series: [
        {
          type: 'bar',
          barMaxWidth: 16,
          data: items.map((i) => i.count),
          itemStyle: { color: '#6a5cff', borderRadius: [0, 4, 4, 0] },
          label: { show: true, position: 'right', fontSize: 10, color: '#7a8699' },
        },
      ],
    },
    true,
  )
}

function resizeCharts() {
  if (gaugeChart) gaugeChart.resize()
  if (trendChart) trendChart.resize()
  if (agentChart) agentChart.resize()
}

/* ------------------------------------------------------------------ 数据 */

function pushSample() {
  const now = new Date()
  const time = now.toLocaleTimeString('zh-CN', { hour12: false })
  memorySamples.value.push({ time, mb: Number(memoryMb.value.toFixed(1)) })
  if (memorySamples.value.length > MAX_SAMPLES) memorySamples.value.shift()
}

async function refreshStatus(showToast = false) {
  loadingStatus.value = true
  try {
    const data = await getModelStatus(true)
    if (data && typeof data === 'object') {
      status.value = Object.assign(
        {
          loaded: false,
          generating: false,
          worker_alive: false,
          memory_mb: 0,
          worker_pid: null,
          idle_seconds: null,
          remaining_before_unload: null,
          error: null,
        },
        data,
      )
      setModelStatus(status.value)
      statusError.value = ''
    }
    pushSample()
    lastUpdated.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
    await nextTick()
    renderGauge()
    renderTrend()
    if (showToast) ElMessage.success('模型状态已刷新')
  } catch (e) {
    statusError.value = (e && e.message) || '获取模型状态失败'
    if (showToast) ElMessage.error(statusError.value)
  } finally {
    loadingStatus.value = false
  }
}

async function refreshDashboard(showToast = false) {
  loadingDashboard.value = true
  try {
    const data = await getDashboard(true)
    if (data && typeof data === 'object') {
      dashboard.value = Object.assign(
        { documents: 0, messages: 0, conversations: 0, today_messages: 0, agent_calls: {} },
        data,
      )
    }
    dashboardError.value = ''
    await nextTick()
    renderAgentChart()
    if (showToast) ElMessage.success('统计数据已刷新')
  } catch (e) {
    dashboardError.value = (e && e.message) || '获取统计数据失败'
    if (showToast) ElMessage.error(dashboardError.value)
  } finally {
    loadingDashboard.value = false
  }
}

async function handleLoad() {
  acting.value = 'load'
  try {
    const res = await loadModel()
    ElMessage.success((res && res.message) || '模型加载完成')
  } catch (e) {
    ElMessage.error((e && e.message) || '模型加载失败')
  } finally {
    acting.value = ''
    await refreshStatus()
    await refreshDashboard()
  }
}

async function handleUnload() {
  acting.value = 'unload'
  try {
    const res = await unloadModel()
    ElMessage.success((res && res.message) || '模型已卸载，内存应明显下降')
  } catch (e) {
    ElMessage.error((e && e.message) || '模型卸载失败')
  } finally {
    acting.value = ''
    await refreshStatus()
  }
}

function startPolling() {
  stopPolling()
  pollTimer = setInterval(() => {
    if (document.hidden) return
    refreshStatus()
  }, POLL_INTERVAL)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

function handleAutoRefreshChange(value) {
  if (value) startPolling()
  else stopPolling()
}

/* -------------------------------------------------------------- 生命周期 */

onMounted(async () => {
  await nextTick()
  renderGauge()
  renderTrend()
  renderAgentChart()
  window.addEventListener('resize', resizeCharts)
  await Promise.all([refreshStatus(), refreshDashboard()])
  if (autoRefresh.value) startPolling()
})

onBeforeUnmount(() => {
  stopPolling()
  window.removeEventListener('resize', resizeCharts)
  ;[gaugeChart, trendChart, agentChart].forEach((chart) => {
    if (chart) chart.dispose()
  })
  gaugeChart = null
  trendChart = null
  agentChart = null
})
</script>

<template>
  <div class="page">
    <div class="page-scroll">
      <!-- 操作栏 -->
      <div class="panel mb">
        <div class="panel-title">
          <span>模型与系统状态</span>
          <span class="sub">最后更新：{{ lastUpdated || '-' }}（每 {{ POLL_INTERVAL / 1000 }} 秒轮询）</span>
        </div>
        <div class="row-gap">
          <el-button
            type="primary"
            size="small"
            :loading="acting === 'load'"
            :disabled="acting === 'unload'"
            @click="handleLoad"
          >
            加载模型
          </el-button>
          <el-button
            type="danger"
            size="small"
            plain
            :loading="acting === 'unload'"
            :disabled="acting === 'load'"
            @click="handleUnload"
          >
            卸载模型
          </el-button>
          <el-button size="small" :loading="loadingStatus" @click="refreshStatus(true)">刷新状态</el-button>
          <el-button size="small" :loading="loadingDashboard" @click="refreshDashboard(true)">刷新统计</el-button>
          <span class="spacer"></span>
          <span class="muted">自动刷新</span>
          <el-switch v-model="autoRefresh" size="small" @change="handleAutoRefreshChange" />
        </div>
        <el-alert
          v-if="statusError"
          class="mt"
          type="error"
          :closable="false"
          show-icon
          :title="`模型状态获取失败：${statusError}`"
          description="请确认后端已启动（python backend/main.py，监听 http://localhost:8000）。"
        />
        <el-alert
          v-if="status.error"
          class="mt"
          type="warning"
          :closable="false"
          show-icon
          :title="`后端返回的模型错误信息：${status.error}`"
        />
      </div>

      <!-- 状态卡片 -->
      <div class="stat-grid mb">
        <div class="stat-card">
          <div class="label">模型加载状态</div>
          <div class="value small">
            <el-tag :type="loadedTag.type" effect="dark" size="small">{{ loadedTag.text }}</el-tag>
          </div>
        </div>
        <div class="stat-card">
          <div class="label">内存占用</div>
          <div class="value">{{ memoryMb.toFixed(0) }} <span class="unit">MB</span></div>
        </div>
        <div class="stat-card">
          <div class="label">Worker PID</div>
          <div class="value small mono">{{ status.worker_pid === null || status.worker_pid === undefined ? '-' : status.worker_pid }}</div>
        </div>
        <div class="stat-card">
          <div class="label">Worker 存活</div>
          <div class="value small">
            <el-tag :type="status.worker_alive ? 'success' : 'danger'" effect="plain" size="small">
              {{ status.worker_alive ? '存活' : '离线' }}
            </el-tag>
          </div>
        </div>
        <div class="stat-card">
          <div class="label">是否生成中</div>
          <div class="value small">
            <el-tag :type="status.generating ? 'warning' : 'info'" effect="plain" size="small">
              {{ status.generating ? '生成中' : '空闲' }}
            </el-tag>
          </div>
        </div>
        <div class="stat-card">
          <div class="label">空闲秒数</div>
          <div class="value">{{ idleSeconds === null ? '-' : idleSeconds.toFixed(1) }} <span class="unit">s</span></div>
        </div>
        <div class="stat-card wide">
          <div class="label">
            距离自动卸载剩余
            <span class="muted">（空闲 {{ IDLE_TIMEOUT }} 秒自动卸载）</span>
          </div>
          <div class="value">
            {{ remainingSeconds === null ? '-' : remainingSeconds.toFixed(1) }} <span class="unit">s</span>
          </div>
          <el-progress
            class="mt6"
            :percentage="unloadPercent"
            :status="unloadPercent < 25 ? 'exception' : unloadPercent < 60 ? 'warning' : 'success'"
            :stroke-width="10"
            :show-text="false"
          />
        </div>
      </div>

      <!-- 图表 -->
      <div class="chart-grid mb">
        <div class="panel">
          <div class="panel-title">
            <span>内存仪表盘</span>
            <span class="sub">Qwen2.5-1.5B Q4_K_M · CPU 推理</span>
          </div>
          <div ref="gaugeEl" class="gauge-chart"></div>
        </div>
        <div class="panel">
          <div class="panel-title">
            <span>内存趋势</span>
            <span class="sub">最近 {{ memorySamples.length }} 次采样</span>
          </div>
          <div ref="trendEl" class="trend-chart"></div>
        </div>
      </div>

      <!-- 仪表盘统计 -->
      <div class="panel mb">
        <div class="panel-title">
          <span>运行统计（/api/stats/dashboard）</span>
          <span class="sub">{{ dashboardError || '数据来自 SQLite' }}</span>
        </div>
        <div class="stat-grid">
          <div class="stat-card">
            <div class="label">知识库文档</div>
            <div class="value">{{ dashboard.documents }}</div>
          </div>
          <div class="stat-card">
            <div class="label">消息总数</div>
            <div class="value">{{ dashboard.messages }}</div>
          </div>
          <div class="stat-card">
            <div class="label">会话总数</div>
            <div class="value">{{ dashboard.conversations }}</div>
          </div>
          <div class="stat-card">
            <div class="label">今日消息</div>
            <div class="value">{{ dashboard.today_messages }}</div>
          </div>
          <div class="stat-card">
            <div class="label">Agent 调用总次数</div>
            <div class="value">{{ agentCallsTotal }}</div>
          </div>
        </div>
      </div>

      <div class="panel">
        <div class="panel-title">
          <span>各 Agent 调用次数</span>
          <span class="sub">仅统计数据库中真实存在的日志</span>
        </div>
        <div v-if="agentCalls.length" ref="agentEl" class="agent-chart"></div>
        <div v-else class="empty-hint">暂无 Agent 调用记录（请先在对话页完成一次问答）</div>
      </div>

      <div class="panel mt14">
        <div class="panel-title">
          <span>约束说明</span>
        </div>
        <ul class="note-list">
          <li>模型运行在独立子进程（PID 见上），FastAPI 主进程不加载模型，全局仅一个模型实例。</li>
          <li>空闲 {{ IDLE_TIMEOUT }} 秒自动卸载；再次提问时后端异步拉起模型，前端会显示「模型加载中」。</li>
          <li>当前登录用户：{{ session.user ? session.user.username : '未登录' }}。</li>
        </ul>
      </div>
    </div>
  </div>
</template>

<style scoped>
.mb {
  margin-bottom: 14px;
}

.mt {
  margin-top: 12px;
}

.mt14 {
  margin-top: 14px;
}

.mt6 {
  margin-top: 6px;
}

.spacer {
  flex: 1;
}

.unit {
  font-size: 12px;
  color: var(--app-text-sub);
  font-weight: 400;
}

.mono {
  font-family: Consolas, monospace;
}

.stat-card.wide {
  grid-column: span 2;
}

.chart-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1.2fr);
  gap: 14px;
}

.gauge-chart {
  width: 100%;
  height: 260px;
}

.trend-chart {
  width: 100%;
  height: 260px;
}

.agent-chart {
  width: 100%;
  height: 220px;
}

.note-list {
  margin: 0;
  padding-left: 20px;
  font-size: 12px;
  color: #5a6473;
  line-height: 1.9;
}

@media (max-width: 1100px) {
  .chart-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .stat-card.wide {
    grid-column: span 1;
  }
}
</style>
