<script setup>
/**
 * Agent 链路可视化页
 *
 * 硬性要求（TRAE_PROMPT 第八条）：只高亮真实执行过的节点，绝不伪造未执行节点。
 * 因此：
 * - 节点是否高亮，完全由 /api/agent/logs/{conversation_id} 或对话页实时收集到的
 *   agent_log 列表决定（按 agent_name + action 映射到流程图节点）；
 * - 未出现在日志中的节点一律置灰、虚线边框，不计入任何统计；
 * - 连线只有在「日志顺序中前后相邻的两个节点」之间才高亮，避免虚构路径。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import * as echarts from 'echarts'
import { getAgentLogs, getChatHistory } from '../api'
import { session } from '../store/session'

defineOptions({ name: 'AgentFlowView' })

/* ------------------------------------------------------------ 流程图定义 */

/** 节点定义与后端 backend/agent/graph/graph.py 中的 LangGraph 节点一一对应 */
const NODE_DEFS = [
  { id: 'dispatch', name: '调度入口', agentName: 'dispatch_agent', actions: ['调度入口'], x: 60, y: 80 },
  { id: 'audit', name: '输入审核', agentName: 'audit_agent', actions: ['输入审核'], x: 250, y: 80 },
  { id: 'retrieval', name: '知识库检索', agentName: 'retrieval_agent', actions: ['知识库检索'], x: 440, y: 80 },
  { id: 'llm_gen', name: 'LLM 生成', agentName: 'llm_gen_agent', actions: ['LLM生成', '加载模型'], x: 630, y: 80 },
  { id: 'audit_output', name: '输出审核', agentName: 'audit_agent', actions: ['输出审核'], x: 820, y: 80 },
  { id: 'image', name: '示意图绘制', agentName: 'image_agent', actions: ['生成示意图'], x: 880, y: 230 },
  { id: 'aggregate', name: '汇总输出', agentName: 'dispatch_agent', actions: ['汇总输出'], x: 1010, y: 80 },
]

const EDGE_DEFS = [
  { from: 'dispatch', to: 'audit', label: '' },
  { from: 'audit', to: 'retrieval', label: '合规' },
  { from: 'audit', to: 'aggregate', label: '拦截直出', curveness: -0.28 },
  { from: 'retrieval', to: 'llm_gen', label: '' },
  { from: 'llm_gen', to: 'audit_output', label: '' },
  { from: 'audit_output', to: 'image', label: '需要配图' },
  { from: 'audit_output', to: 'aggregate', label: '无需配图' },
  { from: 'image', to: 'aggregate', label: '' },
]

const AGENT_LABELS = {
  dispatch_agent: '调度 Agent',
  audit_agent: '审核 Agent',
  retrieval_agent: '检索 Agent',
  llm_gen_agent: '生成 Agent',
  image_agent: '绘图 Agent',
}

const STATUS_META = {
  success: { label: '成功', color: '#67c23a', tag: 'success', priority: 1 },
  skip: { label: '跳过', color: '#909399', tag: 'info', priority: 2 },
  blocked: { label: '已拦截', color: '#e6a23c', tag: 'warning', priority: 3 },
  error: { label: '错误', color: '#f56c6c', tag: 'danger', priority: 4 },
}

const IDLE_COLOR = '#e9ebf0'
const IDLE_BORDER = '#c9ced6'
const IDLE_TEXT = '#a8abb2'
const EDGE_IDLE = '#dcdfe6'
const EDGE_ACTIVE = '#67c23a'

/* ------------------------------------------------------------------ 状态 */

const logs = ref([])
const loading = ref(false)
const loadError = ref('')
const conversations = ref([])
const selectedConv = ref('')
const manualConv = ref('')
const sourceHint = ref('')

const flowEl = ref(null)
const barEl = ref(null)
let flowChart = null
let barChart = null
let resizeObserver = null

/* ------------------------------------------------------------------ 工具 */

function agentLabel(name) {
  return AGENT_LABELS[name] || name || '未知 Agent'
}

function statusMeta(status) {
  return STATUS_META[status] || { label: status || '-', color: '#409eff', tag: 'primary', priority: 0 }
}

