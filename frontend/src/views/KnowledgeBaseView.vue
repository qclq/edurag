<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Collection, Operation, Plus, Refresh, Search, Setting, Upload, View } from '@element-plus/icons-vue'
import MetricCard from '../components/MetricCard.vue'
import StatusPill from '../components/StatusPill.vue'
import { authenticatedRequest } from '../stores/auth'

const route = useRoute()
const drawer = ref(false)
const viewMode = ref('卡片视图')
const query = ref('')
const kbs = ref([])
const departments = ref([])
const selected = ref(null)
const loading = ref(false)
const saving = ref(false)
const retrievalQuery = ref('')
const retrievalResult = ref(null)
const createOpen = ref(false)
const statusFilter = ref('')
let queryTimer = null
const form = reactive({ name: '', description: '', departmentId: null, businessCategory: '', selectable: true, chunkSize: 800, chunkOverlap: 100, topK: 5, similarityThreshold: 0.55, rerankEnabled: true, hybridSearchEnabled: true, promptInstruction: '' })
const createForm = reactive({ name: '', code: '', description: '', departmentId: null, businessCategory: '', selectable: true, publicSearch: true })
const totalDocuments = computed(() => kbs.value.reduce((sum, item) => sum + (item.documentCount || 0), 0))
const readyDocuments = computed(() => kbs.value.reduce((sum, item) => sum + (item.readyDocumentCount || 0), 0))

const loadKbs = async () => {
  loading.value = true
  const params = new URLSearchParams({ pageSize: '100' })
  if (query.value.trim()) params.set('q', query.value.trim())
  if (statusFilter.value) params.set('status', statusFilter.value)
  try {
    const envelope = await authenticatedRequest(`/admin/knowledge-bases?${params}`, { includeEnvelope: true })
    kbs.value = (envelope.data || []).map(item => ({ ...item, icon: item.name.slice(0, 1), departmentName: departments.value.find(dept => dept.id === item.departmentId)?.name || '未归属部门' }))
    const requested = typeof route.query.selected === 'string' ? route.query.selected : null
    if (requested) await selectKb(kbs.value.find(item => item.id === requested) || { id: requested })
  } catch (error) {
    ElMessage.error(error.message || '知识库加载失败')
  } finally {
    loading.value = false
  }
}

const assignForm = (item) => {
  Object.assign(form, {
    name: item.name, description: item.description || '', departmentId: item.departmentId,
    businessCategory: item.businessCategory || '', selectable: item.selectable,
    chunkSize: item.retrieval?.chunkSize || 800, chunkOverlap: item.retrieval?.chunkOverlap || 100,
    topK: item.retrieval?.topK || 5, similarityThreshold: item.retrieval?.similarityThreshold ?? 0.55,
    rerankEnabled: item.retrieval?.rerankEnabled ?? true, hybridSearchEnabled: item.retrieval?.hybridSearchEnabled ?? true,
    promptInstruction: item.retrieval?.promptInstruction || '',
  })
}

const selectKb = async (item) => {
  try {
    selected.value = await authenticatedRequest(`/admin/knowledge-bases/${item.id}`)
    assignForm(selected.value)
    retrievalResult.value = null
    drawer.value = true
  } catch (error) {
    ElMessage.error(error.message || '知识库详情加载失败')
  }
}

const save = async () => {
  if (!selected.value) return
  saving.value = true
  try {
    selected.value = await authenticatedRequest(`/admin/knowledge-bases/${selected.value.id}`, {
      method: 'PATCH',
      body: {
        name: form.name, description: form.description || null, departmentId: form.departmentId,
        businessCategory: form.businessCategory || null, selectable: form.selectable, version: selected.value.version,
        retrieval: { chunkSize: form.chunkSize, chunkOverlap: form.chunkOverlap, topK: form.topK, similarityThreshold: form.similarityThreshold, rerankEnabled: form.rerankEnabled, hybridSearchEnabled: form.hybridSearchEnabled, promptInstruction: form.promptInstruction || null },
      },
    })
    assignForm(selected.value)
    ElMessage.success('知识库配置已保存')
    await loadKbs()
  } catch (error) {
    ElMessage.error(error.message || '保存失败')
  } finally {
    saving.value = false
  }
}

