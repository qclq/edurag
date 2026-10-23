<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Download, Filter, Promotion, Refresh, Search, Upload } from '@element-plus/icons-vue'
import StatusPill from '../components/StatusPill.vue'
import { authenticatedRawRequest, authenticatedRequest } from '../stores/auth'

const route = useRoute()
const router = useRouter()
const query = ref('')
const chunks = ref([])
const documents = ref([])
const documentData = ref(null)
const timeline = ref([])
const selectedId = ref('')
const loading = ref(false)
const testing = ref(false)
const deleting = ref(false)
const testResult = ref(null)
const fileInput = ref(null)
let chunkTimer = null

const documentExtension = computed(() => {
  const name = documentData.value?.fileName || ''
  const extension = name.includes('.') ? name.split('.').pop().trim().toUpperCase() : ''
  if (extension) return extension
  const mime = documentData.value?.mimeType || ''
  if (mime.includes('pdf')) return 'PDF'
  if (mime.includes('spreadsheet') || mime.includes('excel')) return 'XLSX'
  if (mime.includes('presentation') || mime.includes('powerpoint')) return 'PPTX'
  if (mime.includes('word')) return 'DOCX'
  return 'FILE'
})
const documentFormatClass = computed(() => {
  const extension = documentExtension.value.toLowerCase()
  if (extension === 'pdf') return 'format-pdf'
  if (['xls', 'xlsx', 'xlsm', 'xlsb', 'csv', 'ods'].includes(extension)) return 'format-sheet'
  if (['ppt', 'pptx', 'pptm', 'odp'].includes(extension)) return 'format-slide'
  if (['doc', 'docx', 'docm', 'rtf', 'odt'].includes(extension)) return 'format-word'
  return 'format-text'
})

const loadDocuments = async () => {
  try {
    const envelope = await authenticatedRequest('/admin/documents?pageSize=100', { includeEnvelope: true })
    documents.value = envelope.data || []
    const requested = typeof route.query.documentId === 'string' ? route.query.documentId : ''
    selectedId.value = requested || selectedId.value || documents.value[0]?.id || ''
    if (selectedId.value) await loadDocument(selectedId.value)
  } catch (error) { ElMessage.error(error.message || '文档列表加载失败') }
}

const loadChunks = async () => {
  if (!selectedId.value) return
  const params = new URLSearchParams({ pageSize: '100' })
  if (query.value.trim()) params.set('q', query.value.trim())
  const envelope = await authenticatedRequest(`/admin/documents/${selectedId.value}/chunks?${params}`, { includeEnvelope: true })
  chunks.value = envelope.data || []
}

const loadDocument = async (id) => {
  if (!id) return
  loading.value = true
  try {
    const [detail, events] = await Promise.all([
      authenticatedRequest(`/admin/documents/${id}`),
      authenticatedRequest(`/admin/documents/${id}/timeline`),
    ])
    documentData.value = detail
    timeline.value = events || []
    selectedId.value = id
    await loadChunks()
    if (route.query.documentId !== id) window.history.replaceState({}, '', router.resolve({ path: '/admin/document', query: { documentId: id } }).href)
  } catch (error) { ElMessage.error(error.message || '文档详情加载失败') }
  finally { loading.value = false }
}

const reprocess = async () => {
  if (!documentData.value) return
  try {
    const { value } = await ElMessageBox.prompt('请填写重新处理原因', '重新处理文档', { inputValue: '调整解析结果并重新生成索引', inputPattern: /.{2,}/, inputErrorMessage: '原因至少 2 个字符' })
    await authenticatedRequest(`/admin/documents/${documentData.value.id}/reprocess`, { method: 'POST', body: { reason: value, ocrEnabled: true, chunkSize: 800, chunkOverlap: 100 } })
    ElMessage.success('重新处理任务已提交')
    await loadDocument(documentData.value.id)
  } catch (error) { if (error !== 'cancel' && error !== 'close') ElMessage.error(error.message || '任务提交失败') }
}