function formatElapsed(value) {
  const n = Number(value)
  if (!Number.isFinite(n)) return '-'
  return n >= 1 ? `${n.toFixed(3)} s` : `${(n * 1000).toFixed(0)} ms`
}

function formatTime(value) {
  if (value === null || value === undefined || value === '') return ''
  const str = String(value).trim()
  let date = null
  if (/^\d+(\.\d+)?$/.test(str)) {
    const n = Number(str)
    date = new Date(n < 1e12 ? n * 1000 : n)
  } else {
    date = new Date(str.includes('T') ? str : str.replace(' ', 'T'))
    if (Number.isNaN(date.getTime())) date = new Date(str)
  }
  if (!date || Number.isNaN(date.getTime())) return str
  return date.toLocaleString('zh-CN', { hour12: false })
}

function normalizeLog(raw) {
  const log = raw && typeof raw === 'object' ? raw : {}
  return {
    agent_name: log.agent_name || log.agent || '未知 Agent',
    action: log.action || '',
    status: log.status || 'success',
    elapsed: Number(log.elapsed || 0),
    input_summary: log.input_summary || '',
    output_summary: log.output_summary || '',
    created_at: log.created_at || '',
    node_id: nodeIdOf(log),
  }
}

/** agent_log -> 流程图节点 ID（必须同时匹配 agent_name 与 action，避免同名 Agent 混淆） */
function nodeIdOf(log) {
  const name = String((log && log.agent_name) || '').trim()
  const action = String((log && log.action) || '').trim()
  const exact = NODE_DEFS.find((def) => def.agentName === name && def.actions.includes(action))
  if (exact) return exact.id
  const candidates = NODE_DEFS.filter((def) => def.agentName === name)
  return candidates.length === 1 ? candidates[0].id : null
}

/* ------------------------------------------------------------------ 统计 */

const nodeStates = computed(() => {
  const states = {}
  NODE_DEFS.forEach((def) => {
    states[def.id] = { executed: false, count: 0, elapsed: 0, status: '', logs: [] }
  })
  logs.value.forEach((log) => {
    const id = log.node_id
    if (!id || !states[id]) return
    const s = states[id]
    s.executed = true
    s.count += 1
    s.elapsed += Number(log.elapsed) || 0
    s.logs.push(log)
    const prev = s.status ? statusMeta(s.status).priority : -1
    if (statusMeta(log.status).priority >= prev) s.status = log.status
  })
  return states
})

/** 日志顺序中前后相邻的节点对，用于决定哪条连线被真实走过 */
const executedEdges = computed(() => {
  const pairs = new Set()
  const order = logs.value.map((log) => log.node_id).filter(Boolean)
  for (let i = 0; i < order.length - 1; i += 1) {
    if (order[i] !== order[i + 1]) pairs.add(`${order[i]}->${order[i + 1]}`)
  }
  return pairs
})

const executedCount = computed(() => NODE_DEFS.filter((def) => nodeStates.value[def.id].executed).length)

const totalElapsed = computed(() => logs.value.reduce((sum, log) => sum + (Number(log.elapsed) || 0), 0))

const unmatchedLogs = computed(() => logs.value.filter((log) => !log.node_id).length)

const finalStatus = computed(() => {
  if (!logs.value.length) return { text: '无日志', tag: 'info' }
  const blocked = logs.value.some((log) => log.status === 'blocked')
  const error = logs.value.some((log) => log.status === 'error')
  if (error) return { text: '存在错误', tag: 'danger' }
  if (blocked) return { text: '被审核拦截', tag: 'warning' }
  return { text: '全部成功', tag: 'success' }
})

/* -------------------------------------------------------------- ECharts */

