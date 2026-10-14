/**
 * 统一 API 封装
 *
 * 1. axios 实例：baseURL=/api（开发环境由 Vite 代理到 http://localhost:8000）
 *    - 请求拦截：附带登录用户名（后端为简化实现，无 JWT）
 *    - 响应拦截：直接返回 data；统一提取 FastAPI 的 { detail } 错误信息
 * 2. streamAgentChat：使用 fetch + ReadableStream 手工解析 SSE（text/event-stream）
 *    逐行处理 `data: {json}`，支持 `event:` 字段、多行 data、心跳注释行、CRLF、中止
 */
import axios from 'axios'
import { ElMessage } from 'element-plus'
import { session } from '../store/session'

/** 主对话流接口（TRAE_PROMPT 约定的多 Agent SSE 接口） */
export const AGENT_CHAT_URL = '/api/agent/chat'
/** 兜底接口：当主接口未部署（404/405）时回退到阶段 1 的流式问答接口 */
export const CHAT_STREAM_FALLBACK_URL = '/api/chat/stream'

/**
 * 从 axios 错误对象中提取可读的中文错误信息
 */
export function extractErrorMessage(error) {
  if (!error) return '未知错误'
  const resp = error.response
  if (resp) {
    const data = resp.data
    if (data) {
      if (typeof data === 'string') {
        // FastAPI 在非 JSON 场景下可能直接返回文本
        const m = data.match(/"detail"\s*:\s*"([^"]+)"/)
        if (m) return m[1]
        return data.slice(0, 200)
      }
      if (data.detail) {
        return typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)
      }
      if (data.message) return String(data.message)
    }
    return `请求失败（HTTP ${resp.status}）`
  }
  if (error.code === 'ECONNABORTED') return '请求超时，请检查后端是否已启动（http://localhost:8000）'
  if (error.message === 'Network Error') return '无法连接后端服务，请确认 http://localhost:8000 已启动'
  return error.message || '未知错误'
}

const http = axios.create({
  baseURL: '/api',
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
})

http.interceptors.request.use((config) => {
  if (session.user && session.user.username) {
    config.headers['X-Username'] = session.user.username
  }
  return config
})

http.interceptors.response.use(
  (response) => response.data,
  (error) => {
    const message = extractErrorMessage(error)
    // 允许调用方通过 { silent: true } 关闭全局报错提示（轮询类请求）
    if (!error.config || error.config.silent !== true) {
      ElMessage.error(message)
    }
    return Promise.reject(Object.assign(new Error(message), { response: error.response }))
  },
)

/* ============================== 认证 ============================== */

export function register(username, password) {
  return http.post('/auth/register', { username, password })
}

export function login(username, password) {
  return http.post('/auth/login', { username, password })
}

/* ============================== 知识库 ============================== */

export function listDocs(silent = false) {
  return http.get('/kb/list', { silent })
}

export function deleteDoc(docId) {
  return http.delete(`/kb/${encodeURIComponent(docId)}`)
}

/**
 * 上传文档（multipart/form-data）
 * @param {File} file 文件对象
 * @param {(percent:number)=>void} onProgress 上传进度回调（0-100）
 */
export function uploadDoc(file, onProgress) {
  const form = new FormData()
  form.append('file', file)
  return http.post('/kb/upload', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    // 解析 + 分块 + 向量化耗时较长，单独放宽超时
    timeout: 600000,
    onUploadProgress: (evt) => {
      if (onProgress && evt.total) {
        onProgress(Math.round((evt.loaded * 100) / evt.total))
      }
    },
  })
}

/* ============================ Agent 日志 ============================ */

export function getAgentLogs(conversationId, silent = false) {
  return http.get(`/agent/logs/${encodeURIComponent(conversationId)}`, { silent })
}

/* ============================== 对话 ============================== */

/**
 * 历史对话
 * - 不传 conversationId：返回 { conversations: [...] }
 * - 传 conversationId：返回 { messages: [...], agent_logs: [...] }
 */
export function getChatHistory(conversationId, silent = false) {
  return http.get('/chat/history', {
    params: conversationId ? { conversation_id: conversationId } : {},
    silent,
  })
}

/** 删除历史会话（连同其消息 / Agent 日志 / 绘图记录，不可恢复） */
export function deleteConversation(conversationId) {
  return http.delete(`/chat/conversations/${encodeURIComponent(conversationId)}`)
}

/* ============================== 模型 ============================== */

export function getModelStatus(silent = true) {
  return http.get('/model/status', { silent, timeout: 10000 })
}