const chooseVersionFile = () => fileInput.value?.click()
const uploadVersion = async (event) => {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file || !documentData.value) return
  loading.value = true
  try {
    const form = new FormData(); form.append('file', file); form.append('clientFileId', crypto.randomUUID?.() || `web-${Date.now()}`)
    const upload = await authenticatedRequest('/admin/uploads', { method: 'POST', body: form })
    await authenticatedRequest(`/admin/documents/${documentData.value.id}/versions`, { method: 'POST', body: { uploadId: upload.uploadId, switchPolicy: 'after_ready' } })
    ElMessage.success('新版本已上传，处理完成后自动切换')
    await loadDocument(documentData.value.id)
  } catch (error) { ElMessage.error(error.message || '新版本上传失败') }
  finally { loading.value = false }
}

const download = async () => {
  if (!documentData.value) return
  try {
    const response = await authenticatedRawRequest(`/documents/${documentData.value.id}/download`)
    const url = URL.createObjectURL(await response.blob()); const link = document.createElement('a'); link.href = url; link.download = documentData.value.displayName; link.click(); URL.revokeObjectURL(url)
  } catch (error) { ElMessage.error(error.message || '当前账号无权下载该文档') }
}

const deleteDocument = async () => {
  const current = documentData.value
  if (!current || deleting.value) return
  try {
    const { value } = await ElMessageBox.prompt(
      `删除后“${current.displayName}”会立即退出检索范围，历史审计记录仍会保留。请输入完整文档名称确认。`,
      '删除知识库文件',
      {
        confirmButtonText: '确认删除',
        cancelButtonText: '取消',
        confirmButtonClass: 'el-button--danger',
        inputPlaceholder: current.displayName,
        inputValidator: value => value === current.displayName || '输入的文档名称不一致',
      },
    )
    deleting.value = true
    await authenticatedRequest(`/admin/documents/${current.id}`, {
      method: 'DELETE',
      body: { confirmName: value },
    })
    documents.value = documents.value.filter(item => item.id !== current.id)
    documentData.value = null
    chunks.value = []
    timeline.value = []
    testResult.value = null
    selectedId.value = documents.value[0]?.id || ''
    ElMessage.success('文档已删除，后续问答不会再检索该文件')
    if (selectedId.value) await loadDocument(selectedId.value)
    else await router.replace('/admin/document')
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(error.message || '文档删除失败')
  } finally {
    deleting.value = false
  }
}

const editChunk = async (chunk) => {
  try {
    const { value } = await ElMessageBox.prompt(
      '编辑切片文本，保存后系统会创建重向量任务。',
      `编辑切片 #${chunk.chunkIndex + 1}`,
      {
        customClass: 'chunk-editor-dialog',
        inputType: 'textarea',
        inputValue: chunk.content,
        inputValidator: value => value.trim() ? true : '切片内容不能为空',
      },
    )
    await authenticatedRequest(`/admin/chunks/${chunk.id}`, { method: 'PATCH', body: { content: value, reason: '管理员校正文档切片', version: chunk.version } })
    ElMessage.success('切片已保存并提交重向量任务')
    await loadChunks()
  } catch (error) { if (error !== 'cancel' && error !== 'close') ElMessage.error(error.message || '切片保存失败') }
}

const runTest = async () => {
  if (!query.value.trim() || !documentData.value) return
  testing.value = true
  try { testResult.value = await authenticatedRequest(`/admin/documents/${documentData.value.id}/retrieval-tests`, { method: 'POST', body: { query: query.value, topK: 5, minScore: 0 } }) }
  catch (error) { ElMessage.error(error.message || '检索测试失败') }
  finally { testing.value = false }
}

watch(query, () => { window.clearTimeout(chunkTimer); chunkTimer = window.setTimeout(loadChunks, 300) })
onMounted(loadDocuments)
</script>

