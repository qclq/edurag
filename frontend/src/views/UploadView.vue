<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowLeft, Check, Close, Document, Files, InfoFilled, Refresh, Setting, UploadFilled } from '@element-plus/icons-vue'
import StatusPill from '../components/StatusPill.vue'
import QuickKnowledgeBaseDialog from '../components/QuickKnowledgeBaseDialog.vue'
import { authenticatedRequest, useAuth } from '../stores/auth'

const { state } = useAuth()
const router = useRouter()
const route = useRoute()
const files = ref([])
const knowledgeBases = ref([])
const departments = ref([])
const tasks = ref([])
const mode = ref('上传文档')
const submitting = ref(false)
const pageLoading = ref(false)
const loadError = ref('')
const uploadRef = ref(null)
const createKbOpen = ref(false)
const config = ref({ kb:'', category:'规章制度', department:'', number:'', effective:'', ocr:true, vectorize:true, chunk:512, overlap:64 })
const serverUploadConfig = ref(null)
const uploadLimits = computed(() => serverUploadConfig.value || state.bootstrap?.uploadLimits || {})
const maxFiles = computed(() => uploadLimits.value.maxFiles || 20)
const maxFileSizeBytes = computed(() => uploadLimits.value.maxFileSizeBytes || 50 * 1024 * 1024)
const allowedExtensions = computed(() => uploadLimits.value.allowedExtensions || ['pdf', 'docx', 'txt', 'md'])
const accept = computed(() => (uploadLimits.value.allowedExtensions || ['pdf', 'docx', 'txt', 'md']).map(item => `.${item}`).join(','))
const uploadLimitText = computed(() => {
  const extensions = (uploadLimits.value.allowedExtensions || ['pdf', 'docx', 'txt', 'md']).map(item => item.toUpperCase()).join(', ')
  return `支持 ${extensions}（单个文件最大 ${Math.round(maxFileSizeBytes.value / 1024 / 1024)}MB，单次最多 ${maxFiles.value} 个）`
})
const totalSize = computed(() => {
  const bytes = files.value.reduce((sum, file) => sum + file.sizeBytes, 0)
  if (bytes === 0) return '0 MB'
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
})
const loadPage = async () => {
  pageLoading.value = true
  loadError.value = ''
  const results = await Promise.allSettled([
    authenticatedRequest('/admin/document-upload-config'),
    authenticatedRequest('/admin/knowledge-bases?pageSize=100', { includeEnvelope: true }),
    authenticatedRequest('/admin/departments'),
    authenticatedRequest('/admin/tasks?pageSize=20', { includeEnvelope: true }),
  ])
  if (results[0].status === 'fulfilled') serverUploadConfig.value = results[0].value
  if (results[1].status === 'fulfilled') {
    knowledgeBases.value = results[1].value.data || []
    if (!knowledgeBases.value.some(item => item.id === config.value.kb)) config.value.kb = knowledgeBases.value[0]?.id || ''
  } else {
    loadError.value = results[1].reason?.message || '可管理知识库加载失败'
  }
  if (results[2].status === 'fulfilled') departments.value = results[2].value
  if (results[3].status === 'fulfilled') tasks.value = results[3].value.data || []
  pageLoading.value = false
}

