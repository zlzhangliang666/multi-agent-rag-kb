<script setup>
/**
 * 知识库管理页
 * - el-upload 自定义上传（走 axios，可显示进度与后端错误详情）
 * - 文档列表表格：文档名 / 块数量 / doc_id
 * - 删除文档（同步删除向量库与 BM25 索引）
 */
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { deleteDoc, listDocs, uploadDoc } from '../api'

defineOptions({ name: 'KnowledgeBaseView' })

const ACCEPT_EXT = ['.pdf', '.doc', '.docx', '.txt', '.md', '.markdown']
const MAX_SIZE_MB = 50

const docs = ref([])
const loading = ref(false)
const loadError = ref('')
const uploadingCount = ref(0)
const uploadResults = ref([])
const keyword = ref('')

const totalChunks = computed(() => docs.value.reduce((sum, d) => sum + (Number(d.chunk_count) || 0), 0))

const filteredDocs = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  if (!kw) return docs.value
  return docs.value.filter(
    (d) => String(d.doc_name || '').toLowerCase().includes(kw) || String(d.doc_id || '').toLowerCase().includes(kw),
  )
})

const uploading = computed(() => uploadingCount.value > 0)

async function loadDocs(silent = false) {
  loading.value = true
  loadError.value = ''
  try {
    const data = await listDocs(true)
    docs.value = (data && data.docs) || []
    if (!silent) ElMessage.success(`已刷新，共 ${docs.value.length} 个文档`)
  } catch (e) {
    docs.value = []
    loadError.value = (e && e.message) || '加载文档列表失败'
    if (!silent) ElMessage.error(loadError.value)
  } finally {
    loading.value = false
  }
}

function beforeUpload(file) {
  const name = String(file.name || '').toLowerCase()
  const ext = name.slice(name.lastIndexOf('.'))
  if (!ACCEPT_EXT.includes(ext)) {
    ElMessage.error(`不支持的文件类型「${ext || '无扩展名'}」，仅支持 ${ACCEPT_EXT.join(' / ')}`)
    return false
  }
  const sizeMb = file.size / 1024 / 1024
  if (sizeMb > MAX_SIZE_MB) {
    ElMessage.error(`文件过大（${sizeMb.toFixed(1)} MB），请控制在 ${MAX_SIZE_MB} MB 以内`)
    return false
  }
  return true
}

/**
 * el-upload 自定义上传：接管请求，交给 axios 处理（可携带统一错误提示）
 */
async function handleUpload(options) {
  const file = options.file
  const startedAt = Date.now()
  uploadingCount.value += 1
  try {
    const res = await uploadDoc(file, (percent) => {
      if (options.onProgress) options.onProgress({ percent })
    })
    const record = {
      doc_name: (res && res.doc_name) || file.name,
      chunk_count: (res && res.chunk_count) || 0,
      elapsed: (res && res.elapsed) || Number(((Date.now() - startedAt) / 1000).toFixed(2)),
      time: new Date().toLocaleTimeString('zh-CN', { hour12: false }),
      status: 'success',
      message: '',
    }
    uploadResults.value.unshift(record)
    if (options.onSuccess) options.onSuccess(res)
    ElMessage.success(`《${record.doc_name}》入库成功：${record.chunk_count} 个块，耗时 ${record.elapsed} s`)
    await loadDocs(true)
    return res
  } catch (e) {
    uploadResults.value.unshift({
      doc_name: file.name,
      chunk_count: 0,
      elapsed: Number(((Date.now() - startedAt) / 1000).toFixed(2)),
      time: new Date().toLocaleTimeString('zh-CN', { hour12: false }),
      status: 'error',
      message: (e && e.message) || '上传失败',
    })
    if (options.onError) options.onError(e)
    // 注意：此处不能再 reject，否则 el-upload 内部会二次触发 onError
    return null
  } finally {
    uploadingCount.value -= 1
  }
}

async function handleDelete(row) {
  try {
    const res = await deleteDoc(row.doc_id)
    const count = res && typeof res.chunk_count === 'number' ? res.chunk_count : row.chunk_count
    ElMessage.success(`已删除《${row.doc_name}》，清理 ${count} 个块`)
    docs.value = docs.value.filter((d) => d.doc_id !== row.doc_id)
  } catch (e) {
    ElMessage.error((e && e.message) || '删除失败')
  }
}

async function copyDocId(docId) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(docId)
    } else {
      const ta = document.createElement('textarea')
      ta.value = docId
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      document.body.removeChild(ta)
    }
    ElMessage.success('doc_id 已复制')
  } catch (e) {
    ElMessage.warning('复制失败')
  }
}

onMounted(() => {
  loadDocs(true)
})
</script>

