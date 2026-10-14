/**
 * 轻量全局会话状态（不引入 Pinia/Vuex，用 reactive 单例即可满足需求）
 *
 * 职责：
 * 1. 保存登录用户（localStorage 持久化）
 * 2. 保存当前 conversation_id（sessionStorage 持久化，刷新页面不丢会话）
 * 3. 保存最近一次运行的 agent_log，供「Agent 链路」页免请求直接回看
 * 4. 缓存模型状态，供顶栏与系统状态页共用
 */
import { reactive } from 'vue'

const USER_KEY = 'rag_user'
const CONV_KEY = 'rag_conversation_id'

function readUser() {
  try {
    const raw = localStorage.getItem(USER_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === 'object' ? parsed : null
  } catch (e) {
    return null
  }
}

function readConversationId() {
  try {
    return sessionStorage.getItem(CONV_KEY) || ''
  } catch (e) {
    return ''
  }
}

export const session = reactive({
  user: readUser(),
  conversationId: readConversationId(),
  /** 最近一次问答收集到的 agent_log 列表 */
  lastAgentLogs: [],
  /** 最近一次问答的用户输入 */
  lastPrompt: '',
  /** 最近一次问答的会话 ID（用于链路页默认选中） */
  lastRunConversationId: '',
  /** 模型状态缓存，由 App.vue 定时刷新 */
  modelStatus: {
    loaded: false,
    generating: false,
    worker_alive: false,
    memory_mb: 0,
    worker_pid: null,
    idle_seconds: null,
    remaining_before_unload: null,
    error: null,
  },
})

export function setUser(user) {
  session.user = user || null
  try {
    if (user) localStorage.setItem(USER_KEY, JSON.stringify(user))
    else localStorage.removeItem(USER_KEY)
  } catch (e) {
    /* localStorage 不可用时忽略 */
  }
}

export function isLogin() {
  return !!session.user && !!session.user.username
}

export function setConversationId(id) {
  session.conversationId = id || ''
  try {
    if (id) sessionStorage.setItem(CONV_KEY, id)
    else sessionStorage.removeItem(CONV_KEY)
  } catch (e) {
    /* 忽略 */
  }
}

export function setLastRun(logs, prompt, conversationId) {
  session.lastAgentLogs = Array.isArray(logs) ? logs.slice() : []
  session.lastPrompt = prompt || ''
  session.lastRunConversationId = conversationId || session.conversationId || ''
}

export function setModelStatus(status) {
  if (!status || typeof status !== 'object') return
  Object.assign(session.modelStatus, {
    loaded: false,
    generating: false,
    worker_alive: false,
    memory_mb: 0,
    worker_pid: null,
    idle_seconds: null,
    remaining_before_unload: null,
    error: null,
  }, status)
}
