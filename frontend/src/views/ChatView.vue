<script setup>
/**
 * 对话页（最高优先级）
 *
 * 能力：
 * 1. 通过 fetch + ReadableStream 手工解析后端 SSE（/api/agent/chat）
 * 2. token 事件进入缓冲队列，由定时器逐字吐出，实现打字机效果
 * 3. 内容用自研轻量 Markdown 渲染器 + v-html 展示（全文先 HTML 转义）
 * 4. agent_log 事件实时展示当前执行到哪个 Agent，并在右侧时间线累积
 * 5. image 事件（image_type=mermaid）交给 mermaid.js 渲染成 SVG 图
 * 6. done 事件回写 conversation_id，并从 /api/chat/history 拉取落库的引用来源
 * 7. error 事件提示错误；支持「停止生成」（AbortController）
 */
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getChatHistory, streamAgentChat, deleteConversation } from '../api'
import { renderMarkdown, extractMermaid } from '../utils/markdown'
import { renderMermaid } from '../utils/mermaid'
import { session, setConversationId, setLastRun } from '../store/session'

defineOptions({ name: 'ChatView' })

const router = useRouter()

/* ------------------------------------------------------------------ 常量 */

const AGENT_LABELS = {
  dispatch_agent: '调度 Agent',
  audit_agent: '审核 Agent',
  retrieval_agent: '检索 Agent',
  llm_gen_agent: '生成 Agent',
  image_agent: '绘图 Agent',
}

const STATUS_LABELS = {
  success: '成功',
  blocked: '已拦截',
  skip: '跳过',
  error: '错误',
  generating: '进行中',
}

const QUICK_PROMPTS = [
  '简述操作系统的层次结构',
  '什么是死锁？产生死锁的四个必要条件是什么？',
  '画一个计算机网络七层模型示意图',
  'B+ 树和 B 树的区别是什么？',
]

/* ------------------------------------------------------------------ 状态 */

const messages = ref([])
const input = ref('')
const sending = ref(false)
const modelLoading = ref(false)
const notice = ref('')
const currentLogs = ref([])
const conversations = ref([])
const loadingHistory = ref(false)
const sideTab = ref('flow')
const scroller = ref(null)
const autoScroll = ref(true)

const streamStats = reactive({ tokens: 0, chars: 0, startedAt: 0, elapsed: 0, events: 0 })

/* 图片全屏查看（Mermaid SVG 放大/缩小） */
const viewer = reactive({ visible: false, svg: '', code: '', scale: 1 })
const viewerScaleText = computed(() => `${Math.round(viewer.scale * 100)}%`)

function openViewer(img) {
  if (!img || !(img.svg || img.code)) return
  viewer.visible = true
  viewer.svg = img.svg || ''
  viewer.code = img.code || ''
  viewer.scale = 1
}

function closeViewer() {
  viewer.visible = false
}

function zoomIn() {
  viewer.scale = Math.min(4, Number((viewer.scale + 0.2).toFixed(2)))
}

function zoomOut() {
  viewer.scale = Math.max(0.4, Number((viewer.scale - 0.2).toFixed(2)))
}

function zoomReset() {
  viewer.scale = 1
}

function onViewerWheel(e) {
  if (e.deltaY < 0) zoomIn()
  else zoomOut()
}

function onViewerKeydown(e) {
  if (e.key === 'Escape' && viewer.visible) closeViewer()
}

let activeMessage = null // 当前正在流式输出的助手消息（reactive 代理）
let pendingText = '' // token 缓冲队列（打字机待吐出的文字）
let typingTimer = null
let elapsedTimer = null
let abortController = null

/* ------------------------------------------------------------------ 计算 */

const currentAgent = computed(() => (currentLogs.value.length ? currentLogs.value[currentLogs.value.length - 1] : null))

const lastAssistant = computed(() => {
  for (let i = messages.value.length - 1; i >= 0; i -= 1) {
    if (messages.value[i].role === 'assistant') return messages.value[i]
  }
  return null
})

const activeSources = computed(() => (lastAssistant.value ? lastAssistant.value.sources : []))

const totalElapsed = computed(() =>
  currentLogs.value.reduce((sum, log) => sum + (Number(log.elapsed) || 0), 0),
)

const memoryMb = computed(() => Number(session.modelStatus.memory_mb || 0).toFixed(0))

/* ------------------------------------------------------------------ 工具 */

function agentLabel(name) {
  return AGENT_LABELS[name] || name || '未知 Agent'
}

function statusLabel(status) {
  return STATUS_LABELS[status] || status || '-'
}

function statusTagType(status) {
  if (status === 'success') return 'success'
  if (status === 'blocked') return 'warning'
  if (status === 'error') return 'danger'
  if (status === 'generating') return 'primary'
  return 'info'
}

function formatElapsed(value) {
  const n = Number(value)
  if (!Number.isFinite(n)) return '-'
  return n >= 1 ? `${n.toFixed(2)} s` : `${(n * 1000).toFixed(0)} ms`
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
  return date.toLocaleTimeString('zh-CN', { hour12: false })
}

function normalizeLog(raw) {
  const log = raw && typeof raw === 'object' ? raw : {}
  return {
    agent_name: log.agent_name || log.agent || '未知 Agent',
    action: log.action || log.act || '',
    status: log.status || 'success',
    elapsed: Number(log.elapsed || 0),
    input_summary: log.input_summary || log.input || '',
    output_summary: log.output_summary || log.output || '',
    created_at: formatTime(log.created_at),
  }
}