export function loadModel() {
  // 模型加载（含子进程拉起）可能耗时较久
  return http.post('/model/load', {}, { timeout: 300000 })
}

export function unloadModel() {
  return http.post('/model/unload', {}, { timeout: 60000 })
}

/* ============================ LLM 配置 ============================ */

/** 读取 LLM 配置（api_key 已脱敏，仅返回末 4 位） */
export function getLLMConfig(silent = true) {
  return http.get('/llm/config', { silent })
}

/** 保存 LLM 配置（api_key 传空字符串表示保持原值不变） */
export function saveLLMConfig(payload) {
  return http.put('/llm/config', payload)
}

/** 测试远程模型连通性 */
export function testLLMConfig(payload) {
  return http.post('/llm/test', payload, { timeout: 60000 })
}

/* ============================== 统计 ============================== */

export function getDashboard(silent = false) {
  return http.get('/stats/dashboard', { silent })
}

/** 数据大屏总览（文档/消息/会话/Agent 调用/近7天趋势等一次性聚合） */
export function getStatsOverview(silent = true) {
  return http.get('/stats/overview', { silent })
}

/* ==========================================================================
   SSE：fetch + ReadableStream 手工解析
   ========================================================================== */

/**
 * 事件归一化
 *
 * 后端不同版本的事件字段略有差异（例如 worker 直出的 token 事件
 * 形如 {"token": "...", "status": "generating"}），这里统一映射为
 * status ∈ { agent_log, token, image, done, error, ... }
 */
export function normalizeSSEEvent(payload, eventName) {
  if (!payload || typeof payload !== 'object') {
    return { status: eventName || 'raw', text: String(payload ?? '') }
  }
  const data = Object.assign({}, payload)
  let status = data.status || eventName || ''

  const hasToken = typeof data.token === 'string' && data.token.length > 0
  const hasLog = !!data.log || (!!data.agent_name && !!data.action)
  const hasImage = !!data.image_type || (typeof data.code === 'string' && data.code.includes('-->'))

  if (hasLog && status !== 'error') {
    status = 'agent_log'
  } else if (hasToken && ['generating', 'token', 'message', 'chunk', ''].includes(status)) {
    status = 'token'
  } else if (hasImage && ['image', 'diagram', ''].includes(status)) {
    status = 'image'
  } else if (!status) {
    if (data.conversation_id && Object.keys(data).length <= 2) status = 'done'
    else if (data.message) status = 'info'
    else status = 'message'
  }

  data.status = status
  return data
}

/**
 * 创建一个 SSE 行解析器
 *
 * SSE 帧格式：若干 `field: value` 行，以空行结束一帧。
 * 本解析器只关心 data / event 字段，忽略以 `:` 开头的心跳注释行。
 *
 * @param {(evt:object, raw:string)=>void} emit 每解析出一帧调用一次
 */
export function createSSEParser(emit) {
  let buffer = ''
  let dataLines = []
  let eventName = ''

  const dispatch = () => {
    if (dataLines.length === 0) {
      eventName = ''
      return
    }
    const raw = dataLines.join('\n')
    const name = eventName
    dataLines = []
    eventName = ''
    if (!raw.trim()) return

    let payload = null
    try {
      payload = JSON.parse(raw)
    } catch (e) {
      payload = null
    }
    if (payload && typeof payload === 'object') {
      emit(normalizeSSEEvent(payload, name), raw)
    } else {
      // 非 JSON 数据：按纯文本 token 处理，保证流式文本不丢
      emit({ status: name === 'error' ? 'error' : 'token', token: raw, text: raw, message: raw }, raw)
    }
  }

  const handleLine = (line) => {
    if (line === '') {
      dispatch()
      return
    }
    if (line.startsWith(':')) return // 心跳 / 注释
    const idx = line.indexOf(':')
    const field = idx === -1 ? line : line.slice(0, idx)
    let value = idx === -1 ? '' : line.slice(idx + 1)
    if (value.startsWith(' ')) value = value.slice(1)

    if (field === 'data') dataLines.push(value)
    else if (field === 'event') eventName = value
    // id / retry 字段本项目不使用，直接忽略
  }

  return {
    /** 喂入一段解码后的文本 */
    feed(chunk) {
      if (!chunk) return
      buffer += chunk
      let nl = buffer.indexOf('\n')
      while (nl !== -1) {
        let line = buffer.slice(0, nl)
        buffer = buffer.slice(nl + 1)
        if (line.endsWith('\r')) line = line.slice(0, -1)
        handleLine(line)
        nl = buffer.indexOf('\n')
      }
    },
    /** 流结束时调用，处理没有以空行收尾的最后一帧 */
    close() {
      if (buffer) {
        const line = buffer.endsWith('\r') ? buffer.slice(0, -1) : buffer
        buffer = ''
        handleLine(line)
      }
      dispatch()
    },
  }
}