<template>
  <div class="page-content document-page">
    <header class="doc-head panel"><span :class="['document-format', documentFormatClass]">{{ documentExtension }}</span><div><div><h1>{{ documentData?.displayName || '文档中心' }}</h1><span class="version">{{ documentData?.versions?.[0] ? `V${documentData.versions[0].versionNo}` : '—' }}</span></div><p>{{ documentData?.knowledgeBase?.name || '请选择文档查看详情' }} · {{ documentData?.documentNo || '无发文编号' }}</p><el-select v-model="selectedId" filterable placeholder="选择文档" style="width:min(420px,100%);margin-top:9px" @change="loadDocument"><el-option v-for="item in documents" :key="item.id" :label="item.displayName" :value="item.id" /></el-select></div><div class="doc-actions"><input ref="fileInput" type="file" hidden @change="uploadVersion"/><el-button :icon="Upload" title="上传新版本" aria-label="上传新版本" :disabled="!documentData" @click="chooseVersionFile">上传新版本</el-button><el-button :icon="Refresh" title="重新处理" aria-label="重新处理" :disabled="!documentData" @click="reprocess">重新处理</el-button><el-button :icon="Download" title="下载原件" aria-label="下载原件" :disabled="!documentData" @click="download">下载原件</el-button><el-button class="danger-soft" :icon="Delete" title="删除文件" aria-label="删除文件" :loading="deleting" :disabled="!documentData" @click="deleteDocument">删除文件</el-button></div></header>
    <div class="doc-grid">
      <aside class="doc-overview">
        <div class="doc-metrics panel"><h3>解析元数据 <StatusPill v-if="documentData" :tone="documentData.status==='ready'?'success':'warning'" :text="documentData.statusLabel" /></h3><div class="meta-cards"><div><small>业务分类</small><b>{{documentData?.category || '—'}}</b></div><div><small>切片数</small><b>{{documentData?.chunkCount ?? '—'}}</b></div><div><small>版本数</small><b>{{documentData?.versions?.length ?? '—'}}</b></div><div><small>生效日期</small><b>{{documentData?.effectiveAt || '—'}}</b></div></div></div>
        <div class="timeline panel panel-pad"><div class="panel-title"><div><h2>入库全生命周期</h2><p>{{timeline.length}} 条任务事件</p></div></div><article v-for="item in timeline" :key="`${item.taskId}-${item.sequence}`" class="timeline-item"><i/><div><b>{{item.stage || item.type}}</b><p>{{item.data?.message || item.data?.progressText || item.type}}</p><small>{{new Date(item.occurredAt).toLocaleString('zh-CN')}}</small></div></article><el-empty v-if="!timeline.length" description="暂无任务事件" /></div>
      </aside>

      <section class="chunks-panel"><div class="chunks-head"><div><h2>文本切片清单</h2><p>点击切片可校对文本并触发重向量</p></div><b>{{ chunks.length }} 个块</b></div><div class="chunk-filters"><el-input v-model="query" :prefix-icon="Search" clearable placeholder="搜索切片内容或输入检索测试问题..."/><el-button :icon="Filter" :disabled="!documentData" @click="loadChunks">筛选</el-button></div><article v-for="chunk in chunks" :key="chunk.id" class="chunk-row" @click="editChunk(chunk)"><header><b>#{{chunk.chunkIndex+1}} {{chunk.title || chunk.section || '正文切片'}}</b><StatusPill :tone="chunk.vectorStatus==='ready'?'success':'warning'" :text="chunk.vectorStatus" /></header><p>{{chunk.content}}</p><footer><span>{{chunk.tokenCount || 0}} tokens</span><span>{{chunk.pageStart ? `第 ${chunk.pageStart}${chunk.pageEnd && chunk.pageEnd!==chunk.pageStart ? `–${chunk.pageEnd}`:''} 页` : '页码未标注'}}</span></footer></article><el-empty v-if="!chunks.length" description="暂无切片数据" /></section>

      <aside class="sandbox panel"><header><span><el-icon><Promotion /></el-icon></span><div><h3>即席检索调试窗口</h3><p>RAG Ingestion Sandbox</p></div></header><div class="sandbox-form"><el-input v-model="query" type="textarea" :rows="3" placeholder="请输入测试问题"/><el-button class="primary-btn" :icon="Promotion" :loading="testing" :disabled="!query.trim() || !documentData" @click="runTest">执行检索</el-button><template v-if="testResult"><p class="test-answer">{{testResult.testAnswer || testResult.timing?.answerError?.message || '未生成测试回答'}}</p><article v-for="item in testResult.chunks" :key="item.id" class="test-chunk"><b>{{item.documentName || item.section || '召回切片'}}</b><p>{{item.content}}</p></article></template><el-empty v-else description="执行测试后显示召回结果" /></div></aside>
    </div>
  </div>
</template>

