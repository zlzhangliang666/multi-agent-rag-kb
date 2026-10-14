<script setup>
/**
 * 登录 / 注册页
 * 后端 /api/auth/* 为简化实现（SHA-256 口令散列，不下发 JWT），
 * 因此前端仅把用户名与 user_id 保存在 localStorage，用于界面展示与请求头透传。
 * 本页面不包含任何预置账号或密码，所有凭据均由用户自行输入。
 */
import { computed, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { login, register } from '../api'
import { isLogin, session, setUser } from '../store/session'

defineOptions({ name: 'LoginView' })

const router = useRouter()
const route = useRoute()

const activeTab = ref('login')
const submitting = ref(false)
const loginFormRef = ref(null)
const registerFormRef = ref(null)

const loginForm = reactive({ username: '', password: '' })
const registerForm = reactive({ username: '', password: '', confirm: '' })

const loginRules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 2, max: 32, message: '用户名长度 2 ~ 32 个字符', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 4, max: 64, message: '密码长度 4 ~ 64 个字符', trigger: 'blur' },
  ],
}

const registerRules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 2, max: 32, message: '用户名长度 2 ~ 32 个字符', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 4, max: 64, message: '密码长度 4 ~ 64 个字符', trigger: 'blur' },
  ],
  confirm: [
    { required: true, message: '请再次输入密码', trigger: 'blur' },
    {
      validator: (rule, value, callback) => {
        if (value !== registerForm.password) callback(new Error('两次输入的密码不一致'))
        else callback()
      },
      trigger: 'blur',
    },
  ],
}

const tipText = computed(() =>
  activeTab.value === 'login' ? '登录后即可开始问答，账号数据保存在本地 SQLite' : '注册成功后会自动为你登录',
)

const alreadyLogin = computed(() => isLogin())

function afterAuthSuccess(res, fallbackUsername) {
  setUser({
    username: (res && res.username) || fallbackUsername || '',
    user_id: res && res.user_id !== undefined ? res.user_id : null,
  })
  ElMessage.success((res && res.message) || '操作成功')
  const redirect = route.query && route.query.redirect
  router.push(typeof redirect === 'string' && redirect.startsWith('/') ? redirect : '/chat')
}

async function handleLogin() {
  if (!loginFormRef.value) return
  try {
    await loginFormRef.value.validate()
  } catch (e) {
    return
  }
  submitting.value = true
  try {
    const res = await login(loginForm.username.trim(), loginForm.password)
    afterAuthSuccess(res, loginForm.username.trim())
  } catch (e) {
    // 错误提示（如「用户名或密码错误」）已由 axios 响应拦截器统一弹出
  } finally {
    submitting.value = false
  }
}

async function handleRegister() {
  if (!registerFormRef.value) return
  try {
    await registerFormRef.value.validate()
  } catch (e) {
    return
  }
  submitting.value = true
  const username = registerForm.username.trim()
  try {
    const res = await register(username, registerForm.password)
    ElMessage.success((res && res.message) || '注册成功')
    // 注册成功后自动登录一次，减少用户操作
    try {
      const loginRes = await login(username, registerForm.password)
      afterAuthSuccess(loginRes, username)
    } catch (e) {
      loginForm.username = username
      activeTab.value = 'login'
      ElMessage.info('注册成功，请使用刚才的密码登录')
    }
  } catch (e) {
    // 错误提示（如「用户名已存在」）已由拦截器弹出
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <div class="login-card">
      <div class="login-brand">
        <div class="brand-logo">408</div>
        <div>
          <div class="brand-title">知识库问答系统</div>
          <div class="brand-sub">多 Agent 协同 RAG · 本地离线运行</div>
        </div>
      </div>

      <el-alert
        v-if="alreadyLogin"
        class="mb12"
        type="success"
        :closable="false"
        show-icon
        :title="`本机已保存登录身份：${session.user ? session.user.username : ''}`"
        description="如需切换账号，直接用新的用户名密码登录即可覆盖。"
      />

      <el-tabs v-model="activeTab" stretch class="login-tabs">
        <el-tab-pane label="登录" name="login">
          <el-form
            ref="loginFormRef"
            :model="loginForm"
            :rules="loginRules"
            label-position="top"
            size="large"
            @submit.prevent="handleLogin"
          >
            <el-form-item label="用户名" prop="username">
              <el-input v-model="loginForm.username" placeholder="请输入用户名" clearable autocomplete="username" />
            </el-form-item>
            <el-form-item label="密码" prop="password">
              <el-input
                v-model="loginForm.password"
                type="password"
                placeholder="请输入密码"
                show-password
                autocomplete="current-password"
                @keyup.enter="handleLogin"
              />
            </el-form-item>
            <el-button class="submit-btn" type="primary" size="large" :loading="submitting" @click="handleLogin">
              登录
            </el-button>
          </el-form>
        </el-tab-pane>

        <el-tab-pane label="注册" name="register">
          <el-form
            ref="registerFormRef"
            :model="registerForm"
            :rules="registerRules"
            label-position="top"
            size="large"
            @submit.prevent="handleRegister"
          >
            <el-form-item label="用户名" prop="username">
              <el-input v-model="registerForm.username" placeholder="2 ~ 32 个字符" clearable autocomplete="username" />
            </el-form-item>
            <el-form-item label="密码" prop="password">
              <el-input
                v-model="registerForm.password"
                type="password"
                placeholder="4 ~ 64 个字符"
                show-password
                autocomplete="new-password"
              />
            </el-form-item>
            <el-form-item label="确认密码" prop="confirm">
              <el-input
                v-model="registerForm.confirm"
                type="password"
                placeholder="请再次输入密码"
                show-password
                autocomplete="new-password"
                @keyup.enter="handleRegister"
              />
            </el-form-item>
            <el-button class="submit-btn" type="primary" size="large" :loading="submitting" @click="handleRegister">
              注册并登录
            </el-button>
          </el-form>
        </el-tab-pane>
      </el-tabs>

      <div class="login-footer">
        <span class="muted">{{ tipText }}</span>
        <el-button link size="small" type="primary" @click="router.push('/chat')">直接进入对话页</el-button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #eef3fb 0%, #f7f8fc 60%, #eef1f8 100%);
  border-radius: var(--app-radius);
  overflow-y: auto;
  padding: 20px;
}

.login-card {
  width: 400px;
  max-width: 100%;
  background: var(--app-panel);
  border: 1px solid var(--app-border);
  border-radius: 14px;
  padding: 24px 26px 18px;
  box-shadow: 0 12px 32px rgba(31, 39, 51, 0.08);
}

.login-brand {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 14px;
}

.brand-logo {
  width: 44px;
  height: 44px;
  border-radius: 10px;
  background: linear-gradient(135deg, #409eff, #6a5cff);
  color: #fff;
  font-weight: 700;
  font-size: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.brand-title {
  font-size: 17px;
  font-weight: 600;
}

.brand-sub {
  font-size: 12px;
  color: var(--app-text-sub);
  margin-top: 2px;
}

.mb12 {
  margin-bottom: 12px;
}

.login-tabs :deep(.el-tabs__item) {
  font-size: 15px;
}

.submit-btn {
  width: 100%;
  margin-top: 4px;
}

.login-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-top: 12px;
}
</style>