/**
 * 流式对话（SSE）
 *
 * @param {string} prompt 用户问题
 * @param {string} conversationId 会话 ID（首次可为空，由后端在 done 事件中返回）
 * @param {(evt:object, raw:string)=>void} onEvent 每收到一个事件回调一次
 *        evt.status 取值：agent_log / token / image / done / error / model_loading / info
 * @param {{signal?:AbortSignal}} options 可传入 AbortSignal 实现「停止生成」
 * @returns {Promise<{ok:boolean, aborted:boolean, httpStatus:number, url:string, events:number, error:string}>}
 */
export async function streamAgentChat(prompt, conversationId = '', onEvent = () => {}, options = {}) {
  const signal = options.signal
  const body = JSON.stringify({
    prompt,
    conversation_id: conversationId || null,
  })

  const buildHeaders = () => {
    const headers = {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
      'Cache-Control': 'no-cache',
    }
    if (session.user && session.user.username) headers['X-Username'] = session.user.username
    return headers
  }

  const result = { ok: false, aborted: false, httpStatus: 0, url: AGENT_CHAT_URL, events: 0, error: '' }

  let count = 0
  const emit = (evt, raw) => {
    count += 1
    result.events = count
    try {
      onEvent(evt, raw)
    } catch (e) {
      // 视图层回调异常不应中断整个流
      console.error('[SSE] onEvent 处理异常:', e)
    }
  }

  let response = null
  try {
    response = await fetch(AGENT_CHAT_URL, {
      method: 'POST',
      headers: buildHeaders(),
      body,
      signal,
    })
    result.httpStatus = response.status
    // 主接口未部署时回退到 /api/chat/stream，保证对话功能始终可用
    if (response.status === 404 || response.status === 405) {
      emit({ status: 'info', message: '后端未提供 /api/agent/chat，已自动回退到 /api/chat/stream' })
      response = await fetch(CHAT_STREAM_FALLBACK_URL, {
        method: 'POST',
        headers: buildHeaders(),
        body,
        signal,
      })
      result.url = CHAT_STREAM_FALLBACK_URL
      result.httpStatus = response.status
    }
  } catch (err) {
    if (err && err.name === 'AbortError') {
      result.aborted = true
      result.ok = true
      return result
    }
    result.error = err && err.message ? err.message : String(err)
    emit({ status: 'error', message: `无法连接后端流式接口：${result.error}` })
    return result
  }

  if (!response.ok) {
    let detail = ''
    try {
      detail = await response.text()
    } catch (e) {
      detail = ''
    }
    result.error = detail ? detail.slice(0, 300) : `HTTP ${response.status}`
    emit({ status: 'error', message: `流式接口返回错误（HTTP ${response.status}）${detail ? '：' + result.error : ''}` })
    return result
  }

  if (!response.body || typeof response.body.getReader !== 'function') {
    result.error = '当前浏览器不支持 ReadableStream，无法解析 SSE'
    emit({ status: 'error', message: result.error })
    return result
  }

  const contentType = response.headers.get('content-type') || ''
  if (contentType && !contentType.includes('text/event-stream')) {
    // 后端返回的是普通 JSON（例如错误体），一次性读取后转成 error 事件
    let text = ''
    try {
      text = await response.text()
    } catch (e) {
      text = ''
    }
    let message = text
    try {
      const parsed = JSON.parse(text)
      message = parsed.detail || parsed.message || text
    } catch (e) {
      /* 保持原文本 */
    }
    result.error = message.slice(0, 300)
    emit({ status: 'error', message: typeof message === 'string' ? message : JSON.stringify(message) })
    return result
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  const parser = createSSEParser(emit)

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      parser.feed(decoder.decode(value, { stream: true }))
    }
    parser.feed(decoder.decode())
    parser.close()
    result.ok = true
  } catch (err) {
    if (err && err.name === 'AbortError') {
      result.aborted = true
      result.ok = true
      parser.close()
    } else {
      result.error = err && err.message ? err.message : String(err)
      emit({ status: 'error', message: `流式读取中断：${result.error}` })
    }
  } finally {
    try {
      reader.releaseLock()
    } catch (e) {
      /* 忽略 */
    }
  }

  return result
}

export default http