function buildFlowOption() {
  const states = nodeStates.value
  const activeEdges = executedEdges.value
  const hasLogs = logs.value.length > 0

  const data = NODE_DEFS.map((def) => {
    const s = states[def.id]
    const meta = statusMeta(s.status)
    const executed = s.executed
    return {
      id: def.id,
      name: def.name,
      nodeName: def.name,
      x: def.x,
      y: def.y,
      executed,
      elapsedText: executed ? formatElapsed(s.elapsed) : '未执行',
      symbol: 'roundRect',
      symbolSize: [116, 48],
      itemStyle: {
        color: executed ? meta.color : IDLE_COLOR,
        borderColor: executed ? meta.color : IDLE_BORDER,
        borderWidth: executed ? 2 : 1.5,
        borderType: executed ? 'solid' : 'dashed',
        shadowBlur: executed ? 12 : 0,
        shadowColor: executed ? `${meta.color}55` : 'transparent',
      },
      label: {
        show: true,
        position: 'inside',
        color: executed ? '#ffffff' : IDLE_TEXT,
        fontSize: 12,
        fontWeight: executed ? 600 : 400,
        lineHeight: 16,
        formatter: executed ? `${def.name}\n${formatElapsed(s.elapsed)}` : `${def.name}\n未执行`,
      },
      tooltipData: {
        agent: def.agentName,
        actions: def.actions.join(' / '),
        executed,
        count: s.count,
        elapsed: s.elapsed,
        status: executed ? meta.label : '未执行',
        outputs: s.logs.map((log) => log.output_summary).filter(Boolean).slice(0, 3),
      },
    }
  })

  const links = EDGE_DEFS.map((edge) => {
    const key = `${edge.from}->${edge.to}`
    const active = hasLogs && activeEdges.has(key)
    return {
      source: edge.from,
      target: edge.to,
      active,
      curveness: edge.curveness || 0,
      lineStyle: {
        color: active ? EDGE_ACTIVE : EDGE_IDLE,
        width: active ? 3 : 1.5,
        type: active ? 'solid' : 'dashed',
        curveness: edge.curveness || 0,
        opacity: active ? 1 : 0.85,
      },
      label: {
        show: !!edge.label,
        formatter: edge.label,
        fontSize: 10,
        color: active ? '#4e9a34' : '#b6bcc7',
        backgroundColor: 'rgba(255,255,255,0.85)',
        padding: [1, 3],
        borderRadius: 3,
      },
    }
  })

  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'item',
      confine: true,
      formatter: (params) => {
        if (params.dataType === 'edge') {
          return params.data.active ? '该连线在本次请求中被真实走过' : '本次请求未经过该连线'
        }
        const d = params.data && params.data.tooltipData
        if (!d) return params.name
        const lines = [
          `<strong>${params.name}</strong>`,
          `Agent：${d.agent}`,
          `动作：${d.actions}`,
          d.executed
            ? `状态：${d.status} ｜ 命中日志：${d.count} 条 ｜ 累计耗时：${formatElapsed(d.elapsed)}`
            : '状态：<span style="color:#a8abb2">未执行（不计入链路）</span>',
        ]
        if (d.outputs && d.outputs.length) {
          lines.push(`输出摘要：${d.outputs.join('；')}`)
        }
        return lines.join('<br>')
      },
    },
    animationDuration: 450,
    series: [
      {
        type: 'graph',
        layout: 'none',
        roam: true,
        zoom: 0.92,
        left: '2%',
        right: '2%',
        top: 12,
        bottom: 12,
        symbol: 'roundRect',
        symbolSize: [116, 48],
        edgeSymbol: ['none', 'arrow'],
        edgeSymbolSize: 9,
        emphasis: { focus: 'adjacency', scale: 1.04 },
        label: { show: true, position: 'inside' },
        data,
        links,
      },
    ],
  }
}