<template>
  <div class="page">
    <div class="page-scroll">
      <!-- 概览 -->
      <div class="stat-grid mb">
        <div class="stat-card">
          <div class="label">文档总数</div>
          <div class="value">{{ docs.length }}</div>
        </div>
        <div class="stat-card">
          <div class="label">知识块总数</div>
          <div class="value">{{ totalChunks }}</div>
        </div>
        <div class="stat-card">
          <div class="label">上传中</div>
          <div class="value">{{ uploadingCount }}</div>
        </div>
        <div class="stat-card">
          <div class="label">支持格式</div>
          <div class="value small">PDF / DOCX / TXT / MD</div>
        </div>
      </div>

      <!-- 上传 -->
      <div class="panel mb">
        <div class="panel-title">
          <span>上传文档</span>
          <span class="sub">上传后自动执行：解析 → 自适应分块 → ONNX 向量化 → 写入 Chroma 与 BM25 索引</span>
        </div>
        <el-upload
          class="kb-upload"
          drag
          multiple
          :auto-upload="true"
          :show-file-list="true"
          :limit="5"
          :accept="ACCEPT_EXT.join(',')"
          :before-upload="beforeUpload"
          :http-request="handleUpload"
        >
          <div class="upload-inner">
            <div class="upload-title">将文件拖到此处，或<em>点击选择文件</em></div>
            <div class="muted">
              支持 {{ ACCEPT_EXT.join(' / ') }}，单文件不超过 {{ MAX_SIZE_MB }} MB；大文档解析耗时较长，请耐心等待
            </div>
          </div>
        </el-upload>

        <div v-if="uploading" class="uploading-tip">
          <span class="spin-dot"></span> 正在解析与向量化文档，此过程为 CPU 密集操作，请勿关闭页面...
        </div>

        <div v-if="uploadResults.length" class="result-list">
          <div class="result-title">本次上传结果</div>
          <div v-for="(r, idx) in uploadResults" :key="`res-${idx}`" class="result-item" :class="r.status">
            <el-tag size="small" :type="r.status === 'success' ? 'success' : 'danger'" effect="plain">
              {{ r.status === 'success' ? '成功' : '失败' }}
            </el-tag>
            <span class="result-name">{{ r.doc_name }}</span>
            <span class="muted">{{ r.chunk_count }} 块</span>
            <span class="muted">耗时 {{ r.elapsed }} s</span>
            <span class="muted">{{ r.time }}</span>
            <span v-if="r.message" class="result-msg">{{ r.message }}</span>
          </div>
        </div>
      </div>

      <!-- 文档列表 -->
      <div class="panel">
        <div class="panel-title">
          <span>文档列表</span>
          <div class="row-gap">
            <el-input
              v-model="keyword"
              size="small"
              placeholder="搜索文档名 / doc_id"
              clearable
              style="width: 200px"
            />
            <el-button size="small" :loading="loading" @click="loadDocs(false)">刷新</el-button>
          </div>
        </div>

        <el-alert
          v-if="loadError"
          class="mb"
          type="warning"
          :closable="false"
          show-icon
          :title="`文档列表加载失败：${loadError}`"
          description="常见原因：Chroma 向量库尚未初始化，或后端 RAG 组件未就绪。可先执行 scripts/init_knowledge_base.py 后刷新。"
        />

        <el-table :data="filteredDocs" v-loading="loading" stripe border size="default" empty-text="知识库暂无文档">
          <el-table-column type="index" label="#" width="56" align="center" />
          <el-table-column prop="doc_name" label="文档名称" min-width="280" show-overflow-tooltip>
            <template #default="{ row }">
              <span class="doc-name">{{ row.doc_name || '（未命名）' }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="chunk_count" label="块数量" width="110" align="center" sortable>
            <template #default="{ row }">
              <el-tag size="small" type="info" effect="plain">{{ row.chunk_count }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="doc_id" label="doc_id" min-width="220">
            <template #default="{ row }">
              <span class="mono">{{ row.doc_id }}</span>
              <el-button link size="small" type="primary" @click="copyDocId(row.doc_id)">复制</el-button>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="110" align="center" fixed="right">
            <template #default="{ row }">
              <el-popconfirm
                :title="`确定删除《${row.doc_name || row.doc_id}》吗？`"
                width="260"
                confirm-button-text="删除"
                cancel-button-text="取消"
                confirm-button-type="danger"
                @confirm="handleDelete(row)"
              >
                <template #reference>
                  <el-button size="small" type="danger" plain>删除</el-button>
                </template>
              </el-popconfirm>
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

.kb-upload :deep(.el-upload-dragger) {
  padding: 22px;
  border-radius: 8px;
}

.upload-inner {
  text-align: center;
}

.upload-title {
  font-size: 14px;
  color: #46505f;
  margin-bottom: 6px;
}

.upload-title em {
  color: var(--app-primary);
  font-style: normal;
  font-weight: 600;
}

.uploading-tip {
  margin-top: 10px;
  font-size: 12px;
  color: var(--app-warning);
  display: flex;
  align-items: center;
  gap: 8px;
}

.result-list {
  margin-top: 12px;
  border-top: 1px dashed var(--app-border);
  padding-top: 10px;
}

.result-title {
  font-size: 12px;
  font-weight: 600;
  color: #46505f;
  margin-bottom: 6px;
}

.result-item {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  padding: 6px 8px;
  border-radius: 6px;
  font-size: 12px;
  background: #fafbfd;
  border: 1px solid var(--app-border);
  margin-bottom: 6px;
}

.result-item.error {
  background: #fef5f5;
  border-color: #fbc4c4;
}

.result-name {
  font-weight: 600;
}

.result-msg {
  color: #c45656;
  word-break: break-all;
}

.doc-name {
  font-weight: 500;
}

.mono {
  font-family: Consolas, monospace;
  font-size: 12px;
  color: #5a6473;
  margin-right: 6px;
}
</style>