const createKb = async () => {
  if (!createForm.name.trim() || !createForm.code.trim()) return ElMessage.warning('请填写名称和编码')
  saving.value = true
  try {
    const created = await authenticatedRequest('/admin/knowledge-bases', {
      method: 'POST', body: {
        name: createForm.name, code: createForm.code, description: createForm.description || null,
        departmentId: createForm.departmentId, businessCategory: createForm.businessCategory || null,
        selectable: createForm.selectable, ownerIds: [],
        acl: createForm.publicSearch ? [{ isPublic: true, canSearch: true, canPreview: true, canDownload: false, canManage: false }] : [],
        retrieval: { chunkSize: 800, chunkOverlap: 100, topK: 5, similarityThreshold: 0.55, rerankEnabled: true, hybridSearchEnabled: true },
      },
    })
    createOpen.value = false
    Object.assign(createForm, { name: '', code: '', description: '', departmentId: null, businessCategory: '', selectable: true, publicSearch: true })
    await loadKbs(); await selectKb(created)
    ElMessage.success('知识库已创建')
  } catch (error) {
    ElMessage.error(error.message || '创建失败')
  } finally { saving.value = false }
}

const toggleStatus = async (item) => {
  const status = item.status === 'enabled' ? 'disabled' : 'enabled'
  try {
    await authenticatedRequest(`/admin/knowledge-bases/${item.id}/status`, { method: 'POST', body: { status, reason: '管理员在管理页面手动调整' } })
    ElMessage.success(status === 'enabled' ? '知识库已启用' : '知识库已停用')
    await loadKbs(); if (selected.value?.id === item.id) await selectKb(item)
  } catch (error) { ElMessage.error(error.message || '状态更新失败') }
}

const rebuild = async (item) => {
  try {
    await authenticatedRequest(`/admin/knowledge-bases/${item.id}/rebuild-index`, { method: 'POST', body: { reason: '管理员手动重建索引' } })
    ElMessage.success('索引重建任务已提交')
  } catch (error) { ElMessage.error(error.message || '重建任务提交失败') }
}

const rebuildVisible = async () => {
  if (!kbs.value.length) return
  try {
    await ElMessageBox.confirm(`将为当前列表中的 ${kbs.value.length} 个知识库提交重建任务，是否继续？`, '批量重建索引', { type: 'warning' })
    const results = await Promise.allSettled(kbs.value.map(item => authenticatedRequest(`/admin/knowledge-bases/${item.id}/rebuild-index`, { method: 'POST', body: { reason: '管理员批量重建索引' } })))
    ElMessage.success(`已提交 ${results.filter(item => item.status === 'fulfilled').length} 个重建任务`)
  } catch (error) { if (error !== 'cancel' && error !== 'close') ElMessage.error(error.message || '批量重建失败') }
}

const runRetrievalTest = async () => {
  if (!retrievalQuery.value.trim() || !selected.value) return
  try {
    retrievalResult.value = await authenticatedRequest(`/admin/knowledge-bases/${selected.value.id}/retrieval-tests`, { method: 'POST', body: { query: retrievalQuery.value } })
  } catch (error) { ElMessage.error(error.message || '检索测试失败') }
}

watch([query, statusFilter], () => { window.clearTimeout(queryTimer); queryTimer = window.setTimeout(loadKbs, 300) })
onMounted(async () => {
  try { departments.value = await authenticatedRequest('/admin/departments') } catch { departments.value = [] }
  await loadKbs()
})
</script>