function buildBarOption() {
  const states = nodeStates.value
  const executed = NODE_DEFS.filter((def) => states[def.id].executed)
  const categories = executed.map((def) => def.name)
  const values = executed.map((def) => Number(states[def.id].elapsed.toFixed(3)))
  const colors = executed.map((def) => statusMeta(states[def.id].status).color)

  return {
    backgroundColor: 'transparent',
    grid: { left: 8, right: 40, top: 10, bottom: 8, containLabel: true },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, formatter: (p) => `${p[0].name}：${p[0].value} s` },
    xAxis: {
      type: 'value',
      name: '秒',
      nameTextStyle: { fontSize: 10, color: '#909399' },
      axisLabel: { fontSize: 10, color: '#909399' },
      splitLine: { lineStyle: { color: '#eef0f5' } },
    },
    yAxis: {
      type: 'category',
      data: categories,
      inverse: true,
      axisLabel: { fontSize: 11, color: '#5a6473' },
      axisTick: { show: false },
      axisLine: { lineStyle: { color: '#e6e9f0' } },
    },
    series: [
      {
        type: 'bar',
        data: values.map((v, i) => ({ value: v, itemStyle: { color: colors[i], borderRadius: [0, 4, 4, 0] } })),
        barMaxWidth: 18,
        label: { show: true, position: 'right', fontSize: 10, color: '#7a8699', formatter: '{c} s' },
      },
    ],
  }
}

/**
 * 取得（或重建）绑定到指定 DOM 的 ECharts 实例
 * 图表容器带 v-if 时，DOM 可能被销毁重建，必须重新 init，否则实例会绑在游离节点上
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

function renderCharts() {
  if (flowEl.value) {
    flowChart = ensureChart(flowChart, flowEl.value)
    flowChart.setOption(buildFlowOption(), true)
  }
  if (barEl.value) {
    barChart = ensureChart(barChart, barEl.value)
    barChart.setOption(buildBarOption(), true)
  } else if (barChart) {
    barChart.dispose()
    barChart = null
  }
}

function resizeCharts() {
  if (flowChart) flowChart.resize()
  if (barChart) barChart.resize()
}

/* -------------------------------------------------------------- 数据加载 */

function applyLogs(rawLogs, hint) {
  logs.value = (rawLogs || []).map(normalizeLog)
  sourceHint.value = hint || ''
  nextTick(renderCharts)
}

async function loadByConversation(conversationId) {
  const id = String(conversationId || '').trim()
  if (!id) {
    ElMessage.warning('请先选择或输入会话 ID')
    return
  }
  loading.value = true
  loadError.value = ''
  try {
    const data = await getAgentLogs(id, true)
    const list = (data && data.logs) || []
    applyLogs(list, `来自 GET /api/agent/logs/${id.slice(0, 8)}...（${list.length} 条）`)
    if (!list.length) {
      loadError.value = '该会话没有 Agent 日志记录，可能是会话尚未提问，或后端未落库。'
    }
  } catch (e) {
    logs.value = []
    loadError.value = (e && e.message) || '加载 Agent 日志失败'
    applyLogs([], '加载失败')
  } finally {
    loading.value = false
  }
}

function loadFromSession() {
  const sessionLogs = session.lastAgentLogs || []
  if (!sessionLogs.length) {
    ElMessage.warning('对话页最近一次运行没有留下 Agent 日志，请先去对话页提问')
    return
  }
  selectedConv.value = session.lastRunConversationId || session.conversationId || ''
  applyLogs(sessionLogs, `来自对话页最近一次运行的实时日志（${sessionLogs.length} 条）`)
  if (session.lastPrompt) sourceHint.value += ` ｜ 问题：${session.lastPrompt.slice(0, 40)}`
  ElMessage.success(`已载入 ${sessionLogs.length} 条实时日志`)
}

async function loadConversations() {
  try {
    const data = await getChatHistory('', true)
    conversations.value = (data && data.conversations) || []
  } catch (e) {
    conversations.value = []
  }
}

function handleSelectChange(value) {
  if (value === '__session__') {
    loadFromSession()
    return
  }
  if (value) loadByConversation(value)
}

function handleManualQuery() {
  const id = manualConv.value.trim()
  if (!id) {
    ElMessage.warning('请输入会话 ID')
    return
  }
  selectedConv.value = id
  loadByConversation(id)
}

async function refresh() {
  await loadConversations()
  const id = selectedConv.value && selectedConv.value !== '__session__' ? selectedConv.value : session.conversationId
  if (id) await loadByConversation(id)
  else if ((session.lastAgentLogs || []).length) loadFromSession()
  else applyLogs([], '等待数据')
}