function normalizeSources(raw) {
  if (!raw) return []
  let data = raw
  if (typeof data === 'string') {
    const text = data.trim()
    if (!text) return []
    try {
      data = JSON.parse(text)
    } catch (e) {
      return [{ index: 1, doc_name: text.slice(0, 120), page: '', para_index: '', score: null, text: '' }]
    }
  }
  if (!Array.isArray(data)) data = [data]
  return data
    .map((item, i) => {
      if (typeof item === 'string') {
        return { index: i + 1, doc_name: item, page: '', para_index: '', score: null, text: '' }
      }
      if (!item || typeof item !== 'object') return null
      const score = item.score ?? item.rrf_score ?? item.similarity ?? null
      return {
        index: i + 1,
        doc_name: item.doc_name || item.doc || item.source || item.filename || '未知文档',
        page: item.page ?? item.page_no ?? '',
        para_index: item.para_index ?? item.chunk_index ?? item.index ?? '',
        score: score === null || score === undefined ? null : Number(score),
        text: item.text || item.content || item.snippet || '',
      }
    })
    .filter(Boolean)
}

function createMessage(role, content, extra = {}) {
  return Object.assign(
    {
      id: `msg-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      role,
      content,
      prompt: '',
      sources: [],
      images: [],
      logs: [],
      status: role === 'assistant' ? 'pending' : 'sent',
      error: '',
      time: formatTime(Date.now()),
      tokens: 0,
      elapsed: 0,
    },
    extra,
  )
}

/** 追加消息，返回数组中的 reactive 代理（后续修改才能触发视图更新） */
function pushMessage(role, content, extra) {
  messages.value.push(createMessage(role, content, extra))
  return messages.value[messages.value.length - 1]
}

/* ------------------------------------------------------- 打字机逐字显示 */

function ensureTyping() {
  if (typingTimer) return
  typingTimer = setInterval(flushChunk, 22)
}

function stopTyping() {
  if (typingTimer) {
    clearInterval(typingTimer)
    typingTimer = null
  }
}

function flushChunk() {
  if (!activeMessage) {
    stopTyping()
    return
  }
  if (!pendingText) {
    // 流已结束且队列排空：收尾
    if (!sending.value) finishTyping()
    return
  }
  // 自适应步长：积压越多，一次吐字越多，避免长时间落后于后端
  const len = pendingText.length
  let step = 1
  if (len > 400) step = 16
  else if (len > 160) step = 6
  else if (len > 60) step = 3
  else if (len > 20) step = 2

  const piece = pendingText.slice(0, step)
  pendingText = pendingText.slice(step)
  activeMessage.content += piece
  streamStats.chars = activeMessage.content.length
  scrollToBottom()
}

function pushToken(text) {
  if (!text) return
  pendingText += text
  if (activeMessage) activeMessage.tokens += 1
  streamStats.tokens += 1
  ensureTyping()
}

/** 立即吐出剩余文字并停止定时器 */
function flushAll() {
  if (activeMessage && pendingText) {
    activeMessage.content += pendingText
    streamStats.chars = activeMessage.content.length
  }
  pendingText = ''
  stopTyping()
  // 收尾时抽取正文中的 Mermaid 代码块（方案 B 兜底）
  extractInlineMermaid(activeMessage)
}

function finishTyping() {
  pendingText = ''
  stopTyping()
  scrollToBottom()
}

/* ------------------------------------------------------------- 滚动控制 */

function scrollToBottom(force = false) {
  if (!autoScroll.value && !force) return
  nextTick(() => {
    const el = scroller.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

function onScroll() {
  const el = scroller.value
  if (!el) return
  autoScroll.value = el.scrollHeight - el.scrollTop - el.clientHeight < 60
}

/* --------------------------------------------------------- SSE 事件处理 */

async function handleImageEvent(evt, msg) {
  const type = evt.image_type || evt.type || 'mermaid'
  const code = String(evt.code || evt.content || evt.text || '')
  msg.images.push({ type, code, svg: '', error: '', loading: true, showCode: false })
  const item = msg.images[msg.images.length - 1] // 取 reactive 代理
  scrollToBottom()

  if (type === 'mermaid' && code.trim()) {
    try {
      item.svg = await renderMermaid(code)
    } catch (e) {
      item.error = `Mermaid 渲染失败：${e && e.message ? e.message : e}`
    }
  } else if (!code.trim()) {
    item.error = '后端未返回图形代码'
  } else {
    item.error = `暂不支持在浏览器渲染 ${type} 类型，已展示源码`
    item.showCode = true
  }
  item.loading = false
  scrollToBottom()
}

/**
 * 抽取回答正文里的 ```mermaid 代码块并渲染
 *
 * 方案 B 兜底：模型自绘的 Mermaid 代码块若没有被后端以独立 image 事件推送
 * （例如走了 /api/chat/stream 兜底接口），前端自行抽取渲染，从正文里移除。
 */
function extractInlineMermaid(msg) {
  if (!msg || !msg.content || !/```[ \t]*mermaid/i.test(msg.content)) return
  const { text, blocks } = extractMermaid(msg.content)
  if (!blocks.length) return
  msg.content = text.replace(/\n{3,}/g, '\n\n').trim()
  streamStats.chars = msg.content.length
  blocks.forEach((code) => {
    msg.images.push({ type: 'mermaid', code, svg: '', error: '', loading: true, showCode: false })
    const item = msg.images[msg.images.length - 1] // 取 reactive 代理
    renderMermaid(code)
      .then((svg) => {
        item.svg = svg
      })
      .catch((e) => {
        item.error = `Mermaid 渲染失败：${e && e.message ? e.message : e}`
      })
      .finally(() => {
        item.loading = false
        scrollToBottom()
      })
  })
}

/** 从落库历史中补齐引用来源（后端 SSE 未单独推送 sources 时使用） */
async function syncSourcesFromHistory(conversationId, msg) {
  if (!conversationId) return
  try {
    const data = await getChatHistory(conversationId, true)
    const list = (data && data.messages) || []
    for (let i = list.length - 1; i >= 0; i -= 1) {
      const m = list[i]
      if (m && m.role === 'assistant') {
        const sources = normalizeSources(m.sources)
        if (sources.length && msg && !msg.sources.length) msg.sources = sources
        break
      }
    }
  } catch (e) {
    // 历史接口不可用（如数据库未初始化）时忽略，不影响对话
  }
}

function handleEvent(evt, msg) {
  streamStats.events += 1
  const status = evt && evt.status

  switch (status) {
    case 'token': {
      const text = typeof evt.token === 'string' ? evt.token : evt.text || ''
      modelLoading.value = false
      if (msg.status === 'pending') msg.status = 'streaming'
      pushToken(text)
      break
    }

    case 'agent_log': {
      const log = normalizeLog(evt.log || evt)
      currentLogs.value.push(log)
      if (msg) msg.logs.push(log)
      session.lastAgentLogs = currentLogs.value.slice()
      // 检索/生成节点的输出摘要里常带有结果数量，作为无 sources 时的兜底提示
      break
    }

    case 'image': {
      handleImageEvent(evt, msg)
      break
    }

    case 'sources': {
      const sources = normalizeSources(evt.sources || evt.data || evt.chunks)
      if (sources.length && msg) msg.sources = sources
      break
    }

    case 'model_loading': {
      modelLoading.value = true
      notice.value = evt.message || '模型加载中，首次加载约需 20~60 秒...'
      break
    }

    case 'done': {
      modelLoading.value = false
      notice.value = ''
      if (evt.conversation_id) {
        session.conversationId = evt.conversation_id
        setConversationId(evt.conversation_id)
      }
      if (msg) {
        msg.status = 'done'
        msg.elapsed = Number(((Date.now() - streamStats.startedAt) / 1000).toFixed(2))
      }
      flushAll()
      setLastRun(currentLogs.value, msg ? msg.prompt : '', session.conversationId)
      syncSourcesFromHistory(session.conversationId, msg)
      loadConversations()
      break
    }

    case 'error': {
      modelLoading.value = false
      notice.value = ''
      const text = evt.message || evt.error || '生成失败'
      if (msg) {
        msg.status = 'error'
        msg.error = text
      }
      flushAll()
      ElMessage.error(text)
      break
    }

    case 'info':
    case 'warning': {
      if (evt.message) notice.value = String(evt.message)
      break
    }

    default:
      // 未知事件：不中断流，仅在控制台留痕，便于后续对接
      if (evt && evt.message) notice.value = String(evt.message)
      break
  }
}

/* ------------------------------------------------------------- 发送流程 */

async function handleSend(text) {
  const prompt = String(text !== undefined ? text : input.value).trim()
  if (!prompt) {
    ElMessage.warning('请输入问题')
    return
  }
  if (sending.value) {
    ElMessage.warning('正在生成中，请稍候或先停止')
    return
  }
  if (prompt.length > 4096) {
    ElMessage.warning('问题过长（后端限制 4096 字符）')
    return
  }

  input.value = ''
  autoScroll.value = true

  pushMessage('user', prompt)
  const msg = pushMessage('assistant', '', { status: 'streaming', prompt })

  activeMessage = msg
  pendingText = ''
  sending.value = true
  modelLoading.value = false
  notice.value = ''
  currentLogs.value = []
  streamStats.tokens = 0
  streamStats.chars = 0
  streamStats.events = 0
  streamStats.startedAt = Date.now()
  streamStats.elapsed = 0

  abortController = new AbortController()
  elapsedTimer = setInterval(() => {
    streamStats.elapsed = Number(((Date.now() - streamStats.startedAt) / 1000).toFixed(1))
  }, 100)

  scrollToBottom(true)

  let result = null
  try {
    result = await streamAgentChat(
      prompt,
      session.conversationId,
      (evt) => handleEvent(evt, msg),
      { signal: abortController.signal },
    )
  } catch (e) {
    msg.status = 'error'
    msg.error = e && e.message ? e.message : String(e)
    ElMessage.error(msg.error)
  } finally {
    if (elapsedTimer) {
      clearInterval(elapsedTimer)
      elapsedTimer = null
    }
    flushAll()
    sending.value = false
    abortController = null

    if (result && result.aborted) {
      msg.status = 'aborted'
      if (!msg.content) msg.error = '已停止生成'
    } else if (msg.status === 'streaming' || msg.status === 'pending') {
      // 后端未发送 done 事件（例如回退到 /api/chat/stream）时，流结束即视为完成
      msg.status = 'done'
      msg.elapsed = Number(((Date.now() - streamStats.startedAt) / 1000).toFixed(2))
      setLastRun(currentLogs.value, prompt, session.conversationId)
    }
    if (!msg.content && !msg.error && !msg.images.length) {
      msg.error = '模型未返回任何内容，请检查模型是否加载成功（系统状态页）'
      msg.status = 'error'
    }
    activeMessage = null
    scrollToBottom(true)
  }
}

function handleStop() {
  if (abortController) {
    abortController.abort()
    ElMessage.info('已请求停止生成')
  }
}

function useQuickPrompt(text) {
  if (sending.value) return
  input.value = text
  handleSend(text)
}

/* ------------------------------------------------------------- 会话管理 */

async function loadConversations() {
  try {
    const data = await getChatHistory('', true)
    conversations.value = (data && data.conversations) || []
  } catch (e) {
    conversations.value = []
  }
}

/** 把一条历史绘图记录渲染到指定消息上 */
function pushHistoryImage(msg, rec) {
  const type = rec.image_type || 'mermaid'
  const code = String(rec.code || '')
  if (!code.trim()) return
  msg.images.push({ type, code, svg: '', error: '', loading: true, showCode: false })
  const item = msg.images[msg.images.length - 1] // 取 reactive 代理
  if (type === 'mermaid') {
    renderMermaid(code)
      .then((svg) => {
        item.svg = svg
      })
      .catch((e) => {
        item.error = `Mermaid 渲染失败：${e && e.message ? e.message : e}`
      })
      .finally(() => {
        item.loading = false
      })
  } else {
    item.error = `暂不支持在浏览器渲染 ${type} 类型，已展示源码`
    item.showCode = true
    item.loading = false
  }
}

/**
 * 历史会话图片回填
 *
 * 实时对话的图来自 SSE image 事件；历史记录里图片单独存在 image_records 表，
 * 这里按记录中的 query（触发绘图的那条用户问题）把它挂到其后第一条助手消息上。
 */
function attachHistoryImages(rawList, records, msgList) {
  if (!Array.isArray(records) || !records.length) return
  const findUser = (q, from) => {
    for (let i = from; i < rawList.length; i += 1) {
      if (rawList[i].role === 'user' && String(rawList[i].content || '').trim() === q) return i
    }
    return -1
  }
  let cursor = 0
  records.forEach((rec) => {
    const q = String(rec.query || '').trim()
    let ui = q ? findUser(q, cursor) : -1
    if (ui === -1 && q) ui = findUser(q, 0)
    if (ui === -1) ui = rawList.length - 2
    cursor = ui + 1
    let ai = -1
    for (let j = ui + 1; j < rawList.length; j += 1) {
      if (rawList[j].role === 'assistant') {
        ai = j
        break
      }
    }
    if (ai === -1) {
      for (let j = rawList.length - 1; j >= 0; j -= 1) {
        if (rawList[j].role === 'assistant') {
          ai = j
          break
        }
      }
    }
    if (ai === -1 || !msgList[ai]) return
    pushHistoryImage(msgList[ai], rec)
  })
}

async function openConversation(id) {
  if (!id || loadingHistory.value) return
  loadingHistory.value = true
  try {
    const data = await getChatHistory(id, true)
    const list = (data && data.messages) || []
    messages.value = list.map((m) =>
      createMessage(m.role === 'user' ? 'user' : 'assistant', m.content || '', {
        status: 'done',
        sources: normalizeSources(m.sources),
        time: formatTime(m.created_at),
      }),
    )
    attachHistoryImages(list, (data && data.images) || [], messages.value)
    currentLogs.value = ((data && data.agent_logs) || []).map(normalizeLog)
    session.conversationId = id
    setConversationId(id)
    setLastRun(currentLogs.value, '', id)
    autoScroll.value = true
    scrollToBottom(true)
    ElMessage.success(`已载入会话 ${String(id).slice(0, 8)}（${messages.value.length} 条消息）`)
  } catch (e) {
    ElMessage.error(e && e.message ? e.message : '载入会话失败')
  } finally {
    loadingHistory.value = false
  }
}

function resetToNew() {
  if (sending.value) handleStop()
  flushAll()
  messages.value = []
  currentLogs.value = []
  notice.value = ''
  activeMessage = null
  session.conversationId = ''
  setConversationId('')
  setLastRun([], '', '')
}

function newConversation() {
  resetToNew()
  ElMessage.success('已开启新会话')
}

/** 删除一条历史会话（连同其后端落库的消息与 Agent 日志） */
async function removeConversation(conv) {
  const id = conv.conversation_id
  try {
    await ElMessageBox.confirm(
      `确定删除历史对话《${conv.title || '未命名会话'}》吗？将同时删除其消息与 Agent 日志，且不可恢复。`,
      '删除确认',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch (e) {
    return
  }
  try {
    await deleteConversation(id)
    ElMessage.success('已删除该历史对话')
    // 若删除的是当前正在查看的会话，回到新会话状态
    if (id === session.conversationId) resetToNew()
    await loadConversations()
  } catch (e) {
    // 全局拦截器已提示错误
  }
}

async function clearMessages() {
  if (!messages.value.length) return
  try {
    await ElMessageBox.confirm('确定清空当前页面的对话记录吗？（不会删除后端已落库的数据）', '清空确认', {
      type: 'warning',
      confirmButtonText: '清空',
      cancelButtonText: '取消',
    })
  } catch (e) {
    return
  }
  newConversation()
}

function goAgentFlow(msg) {
  setLastRun(msg && msg.logs && msg.logs.length ? msg.logs : currentLogs.value, msg ? msg.prompt : '', session.conversationId)
  router.push('/agent-flow')
}

async function copyMessage(msg) {
  const text = msg.content || ''
  if (!text) {
    ElMessage.warning('暂无可复制内容')
    return
  }
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
    } else {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      document.body.removeChild(ta)
    }
    ElMessage.success('已复制到剪贴板')
  } catch (e) {
    ElMessage.warning('复制失败，请手动选择文本')
  }
}

/* ------------------------------------------------------------- 生命周期 */

onMounted(() => {
  loadConversations()
  window.addEventListener('keydown', onViewerKeydown)
})

onBeforeUnmount(() => {
  stopTyping()
  if (elapsedTimer) clearInterval(elapsedTimer)
  if (abortController) abortController.abort()
  window.removeEventListener('keydown', onViewerKeydown)
})
</script>

<template>
  <div class="page chat-page">
    <div class="chat-body">
      <!-- ===================== 左：会话列表 ===================== -->
      <aside class="conv-panel">
        <div class="panel-head">
          <span>历史会话</span>
          <el-button size="small" type="primary" plain @click="newConversation">新对话</el-button>
        </div>
        <div class="conv-list">
          <div v-if="!conversations.length" class="empty-hint">暂无历史会话</div>
          <div
            v-for="conv in conversations"
            :key="conv.conversation_id"
            class="conv-item"
            :class="{ active: conv.conversation_id === session.conversationId }"
            @click="openConversation(conv.conversation_id)"
          >
            <div class="conv-main">
              <div class="conv-title">{{ conv.title || '未命名会话' }}</div>
              <div class="conv-time">{{ formatTime(conv.created_at) }} · {{ String(conv.conversation_id).slice(0, 8) }}</div>
            </div>
            <el-button
              class="conv-del"
              link
              size="small"
              type="danger"
              title="删除该历史对话"
              @click.stop="removeConversation(conv)"
            >
              删除
            </el-button>
          </div>
        </div>
        <div class="conv-footer">
          <div class="muted">当前会话</div>
          <div class="conv-id">{{ session.conversationId ? session.conversationId.slice(0, 18) : '（未创建）' }}</div>
        </div>
      </aside>

      <!-- ===================== 中：消息区 ===================== -->
      <section class="msg-panel">
        <div ref="scroller" class="msg-scroll" @scroll="onScroll">
          <div v-if="!messages.length" class="welcome">
            <div class="welcome-title">408 考研知识库问答</div>
            <div class="welcome-sub">
              多 Agent 协同 RAG：输入审核 → 混合检索 → 本地模型流式生成 → 输出审核 → 示意图绘制
            </div>
            <div class="quick-list">
              <div v-for="q in QUICK_PROMPTS" :key="q" class="quick-item" @click="useQuickPrompt(q)">{{ q }}</div>
            </div>
          </div>

          <div v-for="msg in messages" :key="msg.id" class="msg-row" :class="msg.role">
            <div class="avatar" :class="msg.role">{{ msg.role === 'user' ? '我' : 'AI' }}</div>
            <div class="bubble-wrap">
              <div class="bubble" :class="[msg.role, msg.status]">
                <!-- 用户消息：纯文本，保留换行 -->
                <div v-if="msg.role === 'user'" class="user-text">{{ msg.content }}</div>

                <!-- 助手消息：Markdown 渲染 -->
                <template v-else>
                  <div
                    class="md-body"
                    :class="{ streaming: msg.status === 'streaming' }"
                    v-html="renderMarkdown(msg.content)"
                  ></div>

                  <!-- 示意图（Mermaid 渲染） -->
                  <div v-for="(img, idx) in msg.images" :key="`img-${idx}`" class="msg-image">
                    <div class="img-head">
                      <el-tag size="small" :type="img.type === 'mermaid' ? 'success' : 'info'">
                        {{ img.type === 'mermaid' ? 'Mermaid 示意图' : `图形（${img.type}）` }}
                      </el-tag>
                      <div class="img-head-actions">
                        <el-button v-if="img.svg" link size="small" @click="openViewer(img)">全屏查看</el-button>
                        <el-button link size="small" @click="img.showCode = !img.showCode">
                          {{ img.showCode ? '隐藏源码' : '查看源码' }}
                        </el-button>
                      </div>
                    </div>
                    <div v-if="img.loading" class="img-tip"><span class="spin-dot"></span> 正在渲染示意图...</div>
                    <div
                      v-else-if="img.svg"
                      class="mermaid-box clickable"
                      title="点击全屏查看"
                      @click="openViewer(img)"
                      v-html="img.svg"
                    ></div>
                    <div v-else class="img-tip error">{{ img.error || '示意图渲染失败' }}</div>
                    <pre v-if="img.showCode" class="md-pre"><code>{{ img.code }}</code></pre>
                  </div>

                  <!-- 引用来源 -->
                  <div v-if="msg.sources.length" class="sources">
                    <div class="sources-title">引用来源（{{ msg.sources.length }}）</div>
                    <div v-for="src in msg.sources" :key="`src-${msg.id}-${src.index}`" class="source-item">
                      <span class="source-idx">[{{ src.index }}]</span>
                      <span class="source-name">{{ src.doc_name }}</span>
                      <span v-if="src.page !== ''" class="source-meta">第 {{ src.page }} 页</span>
                      <span v-if="src.para_index !== ''" class="source-meta">段落 {{ src.para_index }}</span>
                      <span v-if="src.score !== null && !Number.isNaN(src.score)" class="source-meta">
                        得分 {{ src.score.toFixed(4) }}
                      </span>
                      <div v-if="src.text" class="source-text">{{ src.text }}</div>
                    </div>
                  </div>

                  <!-- 错误提示 -->
                  <el-alert
                    v-if="msg.error"
                    class="msg-alert"
                    :title="msg.error"
                    type="error"
                    :closable="false"
                    show-icon
                  />
                </template>
              </div>

              <div v-if="msg.role === 'assistant'" class="msg-footer">
                <span class="muted">{{ msg.time }}</span>
                <span v-if="msg.tokens" class="muted">{{ msg.tokens }} 个分片 / {{ msg.content.length }} 字</span>
                <span v-if="msg.elapsed" class="muted">耗时 {{ msg.elapsed }} s</span>
                <span v-if="msg.logs.length" class="muted">Agent 日志 {{ msg.logs.length }} 条</span>
                <el-button link size="small" @click="copyMessage(msg)">复制</el-button>
                <el-button v-if="msg.logs.length" link size="small" type="primary" @click="goAgentFlow(msg)">
                  查看链路
                </el-button>
              </div>
            </div>
          </div>
        </div>

        <!-- ===================== 输入区 ===================== -->
        <div class="input-area">
          <div class="status-line">
            <template v-if="sending">
              <span class="spin-dot"></span>
              <span v-if="modelLoading" class="status-text warn">
                {{ notice || '模型加载中，首次加载约需 20~60 秒...' }}
              </span>
              <span v-else-if="currentAgent" class="status-text">
                正在执行：{{ agentLabel(currentAgent.agent_name) }} · {{ currentAgent.action || '-' }}
              </span>
              <span v-else class="status-text">{{ notice || '正在连接后端流式接口...' }}</span>
              <span class="muted">已用 {{ streamStats.elapsed }} s / {{ streamStats.chars }} 字</span>
            </template>
            <template v-else>
              <el-tag size="small" :type="session.modelStatus.loaded ? 'success' : 'info'" effect="plain">
                {{ session.modelStatus.loaded ? '模型已加载' : '模型未加载（提问时自动拉起）' }}
              </el-tag>
              <span class="muted">内存 {{ memoryMb }} MB</span>
              <span v-if="notice" class="status-text">{{ notice }}</span>
              <span v-else class="muted">Enter 发送，Shift + Enter 换行</span>
            </template>
          </div>

          <el-input
            v-model="input"
            type="textarea"
            :rows="3"
            resize="none"
            maxlength="4096"
            show-word-limit
            placeholder="向 408 知识库提问，例如：简述请求分页存储管理中的页面置换算法"
            @keydown.enter.exact.prevent="handleSend()"
          />

          <div class="action-line">
            <div class="row-gap">
              <el-button size="small" @click="newConversation">新对话</el-button>
              <el-button size="small" @click="clearMessages">清空</el-button>
              <el-button size="small" :loading="loadingHistory" @click="loadConversations">刷新会话</el-button>
            </div>
            <div class="row-gap">
              <el-button v-if="sending" size="small" type="danger" plain @click="handleStop">停止生成</el-button>
              <el-button
                type="primary"
                :loading="sending"
                :disabled="!input.trim()"
                @click="handleSend()"
              >
                {{ sending ? '生成中' : '发送' }}
              </el-button>
            </div>
          </div>
        </div>
      </section>

      <!-- ===================== 右：Agent 实时链路 / 引用来源 ===================== -->
      <aside class="side-panel">
        <div class="panel-head">
          <span>实时执行链路</span>
          <span class="muted">共 {{ currentLogs.length }} 条</span>
        </div>

        <div class="current-agent">
          <template v-if="currentAgent">
            <div class="ca-line">
              <span class="spin-dot" v-if="sending"></span>
              <strong>{{ agentLabel(currentAgent.agent_name) }}</strong>
              <el-tag size="small" :type="statusTagType(currentAgent.status)" effect="plain">
                {{ statusLabel(currentAgent.status) }}
              </el-tag>
            </div>
            <div class="ca-sub">
              {{ currentAgent.action || '-' }} · 耗时 {{ formatElapsed(currentAgent.elapsed) }} · 链路合计
              {{ totalElapsed.toFixed(2) }} s
            </div>
            <div v-if="currentAgent.output_summary" class="ca-out">{{ currentAgent.output_summary }}</div>
          </template>
          <div v-else class="empty-hint">发送问题后，这里会实时显示执行到哪个 Agent</div>
        </div>

        <el-tabs v-model="sideTab" class="side-tabs">
          <el-tab-pane label="Agent 日志" name="flow">
            <div class="tab-scroll">
              <el-timeline v-if="currentLogs.length">
                <el-timeline-item
                  v-for="(log, idx) in currentLogs"
                  :key="`log-${idx}`"
                  :type="statusTagType(log.status)"
                  :hollow="log.status === 'skip'"
                  size="normal"
                >
                  <div class="tl-head">
                    <span class="tl-name">{{ agentLabel(log.agent_name) }}</span>
                    <el-tag size="small" :type="statusTagType(log.status)" effect="plain">
                      {{ statusLabel(log.status) }}
                    </el-tag>
                  </div>
                  <div class="tl-body">
                    <div>{{ log.action || '-' }}</div>
                    <div class="muted">
                      耗时 {{ formatElapsed(log.elapsed) }}<span v-if="log.created_at"> · {{ log.created_at }}</span>
                    </div>
                    <div v-if="log.output_summary" class="tl-out">{{ log.output_summary }}</div>
                  </div>
                </el-timeline-item>
              </el-timeline>
              <div v-else class="empty-hint">暂无 Agent 日志</div>
              <el-button
                v-if="currentLogs.length"
                class="side-btn"
                size="small"
                type="primary"
                plain
                @click="goAgentFlow(null)"
              >
                在 Agent 链路页查看流程图
              </el-button>
            </div>
          </el-tab-pane>

          <el-tab-pane :label="`引用来源(${activeSources.length})`" name="sources">
            <div class="tab-scroll">
              <div v-if="!activeSources.length" class="empty-hint">
                本回答暂无落库的引用来源（检索结果会在回答结束后同步）
              </div>
              <div v-for="src in activeSources" :key="`side-src-${src.index}`" class="side-source">
                <div class="ss-head">
                  <span class="source-idx">[{{ src.index }}]</span>
                  <span class="source-name">{{ src.doc_name }}</span>
                </div>
                <div class="muted">
                  <span v-if="src.page !== ''">第 {{ src.page }} 页</span>
                  <span v-if="src.para_index !== ''"> · 段落 {{ src.para_index }}</span>
                  <span v-if="src.score !== null && !Number.isNaN(src.score)"> · 得分 {{ src.score.toFixed(4) }}</span>
                </div>
                <div v-if="src.text" class="source-text">{{ src.text }}</div>
              </div>
            </div>
          </el-tab-pane>
        </el-tabs>
      </aside>
    </div>

    <!-- 图片全屏查看 -->
    <div v-if="viewer.visible" class="img-viewer" @click.self="closeViewer">
      <div class="viewer-toolbar">
        <span class="viewer-title">
          示意图全屏查看
          <em class="viewer-scale">{{ viewerScaleText }}</em>
        </span>
        <div class="viewer-actions" @click.stop>
          <el-button size="small" @click="zoomOut">缩小</el-button>
          <el-button size="small" @click="zoomReset">重置</el-button>
          <el-button size="small" @click="zoomIn">放大</el-button>
          <el-button size="small" type="primary" plain @click="closeViewer">关闭</el-button>
        </div>
      </div>
      <div class="viewer-stage" @click.self="closeViewer" @wheel.prevent="onViewerWheel">
        <div
          v-if="viewer.svg"
          class="viewer-canvas"
          :style="{ transform: `scale(${viewer.scale})` }"
          v-html="viewer.svg"
        ></div>
        <pre v-else class="viewer-code"><code>{{ viewer.code }}</code></pre>
      </div>
      <div class="viewer-hint">滚轮或按钮缩放 · 点击空白或按 Esc 关闭</div>
    </div>
  </div>
</template>

<style scoped>
.chat-page {
  gap: 0;
}

.chat-body {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: 200px minmax(0, 1fr) 320px;
  gap: 12px;
}

/* ---------------- 会话列表 ---------------- */

.conv-panel,
.side-panel {
  background: var(--app-panel);
  border: 1px solid var(--app-border);
  border-radius: var(--app-radius);
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px;
  border-bottom: 1px solid var(--app-border);
  font-size: 13px;
  font-weight: 600;
  flex-shrink: 0;
}

.conv-list {
  flex: 1;
  overflow-y: auto;
  padding: 6px;
}

.conv-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 10px;
  border-radius: 8px;
  cursor: pointer;
  margin-bottom: 4px;
  border: 1px solid transparent;
}

.conv-main {
  flex: 1;
  min-width: 0;
}

.conv-del {
  flex-shrink: 0;
  opacity: 0;
  transition: opacity 0.15s;
}

.conv-item:hover .conv-del,
.conv-item.active .conv-del {
  opacity: 1;
}

.conv-item:hover {
  background: #f5f7fa;
}

.conv-item.active {
  background: #eef4ff;
  border-color: #d3e2ff;
}

.conv-title {
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.conv-time {
  font-size: 11px;
  color: var(--app-text-sub);
  margin-top: 2px;
}

.conv-footer {
  padding: 8px 12px;
  border-top: 1px solid var(--app-border);
  flex-shrink: 0;
}

.conv-id {
  font-family: Consolas, monospace;
  font-size: 11px;
  color: #46505f;
  word-break: break-all;
}

/* ---------------- 消息区 ---------------- */

.msg-panel {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  background: var(--app-panel);
  border: 1px solid var(--app-border);
  border-radius: var(--app-radius);
  overflow: hidden;
}

.msg-scroll {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
}

.welcome {
  max-width: 620px;
  margin: 40px auto;
  text-align: center;
}

.welcome-title {
  font-size: 22px;
  font-weight: 600;
  margin-bottom: 8px;
}

.welcome-sub {
  font-size: 13px;
  color: var(--app-text-sub);
  line-height: 1.8;
  margin-bottom: 20px;
}

.quick-list {
  display: grid;
  gap: 8px;
}

.quick-item {
  padding: 10px 14px;
  border: 1px solid var(--app-border);
  border-radius: 8px;
  background: #fafbfd;
  cursor: pointer;
  font-size: 13px;
  text-align: left;
  transition: all 0.15s;
}

.quick-item:hover {
  border-color: #b9d4ff;
  background: #f0f6ff;
  color: #2f6bd8;
}

.msg-row {
  display: flex;
  gap: 10px;
  margin-bottom: 18px;
}

.msg-row.user {
  flex-direction: row-reverse;
}

.avatar {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  color: #fff;
  background: #8b96a6;
}

.avatar.assistant {
  background: linear-gradient(135deg, #409eff, #6a5cff);
}

.bubble-wrap {
  max-width: calc(100% - 52px);
  min-width: 0;
}

.bubble {
  padding: 10px 14px;
  border-radius: 10px;
  background: #f5f7fa;
  border: 1px solid var(--app-border);
  word-break: break-word;
}

.msg-row.user .bubble {
  background: #e8f1ff;
  border-color: #d3e2ff;
}

.bubble.error {
  border-color: #fbc4c4;
  background: #fef5f5;
}

.user-text {
  white-space: pre-wrap;
  line-height: 1.7;
}

.msg-footer {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 6px;
  flex-wrap: wrap;
}

.msg-row.user .msg-footer {
  justify-content: flex-end;
}

.msg-alert {
  margin-top: 8px;
}

/* ---------------- 示意图 ---------------- */

.msg-image {
  margin-top: 10px;
}

.img-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 6px;
}

.img-tip {
  font-size: 12px;
  color: var(--app-text-sub);
  padding: 12px;
  background: #fafbfd;
  border: 1px dashed var(--app-border);
  border-radius: 8px;
  text-align: center;
}

.img-tip.error {
  color: #c45656;
  border-color: #fbc4c4;
  background: #fef5f5;
}

.img-head-actions {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 4px;
}

.mermaid-box.clickable {
  cursor: zoom-in;
  transition: border-color 0.2s ease;
}

.mermaid-box.clickable:hover {
  border-color: #409eff;
}

/* ---------------- 图片全屏查看 ---------------- */

.img-viewer {
  position: fixed;
  inset: 0;
  z-index: 3000;
  background: rgba(20, 24, 32, 0.85);
  display: flex;
  flex-direction: column;
  padding: 14px 20px;
}

.viewer-toolbar {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.viewer-title {
  font-size: 14px;
  font-weight: 600;
  color: #fff;
}

.viewer-scale {
  font-style: normal;
  font-size: 12px;
  color: #a9b6c7;
  margin-left: 6px;
}

.viewer-actions {
  display: flex;
  gap: 6px;
}

.viewer-stage {
  flex: 1;
  min-height: 0;
  margin: 12px 0;
  overflow: auto;
  display: flex;
  background: #fff;
  border-radius: 10px;
}

.viewer-canvas {
  margin: auto;
  padding: 16px;
  transform-origin: center center;
  transition: transform 0.15s ease;
}

.viewer-canvas svg {
  max-width: 86vw;
  height: auto;
}

.viewer-code {
  margin: auto;
  padding: 16px;
  font-size: 13px;
  text-align: left;
  white-space: pre;
}

.viewer-hint {
  flex: none;
  text-align: center;
  font-size: 12px;
  color: #a9b6c7;
}

/* ---------------- 引用来源 ---------------- */

.sources {
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px dashed var(--app-border);
}

.sources-title {
  font-size: 12px;
  font-weight: 600;
  color: #46505f;
  margin-bottom: 6px;
}

.source-item {
  font-size: 12px;
  padding: 6px 8px;
  background: #fafbfd;
  border: 1px solid var(--app-border);
  border-radius: 6px;
  margin-bottom: 6px;
}

.source-idx {
  color: var(--app-primary);
  font-weight: 600;
  margin-right: 4px;
}

.source-name {
  font-weight: 600;
}

.source-meta {
  color: var(--app-text-sub);
  margin-left: 8px;
}

.source-text {
  margin-top: 4px;
  color: #5a6473;
  line-height: 1.6;
  max-height: 66px;
  overflow: hidden;
}

/* ---------------- 输入区 ---------------- */

.input-area {
  border-top: 1px solid var(--app-border);
  padding: 10px 14px 12px;
  background: #fcfdff;
  flex-shrink: 0;
}

.status-line {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 24px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}

.status-text {
  font-size: 12px;
  color: #2f6bd8;
}

.status-text.warn {
  color: var(--app-warning);
}

.action-line {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 8px;
  gap: 10px;
}

/* ---------------- 右侧链路面板 ---------------- */

.current-agent {
  padding: 10px 12px;
  border-bottom: 1px solid var(--app-border);
  background: #fbfcff;
  flex-shrink: 0;
}

.ca-line {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}

.ca-sub {
  font-size: 12px;
  color: var(--app-text-sub);
  margin-top: 4px;
}

.ca-out {
  font-size: 12px;
  color: #46505f;
  margin-top: 6px;
  background: #f2f6ff;
  border-radius: 6px;
  padding: 6px 8px;
  line-height: 1.6;
  max-height: 78px;
  overflow-y: auto;
  word-break: break-word;
}

.side-tabs {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding: 0 12px;
}

.side-tabs :deep(.el-tabs__content) {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.side-tabs :deep(.el-tab-pane) {
  height: 100%;
}

.tab-scroll {
  height: 100%;
  overflow-y: auto;
  padding: 10px 2px 12px;
}

.tl-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.tl-name {
  font-size: 13px;
  font-weight: 600;
}

.tl-body {
  font-size: 12px;
  color: #46505f;
  margin-top: 2px;
  line-height: 1.6;
}

.tl-out {
  margin-top: 4px;
  padding: 4px 8px;
  background: #f6f8fb;
  border-radius: 6px;
  word-break: break-word;
}

.side-btn {
  margin-top: 10px;
  width: 100%;
}

.side-source {
  padding: 8px 10px;
  border: 1px solid var(--app-border);
  border-radius: 8px;
  margin-bottom: 8px;
  background: #fafbfd;
}

.ss-head {
  font-size: 13px;
  margin-bottom: 2px;
}

/* ---------------- 打字机光标 ---------------- */

.md-body.streaming > :last-child::after,
.md-body.streaming:empty::after {
  content: '';
  display: inline-block;
  width: 7px;
  height: 15px;
  margin-left: 2px;
  vertical-align: -2px;
  background: var(--app-primary);
  animation: caret-blink 1s steps(1, end) infinite;
}

@media (max-width: 1280px) {
  .chat-body {
    grid-template-columns: 180px minmax(0, 1fr) 280px;
  }
}

@media (max-width: 1024px) {
  .chat-body {
    grid-template-columns: minmax(0, 1fr);
  }

  .conv-panel,
  .side-panel {
    display: none;
  }
}
</style>