<template>
  <div class="page-content kb-page" :class="{ 'drawer-closed': !drawer }">
    <section class="kb-main">
      <div class="cluster-banner panel"><span><el-icon><Collection /></el-icon></span><div><b>RAG 知识库与权限域</b><p>展示当前账号可管理的知识库、文档就绪情况与检索参数</p></div><strong>{{ kbs.filter(item=>item.status==='enabled').length }} 个已启用</strong></div>
      <div class="kb-actions"><el-input v-model="query" :prefix-icon="Search" clearable placeholder="输入知识库名称或编码..."/><el-button :icon="Refresh" :disabled="!kbs.length" @click="rebuildVisible">批量重建索引</el-button><el-button class="primary-btn" :icon="Plus" @click="createOpen=true">新建知识库</el-button></div>
      <div class="kb-filters panel"><el-segmented v-model="viewMode" :options="['卡片视图','表格视图']" /><el-select v-model="statusFilter" clearable placeholder="全部状态"><el-option label="已启用" value="enabled"/><el-option label="已停用" value="disabled"/></el-select><span /><span /></div>
      <div class="kb-summary card-grid metric-grid"><MetricCard label="可管理知识库" :value="kbs.length" unit="个" tone="green"><el-icon><Collection /></el-icon></MetricCard><MetricCard label="收录文档" :value="totalDocuments" unit="篇" tone="blue"><el-icon><Upload /></el-icon></MetricCard><MetricCard label="已就绪文档" :value="readyDocuments" unit="篇" tone="green"><el-icon><Operation /></el-icon></MetricCard><MetricCard label="待处理文档" :value="Math.max(0,totalDocuments-readyDocuments)" unit="篇" tone="blue"><el-icon><View /></el-icon></MetricCard></div>

      <div v-if="viewMode==='卡片视图'" class="kb-card-grid"><article v-for="kb in kbs" :key="kb.id" class="kb-card panel" @click="selectKb(kb)"><header><span class="kb-logo">{{kb.icon}}</span><div><h3>{{kb.name}}</h3><p>{{kb.departmentName}} · {{kb.code}}</p></div><StatusPill :tone="kb.status==='enabled'?'success':'warning'" :text="kb.statusLabel" /></header><p class="kb-desc">{{kb.description || '暂无描述'}}</p><div class="kb-stats"><div><small>文档</small><b>{{kb.documentCount}}</b></div><div><small>已就绪</small><b>{{kb.readyDocumentCount}}</b></div><div><small>负责人</small><b>{{kb.ownerIds?.length || 0}}</b></div></div><footer><el-button text @click.stop="selectKb(kb)">配置</el-button><el-button text @click.stop="rebuild(kb)">重建索引</el-button><el-button text @click.stop="toggleStatus(kb)">{{kb.status==='enabled'?'停用':'启用'}}</el-button></footer></article><el-empty v-if="!kbs.length && !loading" description="暂无可管理的知识库" /></div>
      <el-table v-else :data="kbs" class="panel" style="width:100%"><el-table-column prop="name" label="知识库名称 / 部门" min-width="240"><template #default="{row}"><b>{{row.name}}</b><p class="table-sub">{{row.departmentName}} · {{row.code}}</p></template></el-table-column><el-table-column label="文档 / 就绪" width="140"><template #default="{row}">{{row.documentCount}} 篇<br><span class="muted">{{row.readyDocumentCount}} 篇可用</span></template></el-table-column><el-table-column prop="businessCategory" label="业务分类" min-width="160"/><el-table-column label="状态" width="130"><template #default="{row}"><StatusPill :tone="row.status==='enabled'?'success':'warning'" :text="row.statusLabel"/></template></el-table-column><el-table-column label="操作" width="180"><template #default="{row}"><el-button text @click="selectKb(row)">配置</el-button><el-button text @click="rebuild(row)">重建</el-button></template></el-table-column></el-table>

      <section class="topology panel panel-pad"><div class="panel-title"><div><h2>知识库状态说明</h2><p>文档完成解析和向量化后才会进入问答检索范围</p></div></div><div class="topology-row"><article><span>KB</span><div><b>知识库配置</b><p>定义业务范围、权限和检索参数。</p></div></article><i/><article><span>DOC</span><div><b>文档处理</b><p>上传、解析、切片并生成向量索引。</p></div></article><i/><article><span>RAG</span><div><b>问答检索</b><p>只使用已启用且处理就绪的资料。</p></div></article></div></section>
    </section>

    <aside v-if="drawer" class="kb-drawer panel"><header><div><b>知识库基本信息与检索参数</b><small>{{ selected?.code }} · 版本 {{ selected?.version }}</small></div><el-button text @click="drawer=false">收起</el-button></header><div v-if="selected" class="drawer-form"><label>名称<el-input v-model="form.name" /></label><label>描述<el-input v-model="form.description" type="textarea" :rows="3" /></label><label>所属部门<el-select v-model="form.departmentId" clearable><el-option v-for="item in departments" :key="item.id" :label="item.name" :value="item.id" /></el-select></label><label>业务分类<el-input v-model="form.businessCategory" /></label><div class="switch-row"><div><b>允许用户主动选择</b><small>关闭后不显示在新会话知识库选项中</small></div><el-switch v-model="form.selectable" /></div><h4>检索参数</h4><label>切片长度 {{form.chunkSize}}<el-slider v-model="form.chunkSize" :min="100" :max="4000" :step="100" /></label><label>切片重叠 {{form.chunkOverlap}}<el-slider v-model="form.chunkOverlap" :min="0" :max="Math.min(1000,form.chunkSize-1)" :step="20" /></label><label>召回数量<el-input v-model.number="form.topK" type="number" min="1" max="50" /></label><label>相似度阈值 {{form.similarityThreshold}}<el-slider v-model="form.similarityThreshold" :min="0" :max="1" :step="0.05" /></label><div class="switch-row"><div><b>混合检索</b><small>结合关键词与向量召回</small></div><el-switch v-model="form.hybridSearchEnabled" /></div><div class="switch-row"><div><b>结果重排</b><small>对候选切片再次排序</small></div><el-switch v-model="form.rerankEnabled" /></div><label>回答约束<el-input v-model="form.promptInstruction" type="textarea" :rows="3" placeholder="可选：该知识库专用回答规则" /></label><el-button class="primary-btn save-btn" :loading="saving" @click="save">保存配置</el-button><div class="test-box"><header><b>即席检索测试</b><el-button text :disabled="!retrievalQuery.trim()" @click="runRetrievalTest">执行</el-button></header><el-input v-model="retrievalQuery" placeholder="输入测试问题" @keyup.enter="runRetrievalTest" /><div v-if="retrievalResult" class="test-result"><span>{{retrievalResult.chunks?.length || 0}} 条</span><p>{{retrievalResult.testAnswer || retrievalResult.timing?.answerError?.message || '未检索到可靠答案'}}</p></div></div></div></aside>

    <el-dialog v-model="createOpen" title="新建知识库" width="620" align-center append-to-body><el-form label-position="top"><div class="form-two"><el-form-item label="知识库名称 *"><el-input v-model="createForm.name" /></el-form-item><el-form-item label="唯一编码 *"><el-input v-model="createForm.code" placeholder="例如 STUDENT_POLICY" /></el-form-item></div><el-form-item label="描述"><el-input v-model="createForm.description" type="textarea" :rows="3" /></el-form-item><div class="form-two"><el-form-item label="所属部门"><el-select v-model="createForm.departmentId" clearable style="width:100%"><el-option v-for="item in departments" :key="item.id" :label="item.name" :value="item.id" /></el-select></el-form-item><el-form-item label="业务分类"><el-input v-model="createForm.businessCategory" /></el-form-item></div><div class="switch-row"><div><b>允许全校用户检索与预览</b><small>关闭后只有负责人和另行配置的 ACL 主体可访问</small></div><el-switch v-model="createForm.publicSearch" /></div></el-form><template #footer><el-button @click="createOpen=false">取消</el-button><el-button class="primary-btn" :loading="saving" @click="createKb">创建</el-button></template></el-dialog>
  </div>