function exportLogs() {
  if (!logs.value.length) {
    ElMessage.warning('暂无日志可导出')
    return
  }
  const payload = {
    conversation_id: selectedConv.value === '__session__' ? session.conversationId : selectedConv.value,
    count: logs.value.length,
    logs: logs.value,
  }
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `agent_logs_${Date.now()}.json`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

/* -------------------------------------------------------------- 生命周期 */

onMounted(async () => {
  await nextTick()
  renderCharts()
  if (typeof ResizeObserver !== 'undefined') {
    resizeObserver = new ResizeObserver(() => resizeCharts())
    if (flowEl.value) resizeObserver.observe(flowEl.value)
  }
  window.addEventListener('resize', resizeCharts)

  await loadConversations()
  if (session.conversationId) {
    selectedConv.value = session.conversationId
    await loadByConversation(session.conversationId)
    // 数据库中没有落库时，退回使用对话页内存中的实时日志
    if (!logs.value.length && (session.lastAgentLogs || []).length) loadFromSession()
  } else if ((session.lastAgentLogs || []).length) {
    loadFromSession()
  } else {
    applyLogs([], '等待数据')
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', resizeCharts)
  if (resizeObserver) {
    resizeObserver.disconnect()
    resizeObserver = null
  }
  if (flowChart) {
    flowChart.dispose()
    flowChart = null
  }
  if (barChart) {
    barChart.dispose()
    barChart = null
  }
})
</script>

<template>
  <div class="page">
    <div class="page-scroll">
      <!-- 工具栏 -->
      <div class="panel mb">
        <div class="panel-title">
          <span>Agent 链路</span>
          <span class="sub">仅高亮真实执行过的节点，未执行节点一律置灰，不做任何虚构</span>
        </div>
        <div class="row-gap">
          <el-select
            v-model="selectedConv"
            size="small"
            placeholder="选择会话"
            style="width: 260px"
            clearable
            @change="handleSelectChange"
          >
            <el-option label="本次运行（对话页实时日志）" value="__session__" />
            <el-option
              v-for="conv in conversations"
              :key="conv.conversation_id"
              :label="`${conv.title || '未命名会话'} · ${String(conv.conversation_id).slice(0, 8)}`"
              :value="conv.conversation_id"
            />
          </el-select>
          <el-input
            v-model="manualConv"
            size="small"
            placeholder="或手动输入 conversation_id"
            style="width: 280px"
            clearable
            @keyup.enter="handleManualQuery"
          />
          <el-button size="small" type="primary" plain :loading="loading" @click="handleManualQuery">查询日志</el-button>
          <el-button size="small" @click="loadFromSession">载入最近一次运行</el-button>
          <el-button size="small" @click="refresh">刷新</el-button>
          <el-button size="small" @click="exportLogs">导出 JSON</el-button>
          <span class="muted">{{ sourceHint }}</span>
        </div>
      </div>

      <!-- 统计 -->
      <div class="stat-grid mb">
        <div class="stat-card">
          <div class="label">日志条数</div>
          <div class="value">{{ logs.length }}</div>
        </div>
        <div class="stat-card">
          <div class="label">执行节点数</div>
          <div class="value">{{ executedCount }} / {{ NODE_DEFS.length }}</div>
        </div>
        <div class="stat-card">
          <div class="label">链路总耗时</div>
          <div class="value">{{ totalElapsed.toFixed(3) }} s</div>
        </div>
        <div class="stat-card">
          <div class="label">整体状态</div>
          <div class="value small">
            <el-tag size="small" :type="finalStatus.tag" effect="dark">{{ finalStatus.text }}</el-tag>
          </div>
        </div>
        <div class="stat-card">
          <div class="label">未匹配节点的日志</div>
          <div class="value">{{ unmatchedLogs }}</div>
        </div>
      </div>

      <!-- 流程图 -->
      <div class="panel mb">
        <div class="panel-title">
          <span>执行流程图（ECharts）</span>
          <span class="sub">dispatch → audit → retrieval → llm_gen → audit_output → image → aggregate</span>
        </div>
        <div v-loading="loading" class="chart-wrap">
          <div ref="flowEl" class="flow-chart"></div>
        </div>
        <div class="legend">
          <span class="legend-item"><i style="background: #67c23a"></i>执行成功</span>
          <span class="legend-item"><i style="background: #e6a23c"></i>审核拦截</span>
          <span class="legend-item"><i style="background: #f56c6c"></i>执行错误</span>
          <span class="legend-item"><i style="background: #909399"></i>跳过</span>
          <span class="legend-item"><i class="idle"></i>未执行（置灰）</span>
        </div>
        <el-alert
          v-if="loadError"
          class="mt"
          type="info"
          :closable="false"
          show-icon
          :title="loadError"
        />
        <el-alert
          v-else-if="!logs.length"
          class="mt"
          type="info"
          :closable="false"
          show-icon
          title="当前没有可展示的 Agent 日志"
          description="请先在「对话」页发起一次提问；链路会根据本次请求真实产生的 agent_log 高亮。"
        />
      </div>

      <!-- 节点耗时 -->
      <div class="panel mb">
        <div class="panel-title">
          <span>各节点耗时（仅统计已执行节点）</span>
          <span class="sub">未执行节点不出现在图中</span>
        </div>
        <div v-if="executedCount" ref="barEl" class="bar-chart"></div>
        <div v-else class="empty-hint">暂无已执行节点</div>
      </div>

      <!-- 时间线表格 -->
      <div class="panel">
        <div class="panel-title">
          <span>Agent 日志时间线</span>
          <span class="sub">agent_name / action / status / elapsed</span>
        </div>
        <el-table :data="logs" v-loading="loading" stripe border size="small" empty-text="暂无日志">
          <el-table-column type="expand">
            <template #default="{ row }">
              <div class="expand-box">
                <div><strong>输入摘要：</strong>{{ row.input_summary || '（无）' }}</div>
                <div><strong>输出摘要：</strong>{{ row.output_summary || '（无）' }}</div>
                <div><strong>创建时间：</strong>{{ formatTime(row.created_at) || '（无）' }}</div>
                <div><strong>映射节点：</strong>{{ row.node_id || '（未匹配到流程图节点）' }}</div>
              </div>
            </template>
          </el-table-column>
          <el-table-column type="index" label="#" width="52" align="center" />
          <el-table-column label="Agent" min-width="130">
            <template #default="{ row }">
              <div>{{ agentLabel(row.agent_name) }}</div>
              <div class="muted mono">{{ row.agent_name }}</div>
            </template>
          </el-table-column>
          <el-table-column prop="action" label="动作" min-width="120" />
          <el-table-column label="状态" width="100" align="center">
            <template #default="{ row }">
              <el-tag size="small" :type="statusMeta(row.status).tag" effect="plain">
                {{ statusMeta(row.status).label }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column
            prop="elapsed"
            label="耗时"
            width="110"
            align="right"
            sortable
            :sort-by="(row) => Number(row.elapsed)"
          >
            <template #default="{ row }">{{ formatElapsed(row.elapsed) }}</template>
          </el-table-column>
          <el-table-column prop="output_summary" label="输出摘要" min-width="220" show-overflow-tooltip />
          <el-table-column label="时间" width="170">
            <template #default="{ row }">
              <span class="mono">{{ formatTime(row.created_at) }}</span>
            </template>
          </el-table-column>
        </el-table>
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

.chart-wrap {
  width: 100%;
  min-height: 340px;
}

.flow-chart {
  width: 100%;
  height: 340px;
}

.bar-chart {
  width: 100%;
  height: 200px;
}

.legend {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
  padding-top: 8px;
  border-top: 1px dashed var(--app-border);
  margin-top: 6px;
}

.legend-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--app-text-sub);
}

.legend-item i {
  display: inline-block;
  width: 12px;
  height: 12px;
  border-radius: 3px;
}

.legend-item i.idle {
  background: #e9ebf0;
  border: 1px dashed #c9ced6;
}

.expand-box {
  padding: 6px 16px;
  font-size: 12px;
  line-height: 1.9;
  color: #46505f;
  word-break: break-all;
}

.mono {
  font-family: Consolas, monospace;
  font-size: 11px;
}
</style>