const submit = async (saveAsDraft = false) => {
  if (submitting.value) return
  if (!files.value.length) return ElMessage.warning('请先选择要上传的文件')
  if (!config.value.kb) {
    createKbOpen.value = true
    return ElMessage.warning('请先创建或选择目标知识库')
  }
  if (config.value.overlap >= config.value.chunk) return ElMessage.warning('重叠窗口必须小于切片最大长度')
  submitting.value = true
  const uploaded = []
  let activeItem = null
  try {
    for (const item of files.value) {
      activeItem = item
      if (!item.uploadId) {
        item.state = 'uploading'
        item.detail = '正在上传文件…'
        const body = new FormData()
        body.append('file', item.raw, item.name)
        body.append('clientFileId', item.clientFileId)
        const result = await authenticatedRequest('/admin/uploads', { method: 'POST', body })
        item.uploadId = result.uploadId
        item.state = 'uploaded'
        item.detail = result.duplicate ? '文件已暂存（检测到相同内容）' : '文件已暂存，等待提交入库'
      }
      uploaded.push({
        uploadId: item.uploadId,
        metadata: {
          knowledgeBaseId: config.value.kb,
          displayName: item.name.replace(/\.[^.]+$/, ''),
          category: config.value.category || null,
          publishDepartmentId: config.value.department || null,
          documentNo: config.value.number || null,
          effectiveAt: config.value.effective || null,
          tags: [],
        },
      })
    }
    activeItem = null
    const result = await authenticatedRequest('/admin/document-ingestions', {
      method: 'POST',
      body: { items: uploaded, processingConfig: { ocrEnabled: config.value.ocr, chunkSize: config.value.chunk, chunkOverlap: config.value.overlap }, saveAsDraft },
    })
    const submittedCount = result.items?.length || uploaded.length
    files.value.forEach(item => { item.state = 'submitted'; item.detail = saveAsDraft ? '草稿已保存' : '已提交处理任务' })
    ElMessage.success(saveAsDraft ? `已保存 ${submittedCount} 个文档草稿` : `已提交 ${submittedCount} 个文档处理任务`)
    await loadTasks({ silent: true })
    uploadRef.value?.clearFiles()
    files.value = []
    if (saveAsDraft) await router.push('/admin/document')
    else mode.value = '任务中心'
  } catch (error) {
    if (activeItem) {
      activeItem.state = 'failed'
      activeItem.detail = error.message || '文件上传失败，可重试'
    } else {
      files.value.filter(item => item.uploadId).forEach(item => {
        item.state = 'failed'
        item.detail = '文件已暂存，但入库提交失败；点击确认可直接重试'
      })
    }
    ElMessage.error(error.message || '上传入库失败')
  } finally { submitting.value = false }
}

const loadTasks = async ({ silent = false } = {}) => {
  try {
    const envelope = await authenticatedRequest('/admin/tasks?pageSize=20', { includeEnvelope: true })
    tasks.value = envelope.data || []
  } catch (error) { if (!silent) ElMessage.error(error.message || '任务列表加载失败') }
}

const retryTask = async (task) => {
  try { await authenticatedRequest(`/admin/tasks/${task.id}/retry`, { method: 'POST', body: { fromStage: task.stage || null } }); ElMessage.success('重试任务已提交'); await loadTasks() }
  catch (error) { ElMessage.error(error.message || '任务重试失败') }
}
const cancelTask = async (task) => {
  try { await authenticatedRequest(`/admin/tasks/${task.id}/cancel`, { method: 'POST' }); ElMessage.success('已请求取消任务'); await loadTasks() }
  catch (error) { ElMessage.error(error.message || '任务取消失败') }
}

const rejectUploadFile = (file) => uploadRef.value?.handleRemove(file)