<style scoped>
.document-format{min-width:54px;height:54px;padding:0 7px;display:grid;place-items:center;border-radius:12px;background:#f1f5f9;color:#475569;font-size:10px;font-weight:900;letter-spacing:.02em}.document-format.format-pdf{background:#fef2f2;color:#dc2626}.document-format.format-word{background:#eff6ff;color:#2563eb}.document-format.format-sheet{background:#ecfdf5;color:#059669}.document-format.format-slide{background:#fff7ed;color:#ea580c}.document-format.format-text{background:#f8fafc;color:#475569}
.doc-head{display:flex;align-items:center;gap:13px;padding:16px 18px;margin-bottom:15px}.pdf-logo{width:44px;height:44px;display:grid;place-items:center;border-radius:10px;background:#fef2f2;color:#dc2626;font-size:10px;font-weight:800}.doc-head>div:nth-child(2){flex:1;min-width:0}.doc-head>div:nth-child(2)>div{display:flex;align-items:center;gap:8px}.doc-head h1{margin:0;font-size:16px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.version{padding:4px 6px;border-radius:6px;background:#f1f5f9;color:#64748b;font-size:8px}.doc-head p{margin:5px 0 0;color:#64748b;font-size:9px}.doc-actions{display:flex;gap:6px;flex:none}.doc-grid{display:grid;grid-template-columns:240px minmax(360px,1fr) 330px;gap:14px;align-items:start}.doc-overview{display:grid;gap:13px}.doc-metrics{padding:15px}.doc-metrics h3{display:flex;justify-content:space-between;font-size:11px}.doc-metrics h3 span{color:#94a3b8;font-size:8px}.meta-cards{display:grid;grid-template-columns:1fr 1fr;gap:7px}.meta-cards>div{padding:9px;border-radius:8px;background:#f8fafc}.meta-cards small,.meta-cards b{display:block}.meta-cards small{color:#94a3b8;font-size:7px}.meta-cards b{margin-top:4px;font-size:11px}.quality{display:flex;align-items:center;gap:8px;padding:9px;margin-top:9px;border-radius:8px;background:#ecfdf5}.quality>span{color:#059669}.quality p{flex:1;margin:0}.quality b,.quality small{display:block}.quality b{font-size:8px}.quality small{margin-top:3px;color:#64748b;font-size:7px}.quality strong{color:#059669;font-size:12px}.timeline-item{position:relative;display:flex;gap:8px;padding-bottom:14px}.timeline-item:not(:last-child):after{content:"";position:absolute;left:11px;top:22px;bottom:0;border-left:1px dashed #86efac}.timeline-item>span{z-index:1;width:23px;height:23px;display:grid;place-items:center;border-radius:50%;background:#ecfdf5;color:#059669}.timeline-item>div{flex:1}.timeline-item b{font-size:8px}.timeline-item time{float:right;color:#94a3b8;font-size:7px}.timeline-item small{display:block;margin-top:3px;color:#64748b;font-size:7px;line-height:1.4}.chunks-panel{min-width:0}.chunks-head{display:flex;justify-content:space-between;align-items:end;padding:0 2px 9px}.chunks-head h2{margin:0;font-size:14px}.chunks-head p{margin:3px 0 0;color:#94a3b8;font-size:8px}.chunks-head>b{font-size:9px}.chunk-filters{display:grid;grid-template-columns:1fr 170px auto;gap:7px;margin-bottom:9px}.chunk-card{padding:14px;margin-top:9px;border-radius:13px;background:#fff;box-shadow:var(--shadow)}.chunk-card.active{outline:2px solid rgba(0,81,213,.18);box-shadow:0 7px 22px rgba(0,81,213,.1)}.chunk-card header{display:flex;align-items:center;gap:8px}.chunk-card header>span{padding:4px 6px;border-radius:5px;background:#f1f5f9;color:#64748b;font-size:7px}.chunk-card h3{flex:1;margin:0;font-size:10px}.chunk-card header small{color:#94a3b8;font-size:7px}.chunk-card>p{margin:10px 0;color:#475569;font-size:9px;line-height:1.75}.chunk-card footer{display:flex;align-items:center;gap:5px;padding-top:8px;border-top:1px solid #eff4ff}.chunk-card footer>span{margin-right:auto;color:#64748b;font-size:7px}.sandbox{position:sticky;top:88px;overflow:hidden}.sandbox>header{display:flex;align-items:center;gap:8px;padding:14px;border-bottom:1px solid #eff4ff}.sandbox>header>span{width:32px;height:32px;display:grid;place-items:center;border-radius:9px;background:#06080c;color:#fff}.sandbox>header>div{flex:1}.sandbox h3{margin:0;font-size:11px}.sandbox header p{margin:2px 0 0;color:#94a3b8;font-size:7px}.sandbox-form{padding:13px}.sandbox-form>small{float:right;color:#64748b;font-size:7px}.sandbox-form>.el-textarea{margin:9px 0}.sandbox-form>.primary-btn{width:100%}.cache{display:flex;justify-content:space-between;align-items:center;margin:9px 0}.cache>span{color:#64748b;font-size:7px}.sandbox-form>h4{margin:13px 0 7px;font-size:8px}.sandbox-form>h4 b{float:right;color:#059669}.rank{display:flex;gap:8px;padding:9px;margin-top:7px;border-radius:9px;background:#f8fafc}.rank.active{background:#eff4ff}.rank>span{width:21px;height:21px;display:grid;place-items:center;border-radius:6px;background:#fff;color:#0051d5;font-size:8px;font-weight:800}.rank>div{flex:1}.rank header{display:flex;justify-content:space-between}.rank b{font-size:8px}.rank strong{color:#059669;font-size:9px}.rank p{margin:6px 0;color:#475569;font-size:7px;line-height:1.5}.rank small{color:#94a3b8;font-size:7px}.rank a{float:right;color:#0051d5;font-size:7px}.rag-preview{padding:10px;margin-top:10px;border-radius:9px;background:#f8fafc}.rag-preview h4{display:flex;align-items:center;gap:5px;margin:0 0 7px;color:#0051d5;font-size:8px}.rag-preview p,.rag-preview b,.rag-preview a{font-size:7px;line-height:1.55}.rag-preview b{display:block;color:#334155}.rag-preview a{color:#0051d5}.sandbox-foot{text-align:center;color:#94a3b8;font-size:7px}
.timeline-item{display:flex;gap:9px;padding:9px 0;border-bottom:1px solid #eff4ff}.timeline-item>i{width:7px;height:7px;margin-top:5px;border-radius:50%;background:#0051d5}.timeline-item>div{min-width:0}.timeline-item b,.timeline-item p,.timeline-item small{display:block;margin:0}.timeline-item b{font-size:9px}.timeline-item p{margin-top:3px;color:#64748b;font-size:8px}.timeline-item small{margin-top:3px;color:#94a3b8;font-size:7px}.chunk-row{padding:14px 15px;border-bottom:1px solid #eff4ff;background:#fff;cursor:pointer}.chunk-row:hover{background:#f8fbff}.chunk-row header,.chunk-row footer{display:flex;align-items:center;justify-content:space-between;gap:9px}.chunk-row header b{font-size:10px}.chunk-row>p{margin:9px 0;color:#475569;font-size:9px;line-height:1.65;display:-webkit-box;-webkit-line-clamp:4;-webkit-box-orient:vertical;overflow:hidden}.chunk-row footer{color:#94a3b8;font-size:8px}.test-answer,.test-chunk{padding:10px;border-radius:8px;background:#f8fafc;color:#475569;font-size:9px;line-height:1.55}.test-chunk{margin-top:8px;background:#fff}.test-chunk p{margin:5px 0 0}
:global(.chunk-editor-dialog){width:min(900px,calc(100vw - 32px));max-width:none}:global(.chunk-editor-dialog .el-message-box__content){padding-bottom:8px}:global(.chunk-editor-dialog .el-message-box__input){padding-top:14px}:global(.chunk-editor-dialog .el-textarea__inner){height:clamp(320px,52vh,560px);padding:14px;line-height:1.7;resize:vertical}
@media(max-width:1350px){.doc-grid{grid-template-columns:220px 1fr}.sandbox{position:static;grid-column:1/-1}.doc-actions :deep(.el-button>span){display:none}.doc-actions .el-button{margin-left:0}}
@media(max-width:800px){.doc-grid{grid-template-columns:1fr}.doc-head{align-items:flex-start;flex-wrap:wrap}.doc-actions{width:100%;overflow-x:auto;padding-bottom:2px}.doc-actions .el-button{flex:1}.timeline{display:none}.chunk-filters{grid-template-columns:1fr}.chunk-filters .el-select{width:100%}.chunk-card footer .el-button{display:none}:global(.chunk-editor-dialog .el-textarea__inner){height:45vh;min-height:240px}}
</style>
