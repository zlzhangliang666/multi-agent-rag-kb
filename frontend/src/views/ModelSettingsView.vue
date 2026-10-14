<script setup>
/**
 * 模型设置页
 *
 * - 默认使用本地模型（Qwen2.5 + llama.cpp，离线 CPU 推理）
 * - 可切换为 OpenAI 兼容协议的远程模型（DeepSeek / Moonshot / 智谱 / Ollama / vLLM 等）
 * - API Key 只保存在后端 SQLite，读取时仅脱敏回显末 4 位，前端不回传明文
 * - 切到远程时后端会自动卸载本地模型以释放内存；远程失败可选自动回退本地
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { getLLMConfig, saveLLMConfig, testLLMConfig } from '../api'

defineOptions({ name: 'ModelSettingsView' })

const PRESETS = [
  { label: 'OpenAI', base_url: 'https://api.openai.com/v1', model: 'gpt-4o-mini' },
  { label: 'DeepSeek', base_url: 'https://api.deepseek.com/v1', model: 'deepseek-chat' },
  { label: 'Moonshot', base_url: 'https://api.moonshot.cn/v1', model: 'moonshot-v1-8k' },
  { label: '智谱 GLM', base_url: 'https://open.bigmodel.cn/api/paas/v4', model: 'glm-4-flash' },
  { label: 'Ollama（本地）', base_url: 'http://localhost:11434/v1', model: 'qwen2.5:7b' },
  { label: 'vLLM / LM Studio', base_url: 'http://localhost:8001/v1', model: 'local-model' },
]

const form = reactive({
  provider: 'local',
  base_url: 'https://api.openai.com/v1',
  model: 'gpt-4o-mini',
  api_key: '', // 留空表示保持后端已保存的值不变
  temperature: 0.7,
  max_tokens: 1024,
  fallback_to_local: false,
})

const meta = reactive({
  api_key_masked: '',
  has_api_key: false,
  active_provider: 'local',
})

const loading = ref(false)
const saving = ref(false)
const testing = ref(false)
const testResult = ref(null) // { status:'ok'|'error', message }
const loadError = ref('')

const isRemote = computed(() => form.provider === 'openai')
const activeTag = computed(() =>
  meta.active_provider === 'openai'
    ? { text: '远程模型（OpenAI 兼容）', type: 'primary' }
    : { text: '本地模型（Qwen2.5 · 离线 CPU）', type: 'success' },
)
const keyPlaceholder = computed(() =>
  meta.has_api_key ? `已保存：${meta.api_key_masked}（留空则不修改）` : '请输入 API Key（如 sk-...）',
)

function applyConfig(data) {
  if (!data || typeof data !== 'object') return
  form.provider = data.provider || 'local'
  form.base_url = data.base_url || 'https://api.openai.com/v1'
  form.model = data.model || 'gpt-4o-mini'
  form.temperature = data.temperature ?? 0.7
  form.max_tokens = data.max_tokens ?? 1024
  form.fallback_to_local = !!data.fallback_to_local
  form.api_key = '' // 明文不回传，始终留空等待用户重新输入
  meta.api_key_masked = data.api_key_masked || ''
  meta.has_api_key = !!data.has_api_key
  meta.active_provider = data.active_provider || 'local'
  testResult.value = null
}

async function loadConfig() {
  loading.value = true
  loadError.value = ''
  try {
    const data = await getLLMConfig(true)
    applyConfig(data)
  } catch (e) {
    loadError.value = (e && e.message) || '读取配置失败'
  } finally {
    loading.value = false
  }
}

function applyPreset(preset) {
  form.base_url = preset.base_url
  form.model = preset.model
}

async function handleSave() {
  if (isRemote.value && !form.api_key && !meta.has_api_key) {
    ElMessage.warning('启用远程模型前请先填写 API Key')
    return
  }
  saving.value = true
  try {
    const data = await saveLLMConfig({
      provider: form.provider,
      api_key: form.api_key,
      base_url: form.base_url,
      model: form.model,
      temperature: form.temperature,
      max_tokens: form.max_tokens,
      fallback_to_local: form.fallback_to_local,
    })
    applyConfig(data)
    ElMessage.success(
      data.active_provider === 'openai'
        ? '已切换为远程模型，本地模型已自动卸载'
        : '已保存配置，当前使用本地模型',
    )
  } catch (e) {
    ElMessage.error((e && e.message) || '保存失败')
  } finally {
    saving.value = false
  }
}

async function handleTest() {
  if (!form.api_key && !meta.has_api_key) {
    ElMessage.warning('请先填写 API Key（或先保存后再测试）')
    return
  }
  testing.value = true
  testResult.value = null
  try {
    const res = await testLLMConfig({
      api_key: form.api_key,
      base_url: form.base_url,
      model: form.model,
    })
    testResult.value = res
    if (res.status === 'ok') ElMessage.success(res.message || '连接成功')
    else ElMessage.error(res.message || '连接失败')
  } catch (e) {
    testResult.value = { status: 'error', message: (e && e.message) || '测试请求失败' }
    ElMessage.error(testResult.value.message)
  } finally {
    testing.value = false
  }
}

onMounted(loadConfig)
</script>

<template>
  <div class="page">
    <div class="page-scroll">
      <!-- 当前生效模型 -->
      <div class="panel mb">
        <div class="panel-title">
          <span>当前生效模型</span>
          <span class="sub">配置保存在后端 SQLite，API Key 仅脱敏回显</span>
        </div>
        <div class="row-gap">
          <el-tag :type="activeTag.type" effect="dark" size="small" round>{{ activeTag.text }}</el-tag>
          <span class="muted" v-if="meta.active_provider === 'openai'">
            远程端点：{{ form.base_url }} · 模型：{{ form.model }}
          </span>
          <span class="muted" v-else>
            未配置远程 API Key 时，统一使用本地模型；在「系统状态」页可查看内存与加载情况。
          </span>
          <span class="spacer"></span>
          <el-button size="small" :loading="loading" @click="loadConfig">重新读取</el-button>
        </div>
        <el-alert
          v-if="loadError"
          class="mt"
          type="error"
          :closable="false"
          show-icon
          :title="`读取配置失败：${loadError}`"
          description="请确认后端已启动（python backend/main.py，监听 http://localhost:8000）。"
        />
      </div>

      <!-- 配置表单 -->
      <div class="panel mb">
        <div class="panel-title">
          <span>模型配置</span>
          <span class="sub">仅支持 OpenAI 兼容协议（/chat/completions 流式）</span>
        </div>

        <el-form label-width="112px" label-position="right" class="cfg-form">
          <el-form-item label="模型来源">
            <el-radio-group v-model="form.provider">
              <el-radio-button value="local">本地模型</el-radio-button>
              <el-radio-button value="openai">远程模型</el-radio-button>
            </el-radio-group>
          </el-form-item>

          <template v-if="isRemote">
            <el-form-item label="快速预设">
              <div class="row-gap">
                <el-button
                  v-for="p in PRESETS"
                  :key="p.label"
                  size="small"
                  plain
                  @click="applyPreset(p)"
                >
                  {{ p.label }}
                </el-button>
              </div>
            </el-form-item>

            <el-form-item label="Base URL">
              <el-input
                v-model="form.base_url"
                placeholder="https://api.openai.com/v1"
                clearable
              />
              <div class="hint">填到 /v1 即可，无需带 /chat/completions。</div>
            </el-form-item>

            <el-form-item label="模型名称">
              <el-input v-model="form.model" placeholder="gpt-4o-mini" clearable />
            </el-form-item>

            <el-form-item label="API Key">
              <el-input
                v-model="form.api_key"
                type="password"
                show-password
                :placeholder="keyPlaceholder"
                clearable
              />
              <div class="hint">
                仅保存在后端，不会明文回传前端；留空表示沿用已保存的 Key。
              </div>
            </el-form-item>
          </template>

          <el-form-item label="temperature">
            <el-slider
              v-model="form.temperature"
              :min="0"
              :max="2"
              :step="0.1"
              :disabled="!isRemote"
              show-input
              class="slider"
            />
          </el-form-item>

          <el-form-item label="max_tokens">
            <el-input-number
              v-model="form.max_tokens"
              :min="64"
              :max="8192"
              :step="128"
              :disabled="!isRemote"
            />
          </el-form-item>

          <el-form-item label="失败回退本地">
            <el-switch v-model="form.fallback_to_local" :disabled="!isRemote" />
            <span class="muted ml">开启后：远程模型不可用时自动改用本地模型继续作答</span>
          </el-form-item>

          <el-form-item>
            <div class="row-gap">
              <el-button
                type="primary"
                :loading="saving"
                :disabled="testing"
                @click="handleSave"
              >
                保存配置
              </el-button>
              <el-button
                :loading="testing"
                :disabled="!isRemote || saving"
                @click="handleTest"
              >
                测试连接
              </el-button>
            </div>
          </el-form-item>
        </el-form>

        <el-alert
          v-if="testResult"
          class="mt"
          :type="testResult.status === 'ok' ? 'success' : 'error'"
          :closable="false"
          show-icon
          :title="testResult.status === 'ok' ? '连接成功' : '连接失败'"
          :description="testResult.message"
        />
      </div>

      <!-- 说明 -->
      <div class="panel">
        <div class="panel-title"><span>行为说明</span></div>
        <ul class="note-list">
          <li>不配置 API Key 时，系统始终使用本地模型（离线、CPU 推理、空闲自动卸载）。</li>
          <li>切换到远程模型后，后端会自动卸载本地模型权重，释放约 1.3 GB 内存。</li>
          <li>远程模型未产出任何内容时：默认直接报错；勾选「失败回退本地」则自动改用本地模型作答。</li>
          <li>API Key 仅存后端 SQLite，读取接口只返回末 4 位掩码，接口日志不记录明文。</li>
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

.ml {
  margin-left: 10px;
}

.spacer {
  flex: 1;
}

.cfg-form {
  max-width: 720px;
}

.cfg-form :deep(.el-form-item) {
  margin-bottom: 16px;
}

.slider {
  width: 100%;
}

.hint {
  font-size: 12px;
  color: var(--app-text-sub);
  line-height: 1.6;
  margin-top: 2px;
}

.note-list {
  margin: 0;
  padding-left: 20px;
  font-size: 12px;
  color: #5a6473;
  line-height: 1.9;
}
</style>
