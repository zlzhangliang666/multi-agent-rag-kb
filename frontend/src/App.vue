<script setup>
/**
 * 应用外壳：左侧菜单 + 顶部状态栏 + 路由视图
 * 菜单：对话 / 知识库 / Agent 链路 / 系统状态 / 登录
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { getModelStatus } from './api'
import { session, setModelStatus, setUser } from './store/session'

const route = useRoute()
const router = useRouter()

const MENU_ITEMS = [
  { path: '/chat', index: '01', label: '对话', desc: '流式问答' },
  { path: '/knowledge', index: '02', label: '知识库', desc: '文档管理' },
  { path: '/agent-flow', index: '03', label: 'Agent 链路', desc: '执行可视化' },
  { path: '/system', index: '04', label: '系统状态', desc: '模型与内存' },
  { path: '/dashboard', index: '05', label: '数据大屏', desc: '统计总览' },
  { path: '/settings', index: '06', label: '模型设置', desc: 'API Key 与远程模型' },
  { path: '/login', index: '07', label: '登录', desc: '账号' },
]

const activeMenu = computed(() => {
  const matched = MENU_ITEMS.find((item) => route.path.startsWith(item.path))
  return matched ? matched.path : '/chat'
})

const currentTitle = computed(() => (route.meta && route.meta.title) || '对话')

const statusText = computed(() => {
  const st = session.modelStatus
  if (st.active_provider === 'openai') return '远程模型已启用'
  if (st.generating) return '模型生成中'
  if (st.loaded) return '模型已加载'
  if (st.worker_alive) return '模型未加载（子进程就绪）'
  return '子进程未就绪'
})

const statusType = computed(() => {
  const st = session.modelStatus
  if (st.active_provider === 'openai') return 'primary'
  if (st.generating) return 'warning'
  if (st.loaded) return 'success'
  if (st.worker_alive) return 'info'
  return 'danger'
})

const memoryText = computed(() => {
  const mb = Number(session.modelStatus.memory_mb || 0)
  return `${mb.toFixed(0)} MB`
})

const userMenuVisible = ref(false)
let pollTimer = null

async function refreshStatus() {
  try {
    const data = await getModelStatus(true)
    if (data && typeof data === 'object') setModelStatus(data)
  } catch (e) {
    // 后端未启动时静默失败，避免刷屏报错
    setModelStatus({ loaded: false, generating: false, worker_alive: false, memory_mb: 0 })
  }
}

function handleUserCommand(command) {
  userMenuVisible.value = false
  if (command === 'logout') {
    setUser(null)
    ElMessage.success('已退出登录')
    router.push('/login')
  } else if (command === 'system') {
    router.push('/system')
  }
}

onMounted(() => {
  refreshStatus()
  pollTimer = setInterval(refreshStatus, 5000)
})

onBeforeUnmount(() => {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
})
</script>

<template>
  <el-container class="app-shell">
    <el-aside width="220px" class="app-aside">
      <div class="brand">
        <div class="brand-logo">KB</div>
        <div class="brand-text">
          <div class="brand-title">知识库问答系统</div>
          <div class="brand-sub">多 Agent 协同 RAG</div>
        </div>
      </div>

      <el-menu :default-active="activeMenu" router class="app-menu">
        <el-menu-item v-for="item in MENU_ITEMS" :key="item.path" :index="item.path">
          <span class="menu-idx">{{ item.index }}</span>
          <span class="menu-label">
            <span class="menu-name">{{ item.label }}</span>
            <span class="menu-desc">{{ item.desc }}</span>
          </span>
        </el-menu-item>
      </el-menu>

      <div class="aside-footer">
        <div class="footer-line">后端 http://localhost:8000</div>
        <div class="footer-line">离线运行 · 本地模型 · CPU 推理</div>
      </div>
    </el-aside>

    <el-container class="app-body">
      <el-header height="56px" class="app-header">
        <div class="header-left">
          <span class="header-title">{{ currentTitle }}</span>
        </div>
        <div class="header-right">
          <el-tag :type="statusType" effect="dark" size="small" round>{{ statusText }}</el-tag>
          <span class="header-mem">内存 {{ memoryText }}</span>
          <el-dropdown v-if="session.user" trigger="click" @command="handleUserCommand">
            <span class="user-chip">{{ session.user.username }}</span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="system">查看系统状态</el-dropdown-item>
                <el-dropdown-item command="logout" divided>退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
          <el-button v-else size="small" type="primary" plain @click="router.push('/login')">
            登录 / 注册
          </el-button>
        </div>
      </el-header>

      <el-main class="app-main">
        <router-view v-slot="{ Component }">
          <keep-alive :include="['ChatView']">
            <component :is="Component" />
          </keep-alive>
        </router-view>
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
.app-aside {
  display: flex;
  flex-direction: column;
  background: var(--app-aside-bg);
  border-right: 1px solid #171d26;
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 16px 16px 14px;
  border-bottom: 1px solid #2c3543;
}

.brand-logo {
  width: 38px;
  height: 38px;
  border-radius: 9px;
  background: linear-gradient(135deg, #409eff, #6a5cff);
  color: #fff;
  font-weight: 700;
  font-size: 15px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.brand-title {
  color: #fff;
  font-size: 14px;
  font-weight: 600;
  line-height: 1.3;
}

.brand-sub {
  color: #8b96a6;
  font-size: 11px;
  line-height: 1.4;
}

.app-menu {
  flex: 1;
  border-right: none;
  padding: 8px 0;
  --el-menu-bg-color: transparent;
  --el-menu-text-color: #b8c2cf;
  --el-menu-hover-bg-color: #2a3341;
  --el-menu-active-color: #ffffff;
  --el-menu-item-height: 52px;
}

.app-menu :deep(.el-menu-item) {
  margin: 4px 10px;
  border-radius: 8px;
  height: 52px;
  line-height: 1.3;
}

.app-menu :deep(.el-menu-item.is-active) {
  background: linear-gradient(90deg, #3d7eff, #5b6cff);
  color: #fff;
}

.menu-idx {
  font-size: 11px;
  opacity: 0.55;
  margin-right: 10px;
  font-family: Consolas, monospace;
}

.menu-label {
  display: flex;
  flex-direction: column;
}

.menu-name {
  font-size: 14px;
}

.menu-desc {
  font-size: 11px;
  opacity: 0.6;
}

.aside-footer {
  padding: 12px 16px;
  border-top: 1px solid #2c3543;
  color: #6f7b8c;
  font-size: 11px;
  line-height: 1.7;
}

.app-body {
  min-width: 0;
}

.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: var(--app-panel);
  border-bottom: 1px solid var(--app-border);
  padding: 0 18px;
}

.header-left {
  display: flex;
  align-items: baseline;
  gap: 10px;
  min-width: 0;
}

.header-title {
  font-size: 16px;
  font-weight: 600;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.header-mem {
  font-size: 12px;
  color: var(--app-text-sub);
  font-family: Consolas, monospace;
}

.user-chip {
  cursor: pointer;
  padding: 4px 12px;
  border-radius: 14px;
  background: #eef3ff;
  color: #3370ff;
  font-size: 12px;
  outline: none;
}

.user-chip:hover {
  background: #e0eaff;
}

@media (max-width: 900px) {
  .menu-desc {
    display: none;
  }
}
</style>