</template>

<style scoped>
.kb-page{display:grid;grid-template-columns:minmax(0,1fr) 350px;gap:18px;align-items:start}.kb-page.drawer-closed{grid-template-columns:1fr}.kb-main{min-width:0}.cluster-banner{display:flex;align-items:center;gap:12px;padding:15px 18px}.cluster-banner>span{width:38px;height:38px;display:grid;place-items:center;border-radius:10px;background:#eff4ff;color:#0051d5}.cluster-banner>div{flex:1}.cluster-banner b{font-size:13px}.cluster-banner p{margin:4px 0 0;color:#64748b;font-size:9px}.cluster-banner>strong{font-size:9px;color:#64748b}.kb-actions{display:flex;gap:9px;margin:14px 0}.kb-actions .el-input{flex:1}.kb-filters{display:grid;grid-template-columns:auto 1fr 1fr 1fr;gap:9px;padding:11px;margin-bottom:14px}.kb-summary{margin-bottom:14px}.kb-summary :deep(.metric-card){min-height:102px;padding:16px}.kb-card-grid{display:grid;grid-template-columns:1fr 1fr;gap:13px}.kb-card{padding:17px;cursor:pointer}.kb-card header{display:flex;align-items:center;gap:10px}.kb-logo{width:39px;height:39px;display:grid;place-items:center;border-radius:11px;background:#eff4ff;font-weight:800}.kb-card header>div{flex:1;min-width:0}.kb-card h3{margin:0;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.kb-card header p{margin:3px 0 0;color:#94a3b8;font-size:8px}.kb-desc{height:34px;margin:12px 0;color:#64748b;font-size:9px;line-height:1.6}.kb-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:7px}.kb-stats>div{padding:9px;border-radius:8px;background:#f8fafc}.kb-stats small,.kb-stats b{display:block}.kb-stats small{color:#94a3b8;font-size:8px}.kb-stats b{margin-top:4px;font-size:10px}.acl{display:flex;justify-content:space-between;padding:9px;margin-top:9px;border-radius:8px;background:#f8fafc;color:#64748b;font-size:8px}.kb-card footer{display:flex;gap:4px;padding-top:10px;margin-top:10px;border-top:1px solid #eff4ff}.topology{margin-top:14px}.topology-row{display:flex;align-items:center;gap:20px}.topology-row article{display:flex;align-items:flex-start;gap:10px;flex:1}.topology-row article>span{width:38px;height:38px;display:grid;place-items:center;border-radius:10px;background:#06080c;color:white;font-size:9px;font-weight:800}.topology-row b{font-size:10px}.topology-row p{margin:5px 0 0;color:#64748b;font-size:8px;line-height:1.6}.topology-row>i{width:60px;border-top:2px dashed #93c5fd}.table-sub{margin:3px 0 0;color:#94a3b8;font-size:9px}.kb-drawer{position:sticky;top:88px;max-height:calc(100vh - 104px);overflow:auto}.kb-drawer>header{position:sticky;z-index:2;top:0;display:flex;align-items:center;justify-content:space-between;padding:16px 17px;border-bottom:1px solid #eff4ff;background:#fff}.kb-drawer>header>div{display:flex;flex-direction:column}.kb-drawer>header b{font-size:12px}.kb-drawer>header small{margin-top:3px;color:#94a3b8;font-size:8px}.drawer-form{display:grid;gap:14px;padding:17px}.drawer-form>label{display:grid;gap:7px;color:#475569;font-size:9px;font-weight:700}.drawer-form h4{margin:3px 0 -3px;font-size:11px}.switch-row{display:flex;align-items:center;justify-content:space-between;gap:9px;padding:10px;border-radius:9px;background:#f8fafc}.switch-row>div{display:flex;flex-direction:column}.switch-row b{font-size:9px}.switch-row small{margin-top:3px;color:#94a3b8;font-size:8px}.save-btn{width:100%}.test-box{padding:12px;border-radius:11px;background:#f8fafc}.test-box>header{display:flex;justify-content:space-between;align-items:center;margin-bottom:9px}.test-box b{font-size:9px}.test-result{padding:9px;margin-top:8px;border-radius:8px;background:#fff}.test-result span{float:right;color:#059669;font-size:8px}.test-result p{margin:5px 0 0;color:#64748b;font-size:8px;line-height:1.5}.form-two{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media(max-width:1280px){.kb-page{grid-template-columns:1fr}.kb-drawer{position:static;max-height:none}.kb-summary{grid-template-columns:repeat(2,1fr)}}
@media(max-width:760px){.kb-actions{flex-wrap:wrap}.kb-actions .el-input{flex-basis:100%}.kb-filters{grid-template-columns:1fr 1fr}.kb-card-grid{grid-template-columns:1fr}.topology-row{align-items:flex-start;flex-direction:column}.topology-row>i{display:none}}
@media(max-width:520px){.kb-filters{grid-template-columns:1fr}.kb-summary{grid-template-columns:1fr}.cluster-banner>strong,.cluster-banner>.status-pill{display:none}}
</style>
