import { createRouter, createWebHistory } from 'vue-router'

// 对话页是最高优先级页面，直接静态引入，保证首屏即可用
import ChatView from '../views/ChatView.vue'

const routes = [
  { path: '/', redirect: '/chat' },
  {
    path: '/chat',
    name: 'chat',
    component: ChatView,
    meta: { title: '对话', index: '01' },
  },
  {
    path: '/knowledge',
    name: 'knowledge',
    component: () => import('../views/KnowledgeBaseView.vue'),
    meta: { title: '知识库', index: '02' },
  },
  {
    path: '/agent-flow',
    name: 'agent-flow',
    component: () => import('../views/AgentFlowView.vue'),
    meta: { title: 'Agent 链路', index: '03' },
  },
  {
    path: '/system',
    name: 'system',
    component: () => import('../views/SystemStatusView.vue'),
    meta: { title: '系统状态', index: '04' },
  },
  {
    path: '/dashboard',
    name: 'dashboard',
    component: () => import('../views/DataDashboardView.vue'),
    meta: { title: '数据大屏', index: '05' },
  },
  {
    path: '/settings',
    name: 'settings',
    component: () => import('../views/ModelSettingsView.vue'),
    meta: { title: '模型设置', index: '06' },
  },
  {
    path: '/login',
    name: 'login',
    component: () => import('../views/LoginView.vue'),
    meta: { title: '登录', index: '07' },
  },
  // 未匹配到的路径统一回到对话页
  { path: '/:pathMatch(.*)*', redirect: '/chat' },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.afterEach((to) => {
  const title = to.meta && to.meta.title
  document.title = title ? `${title} - 知识库问答系统` : '知识库问答系统'
})

export default router