const handleUpload = (file) => {
  const raw = file.raw
  const sizeBytes = raw?.size || file.size || 0
  const extension = (file.name.split('.').pop() || '').toLowerCase()
  const fingerprint = `${file.name}:${sizeBytes}:${raw?.lastModified || 0}`
  if (!raw) {
    ElMessage.error(`${file.name} 无法读取，请重新选择`)
    return rejectUploadFile(file)
  }
  if (!allowedExtensions.value.includes(extension)) {
    ElMessage.warning(`${file.name} 的格式不受支持`)
    return rejectUploadFile(file)
  }
  if (sizeBytes <= 0) {
    ElMessage.warning(`${file.name} 是空文件，无法入库`)
    return rejectUploadFile(file)
  }
  if (sizeBytes > maxFileSizeBytes.value) {
    ElMessage.warning(`${file.name} 超过单文件大小限制`)
    return rejectUploadFile(file)
  }
  if (files.value.some(item => item.fingerprint === fingerprint)) {
    ElMessage.warning(`${file.name} 已在待处理队列中`)
    return rejectUploadFile(file)
  }
  files.value.push({
    uid: file.uid,
    name: file.name,
    raw,
    uploadFile: file,
    fingerprint,
    clientFileId: crypto.randomUUID?.() || `web-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    sizeBytes,
    size: `${(sizeBytes / 1024 / 1024).toFixed(1)} MB`,
    state: 'pending',
    detail: '已选择，等待上传',
  })
}

const removeFile = async (index) => {
  const [item] = files.value.splice(index, 1)
  if (item?.uploadFile) uploadRef.value?.handleRemove(item.uploadFile)
  if (item?.uploadId && item.state !== 'submitted') {
    try { await authenticatedRequest(`/admin/uploads/${item.uploadId}`, { method: 'DELETE' }) } catch { /* The pending upload may already be unavailable. */ }
  }
}

const clearQueue = async () => {
  if (submitting.value) return
  const pending = files.value.filter(item => item.uploadId && item.state !== 'submitted')
  await Promise.allSettled(pending.map(item => authenticatedRequest(`/admin/uploads/${item.uploadId}`, { method: 'DELETE' })))
  files.value = []
  uploadRef.value?.clearFiles()
}

const handleKnowledgeBaseCreated = (created) => {
  if (!knowledgeBases.value.some(item => item.id === created.id)) knowledgeBases.value.unshift(created)
  config.value.kb = created.id
  loadError.value = ''
  ElMessage.success('知识库已创建并自动选中，现在可以提交文件入库')
}

let taskPollTimer = null
const updateTaskPolling = (value) => {
  window.clearInterval(taskPollTimer)
  if (value === '任务中心') taskPollTimer = window.setInterval(() => loadTasks({ silent: true }), 3000)
}
watch(mode, updateTaskPolling)

onMounted(async () => {
  if (route.query.taskId) mode.value = '任务中心'
  await loadPage()
  updateTaskPolling(mode.value)
})
onBeforeUnmount(() => window.clearInterval(taskPollTimer))
</script>

<template>
  <div class="page-content upload-page">
    <header class="upload-head"><div><p><el-icon><ArrowLeft /></el-icon> 文档中心　/　批量录入与任务</p><div><h1>{{mode}}</h1><span>RAG PIPELINE INGESTION</span></div></div><div><el-segmented v-model="mode" :options="['上传文档','任务中心']"/><el-button @click="router.push('/admin/document')">返回文档中心</el-button></div></header>

    <div v-if="mode==='上传文档'" class="upload-grid">
      <section class="upload-left">
        <el-upload ref="uploadRef" class="dropzone" drag action="#" multiple :limit="maxFiles" :auto-upload="false" :show-file-list="false" :on-change="handleUpload" :on-exceed="() => ElMessage.warning(`单次最多选择 ${maxFiles} 个文件`)" :accept="accept">
          <el-icon class="upload-icon"><UploadFilled /></el-icon><h3>点击上传或将文件拖拽至此处</h3><p>{{uploadLimitText}}</p><el-button class="primary-btn" :icon="Files">选取本地文件</el-button><div class="file-types"><i v-for="extension in uploadLimits.allowedExtensions || ['pdf','docx','txt','md']" :key="extension">{{extension.toUpperCase()}}</i></div>
        </el-upload>

        <section class="queue panel panel-pad">
          <div class="panel-title"><div><h2>待处理队列</h2><p>{{files.length}} 个文件</p></div><el-button text :disabled="files.length === 0 || submitting" @click="clearQueue">清空队列</el-button></div>
          <el-empty v-if="files.length === 0" description="尚未选择文件" :image-size="72" />
          <article v-for="(file,index) in files" :key="file.clientFileId" class="file-row"><span class="file-icon" :class="file.name.toLowerCase().endsWith('.pdf')?'pdf':'docx'"><el-icon><Document /></el-icon></span><div class="file-copy"><header><b>{{file.name}}</b><el-button text circle :icon="Close" :disabled="submitting" @click="removeFile(index)" /></header><div class="file-meta"><span>{{({pending:'待上传',uploading:'上传中',uploaded:'已暂存',failed:'可重试',submitted:'已提交'})[file.state]}}</span><i>·</i><span>大小：{{file.size}}</span></div><footer><span>{{file.detail}}</span></footer></div></article>
          <div class="compliance"><el-icon><Check /></el-icon><span>提交前请确认文档内容及元数据准确无误</span><b>合计总大小：{{totalSize}}</b></div>
        </section>
        <div class="tip-card"><el-icon><InfoFilled /></el-icon><p><b>提升检索召回率小贴士</b>带有结构化标题序号（如一、1.1、(1)）的文档切片精度更高；对于表格密集的规范文件，建议保留“智能 OCR”开启状态。</p></div>
      </section>

      <aside class="config-panel panel">
        <header><div><el-icon><Setting /></el-icon><h2>元数据与解析配置</h2></div><small>以下配置应用于本批全部文件</small></header>
        <el-form label-position="top" class="config-form">
          <el-alert v-if="loadError" :title="loadError" type="error" show-icon :closable="false"><template #default><el-button link type="primary" @click="loadPage">重新加载</el-button></template></el-alert>
          <el-alert v-else-if="!pageLoading && !knowledgeBases.length" class="upload-blocker" title="尚未创建可管理的知识库，文件无法确定入库位置。" type="warning" show-icon :closable="false"><template #default><el-button link type="primary" @click="createKbOpen = true">立即创建首个知识库</el-button></template></el-alert>
          <el-form-item label="目标知识库 *"><el-select v-model="config.kb" style="width:100%" :loading="pageLoading" :disabled="knowledgeBases.length === 0" placeholder="暂无可管理知识库"><el-option v-for="item in knowledgeBases" :key="item.id" :label="item.name" :value="item.id" /></el-select><small>仅显示当前账号可管理的知识库　<el-button link type="primary" @click="createKbOpen = true">新建知识库</el-button></small></el-form-item>
          <el-form-item label="业务分类 *"><el-radio-group v-model="config.category" class="category-radios"><el-radio-button value="规章制度">规章制度<small>条例、细则、办法</small></el-radio-button><el-radio-button value="办事指南">办事指南<small>办事流程、服务指引</small></el-radio-button><el-radio-button value="通知公告">通知公告<small>时效性发布、通知</small></el-radio-button><el-radio-button value="表格模板">表格模板<small>申请单、填报标准</small></el-radio-button></el-radio-group></el-form-item>
          <div class="form-two"><el-form-item label="责任发布部门 *"><el-select v-model="config.department" :disabled="departments.length === 0" placeholder="暂无部门数据"><el-option v-for="item in departments" :key="item.id" :label="item.name" :value="item.id" /></el-select></el-form-item><el-form-item label="正式发文编号"><el-input v-model="config.number" placeholder="请输入正式发文编号"/></el-form-item></div>
          <el-form-item label="生效日期"><el-date-picker v-model="config.effective" type="date" value-format="YYYY-MM-DD" style="width:100%" /></el-form-item>
          <div class="advanced-title"><el-icon><Setting /></el-icon><b>高级解析与切片参数 (RAG Pipeline)</b></div>
          <div class="switch-setting"><div><b>开启智能 OCR 解析</b><small>自动识别扫描件公章、嵌入图表和手写文字</small></div><el-switch v-model="config.ocr" /></div>
          <div class="slider-grid"><label>切片最大长度 <b>{{config.chunk}} Tokens</b><el-slider v-model="config.chunk" :min="256" :max="1024" :step="64" /></label><label>重叠窗口长度 <b>{{config.overlap}} Tokens</b><el-slider v-model="config.overlap" :min="0" :max="Math.min(256, config.chunk - 16)" :step="16" /></label></div>
          <div class="switch-setting"><div><b>解析完成后自动向量化入库</b><small>处理流水线会使用系统配置的嵌入模型与向量索引服务</small></div><StatusPill tone="success" text="已启用" /></div>
        </el-form>
      </aside>
    </div>

    <section v-else class="panel task-center"><header><div><h2>处理任务</h2><p>每 3 秒自动更新文档解析、切片和向量入库进度</p></div><el-button :icon="Refresh" @click="loadTasks()">刷新</el-button></header><el-table :data="tasks" row-key="id"><el-table-column prop="taskNo" label="任务编号" min-width="170"/><el-table-column prop="type" label="类型" width="155"/><el-table-column label="状态" width="120"><template #default="{row}"><StatusPill :tone="row.status==='succeeded'?'success':row.status==='failed'?'danger':'info'" :text="row.status"/></template></el-table-column><el-table-column label="进度" min-width="220"><template #default="{row}"><el-progress :percentage="row.progressPercent || 0" :status="row.status==='failed'?'exception':row.status==='succeeded'?'success':''"/><small>{{row.progressText || row.error?.message}}</small></template></el-table-column><el-table-column label="创建时间" min-width="160"><template #default="{row}">{{new Date(row.createdAt).toLocaleString('zh-CN')}}</template></el-table-column><el-table-column label="操作" width="150"><template #default="{row}"><el-button v-if="['failed','cancelled'].includes(row.status)" text @click="retryTask(row)">重试</el-button><el-button v-if="!['succeeded','failed','cancelled','partial_success'].includes(row.status)" text type="danger" @click="cancelTask(row)">取消</el-button></template></el-table-column></el-table><el-empty v-if="!tasks.length" description="暂无处理任务" /></section>

    <footer v-if="mode==='上传文档'" class="submit-bar panel"><div><StatusPill :text="files.length ? `已选择 ${files.length} 个本地文件` : '等待选择文件'" /><span>文件总大小 {{totalSize}}</span></div><el-button :disabled="files.length === 0 || submitting" @click="clearQueue">取消</el-button><el-button :loading="submitting" :disabled="files.length === 0" @click="submit(true)">保存为草稿</el-button><el-button class="primary-btn" :loading="submitting" :disabled="files.length === 0" @click="submit(false)">{{config.kb ? `确认上传并开始入库处理（${files.length}个文件）` : '先创建知识库再继续'}}</el-button></footer>
    <QuickKnowledgeBaseDialog v-model="createKbOpen" @created="handleKnowledgeBaseCreated" />
  </div>
</template>

<style scoped>
.upload-page{padding-bottom:90px}.upload-head{display:flex;align-items:flex-end;justify-content:space-between;margin-bottom:18px}.upload-head>div:first-child>p{display:flex;align-items:center;gap:6px;margin:0 0 7px;color:#94a3b8;font-size:10px}.upload-head>div:first-child>div{display:flex;align-items:baseline;gap:10px}.upload-head h1{margin:0;font-size:26px}.upload-head>div:first-child span{color:#0051d5;font-size:8px;letter-spacing:.08em}.upload-head>div:last-child{display:flex;gap:8px}.upload-grid{display:grid;grid-template-columns:minmax(0,1fr) 420px;gap:17px;align-items:start}.upload-left{min-width:0}.dropzone :deep(.el-upload-dragger){min-height:275px;display:flex;flex-direction:column;align-items:center;justify-content:center;border:2px dashed #bfdbfe;border-radius:16px;background:linear-gradient(140deg,#fff,#f6f9ff)}.upload-icon{font-size:36px;color:#0051d5}.dropzone h3{margin:13px 0 5px;font-size:14px}.dropzone p{margin:0 0 13px;color:#64748b;font-size:9px}.dropzone>span{margin-top:8px;color:#94a3b8;font-size:8px}.file-types{display:flex;gap:7px;margin-top:13px}.file-types i{padding:4px 7px;border-radius:5px;background:#f1f5f9;color:#64748b;font-size:8px;font-style:normal;font-weight:700}.queue{margin-top:14px}.file-row{display:flex;gap:12px;padding:12px 0;border-top:1px solid #eff4ff}.file-icon{width:38px;height:38px;flex:none;display:grid;place-items:center;border-radius:10px}.file-icon.pdf{background:#fef2f2;color:#dc2626}.file-icon.docx{background:#eff4ff;color:#0051d5}.file-copy{min-width:0;flex:1}.file-copy header{display:flex;align-items:center}.file-copy header b{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:10px}.file-meta{display:flex;align-items:center;gap:7px;margin:3px 0 7px;color:#64748b;font-size:8px}.file-copy footer{display:flex;justify-content:space-between;margin-top:5px;color:#059669;font-size:8px}.file-copy footer small{color:#94a3b8}.compliance{display:flex;align-items:center;gap:7px;padding:9px;margin-top:8px;border-radius:8px;background:#ecfdf5;color:#059669;font-size:8px}.compliance b{margin-left:auto;color:#64748b}.tip-card{display:flex;gap:10px;padding:13px;margin-top:13px;border-radius:12px;background:#fff;color:#0051d5}.tip-card p{margin:0;color:#64748b;font-size:9px;line-height:1.6}.tip-card b{display:block;margin-bottom:3px;color:#334155}.config-panel{position:sticky;top:88px}.config-panel>header{display:flex;align-items:center;justify-content:space-between;padding:16px 18px;border-bottom:1px solid #eff4ff}.config-panel>header>div{display:flex;align-items:center;gap:8px}.config-panel h2{margin:0;font-size:14px}.config-form{padding:17px}.upload-blocker{margin-bottom:14px}.config-form :deep(.el-form-item){margin-bottom:14px}.config-form :deep(.el-form-item__label){font-size:9px;font-weight:700}.config-form small{display:block;margin-top:5px;color:#94a3b8;font-size:8px}.category-radios{display:grid;grid-template-columns:1fr 1fr;width:100%}.category-radios :deep(.el-radio-button__inner){width:100%;height:48px;display:flex;align-items:flex-start;flex-direction:column;font-size:9px}.category-radios :deep(small){font-weight:400}.form-two{display:grid;grid-template-columns:1fr 1fr;gap:9px}.advanced-title{display:flex;align-items:center;gap:7px;padding-top:2px;margin-bottom:11px;color:#334155;font-size:10px}.switch-setting{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:10px;margin-bottom:9px;border-radius:9px;background:#f8fafc}.switch-setting b,.switch-setting small{display:block}.switch-setting b{font-size:9px}.slider-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;padding:10px}.slider-grid label{font-size:8px}.slider-grid b{float:right;color:#0051d5}.submit-bar{position:fixed;z-index:29;right:32px;bottom:14px;left:288px;display:flex;align-items:center;gap:8px;padding:11px 14px}.submit-bar>div{flex:1;display:flex;align-items:center;gap:10px}.submit-bar>div>span:last-child{color:#64748b;font-size:8px}
.task-center{padding:18px;overflow:hidden}.task-center>header{display:flex;align-items:center;justify-content:space-between;margin-bottom:15px}.task-center h2{margin:0;font-size:16px}.task-center header p{margin:4px 0 0;color:#64748b;font-size:10px}.task-center small{display:block;margin-top:5px;color:#64748b;font-size:8px}
@media(max-width:1200px){.upload-grid{grid-template-columns:1fr}.config-panel{position:static}.submit-bar{left:274px}}
@media(max-width:1100px){.submit-bar{left:18px;right:18px}.upload-head{align-items:flex-start;flex-direction:column;gap:10px}.task-center{overflow-x:auto}.task-center :deep(.el-table){min-width:820px}}
@media(max-width:720px){.upload-page{padding-bottom:135px}.form-two,.slider-grid{grid-template-columns:1fr}.submit-bar{align-items:stretch;flex-wrap:wrap}.submit-bar>div{flex-basis:100%;flex-direction:column;align-items:flex-start}.submit-bar>.el-button:not(.primary-btn){display:none}.submit-bar>.primary-btn{width:100%;margin:0}.upload-head>div:last-child{display:none}.compliance b{display:none}}
</style>
