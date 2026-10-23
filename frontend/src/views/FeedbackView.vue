<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  ChatDotRound, Check, Clock, DocumentChecked, EditPen, Refresh,
  Search, Select, TrendCharts, Warning,
} from '@element-plus/icons-vue'
import PageHeader from '../components/PageHeader.vue'
import MetricCard from '../components/MetricCard.vue'
import StatusPill from '../components/StatusPill.vue'
import { authenticatedRequest } from '../stores/auth'

const activeTab = ref('questions')
const keyword = ref('')
const detailOpen = ref(false)
const selectedRecord = ref(null)
const resolution = ref('')
const note = ref('')
const notifyUser = ref(true)
const loading = ref(false)
const detailData = ref(null)
const qaStatus = ref('')
const feedbackStatus = ref('')
const timeRange = ref('7d')

const questions = ref([])
const feedbacks = ref([])
const archives = ref([])

const statusTone = status => status === 'completed' || status === 'resolved' ? 'success' : status === 'failed' ? 'danger' : status === 'no_answer' || status === 'pending' ? 'warning' : 'info'
const statusLabel = status => ({ completed:'回答完成', no_answer:'未检索到可靠依据', failed:'回答失败', stopped:'已停止', submitted:'已提交', retrieving:'检索中', generating:'生成中', pending:'待处理', processing:'处理中', resolved:'已解决' })[status] || status
const feedbackTypeLabel = type => ({
  unhelpful:'回答需改进',
  user_suggestion:'功能建议', user_bug:'系统故障', user_content:'内容问题', user_other:'其他反馈',
  conversation_answer_incorrect:'回答不正确', conversation_answer_incomplete:'回答不完整',
  conversation_citation_issue:'引用问题', conversation_other:'对话其他问题',
})[type] || type
const resolutionOptions = [
  { label:'回答内容无误', value:'answer_correct' }, { label:'知识库缺少相关文档', value:'missing_document' },
  { label:'文档已过期', value:'document_outdated' }, { label:'文档内容冲突', value:'document_conflict' },
  { label:'检索结果不相关', value:'irrelevant_retrieval' }, { label:'模型生成错误', value:'generation_error' },
  { label:'用户问题信息不足', value:'insufficient_question' }, { label:'其他', value:'other' },
]

const withinRange = row => {
  if (!timeRange.value) return true
  const days = { today:1, '7d':7, '30d':30 }[timeRange.value]
  return !days || new Date(row.createdAt) >= new Date(Date.now() - days * 86400000)
}
const visibleQuestions = computed(() => questions.value.filter(row => (!keyword.value || `${row.question}${row.user}${row.kb}`.includes(keyword.value)) && (!qaStatus.value || row.status === qaStatus.value) && withinRange(row)))
const visibleFeedbacks = computed(() => feedbacks.value.filter(row => (!keyword.value || `${row.question}${row.message}${row.kb}`.includes(keyword.value)) && (!feedbackStatus.value || row.status === feedbackStatus.value) && withinRange(row)))
const visibleArchives = computed(() => archives.value.filter(row => (!keyword.value || `${row.knowledgeBaseName}${row.summary}`.includes(keyword.value)) && withinRange({ createdAt:row.date })))

const loadData = async () => {
  loading.value = true
  const results = await Promise.allSettled([
    authenticatedRequest('/admin/qa-records?pageSize=100', { includeEnvelope: true }),
    authenticatedRequest('/admin/feedbacks?pageSize=100', { includeEnvelope: true }),
    authenticatedRequest('/admin/qa-summaries?pageSize=100', { includeEnvelope: true }),
  ])
  if (results[0].status === 'fulfilled') questions.value = (results[0].value.data || []).map(item => ({ ...item, user:item.userId, session:item.conversationId, kb:item.knowledgeBaseNames?.join('、') || '未关联知识库', state:statusLabel(item.status), tone:statusTone(item.status), refs:item.citationCount, time:new Date(item.createdAt).toLocaleString('zh-CN'), latency:item.totalMs != null ? `${item.totalMs}ms` : '—' }))
  if (results[1].status === 'fulfilled') feedbacks.value = (results[1].value.data || []).map(item => ({ ...item, type:feedbackTypeLabel(item.type), question:item.comment || `反馈 ${item.id.slice(0,8)}`, message:item.comment || '用户未补充说明', kb:item.knowledgeBaseId || '未关联知识库', state:statusLabel(item.status), tone:statusTone(item.status), owner:item.assigneeId ? '已领取' : '待领取', time:new Date(item.createdAt).toLocaleString('zh-CN'), updated:new Date(item.updatedAt).toLocaleString('zh-CN') }))
  if (results[2].status === 'fulfilled') archives.value = results[2].value.data || []
  const failed = results.find(item => item.status === 'rejected')
  if (failed && !questions.value.length && !feedbacks.value.length && !archives.value.length) ElMessage.error(failed.reason?.message || '质量数据加载失败')
  loading.value = false
}

const openDetail = async (record) => {
  selectedRecord.value = record
  resolution.value = ''
  note.value = ''
  detailData.value = null
  detailOpen.value = true
  try {
    detailData.value = activeTab.value === 'questions'
      ? await authenticatedRequest(`/admin/qa-records/${record.id}`)
      : await authenticatedRequest(`/admin/feedbacks/${record.id}`)
  } catch (error) { ElMessage.error(error.message || '详情加载失败') }
}

const submitResolution = async () => {
  if (!resolution.value) {
    ElMessage.warning('请选择处理结论后再提交')
    return
  }
  if (!note.value.trim()) return ElMessage.warning('请填写处理备注')
  try {
    await authenticatedRequest(`/admin/feedbacks/${selectedRecord.value.id}/resolve`, { method:'POST', body:{ resolution:resolution.value, note:note.value, notifyUser:notifyUser.value, relatedDocumentIds:[] } })
    ElMessage.success('反馈已解决并保存处理记录')
    detailOpen.value = false
    await loadData()
  } catch (error) { ElMessage.error(error.message || '反馈处理失败') }
}

const claimFeedback = async () => {
  try { await authenticatedRequest(`/admin/feedbacks/${selectedRecord.value.id}/claim`, {method:'POST'}); ElMessage.success('已领取该反馈'); await loadData(); detailOpen.value=false }
  catch (error) { ElMessage.error(error.message || '领取反馈失败') }
}

const exportData = () => {
  const rows = activeTab.value === 'questions'
    ? [['问题','状态','引用数','时间'], ...visibleQuestions.value.map(item=>[item.question,item.state,item.refs,item.time])]
    : activeTab.value === 'feedbacks'
      ? [['反馈','类型','状态','时间'], ...visibleFeedbacks.value.map(item=>[item.message,item.type,item.state,item.time])]
      : [['日期','知识库','问答数','正常回答','未命中','失败','摘要'], ...visibleArchives.value.map(item=>[item.date,item.knowledgeBaseName,item.questionCount,item.answeredCount,item.noAnswerCount,item.failedCount,item.summary])]
  const names = { questions:'问答记录', feedbacks:'反馈记录', archives:'归档摘要' }
  const csv=rows.map(row=>row.map(value=>`"${String(value??'').replaceAll('"','""')}"`).join(',')).join('\r\n');const url=URL.createObjectURL(new Blob([`\uFEFF${csv}`],{type:'text/csv;charset=utf-8'}));const link=document.createElement('a');link.href=url;link.download=`${names[activeTab.value]}.csv`;link.click();URL.revokeObjectURL(url)
}

onMounted(loadData)
</script>

<template>
  <div class="page-content feedback-page">
    <PageHeader eyebrow="Quality Operations · 质量运营" title="问答记录与反馈管理" description="追踪问答命中、引用质量与用户反馈，持续识别知识缺口和过期内容。">
      <el-button :icon="Refresh" :loading="loading" @click="loadData">刷新数据</el-button>
      <el-button class="primary-btn" :icon="TrendCharts" @click="exportData">导出当前记录</el-button>
    </PageHeader>

    <div class="card-grid metric-grid feedback-metrics">
      <MetricCard label="问答记录" :value="questions.length" unit="条" tone="blue"><el-icon><ChatDotRound /></el-icon></MetricCard>
      <MetricCard label="知识命中率" :value="questions.length?`${Math.round(questions.filter(item=>item.status==='completed').length/questions.length*100)}%`:'—'" unit="当前列表" tone="green"><el-icon><DocumentChecked /></el-icon></MetricCard>
      <MetricCard label="待处理反馈" :value="feedbacks.filter(item=>item.status!=='resolved').length" unit="条" tone="red"><el-icon><Warning /></el-icon></MetricCard>
      <MetricCard label="已解决反馈" :value="feedbacks.filter(item=>item.status==='resolved').length" unit="条" tone="amber"><el-icon><Clock /></el-icon></MetricCard>
    </div>

    <section class="feedback-workspace panel">
      <header class="workspace-tabs">
        <button :class="{ active: activeTab === 'questions' }" @click="activeTab='questions'">
          <el-icon><ChatDotRound /></el-icon>问答记录<span>{{ questions.length }}</span>
        </button>
        <button :class="{ active: activeTab === 'feedbacks' }" @click="activeTab='feedbacks'">
          <el-icon><EditPen /></el-icon>用户反馈<span class="danger-count">{{ feedbacks.length }}</span>
        </button>
        <button :class="{ active: activeTab === 'archives' }" @click="activeTab='archives'">
          <el-icon><DocumentChecked /></el-icon>归档摘要<span>{{ archives.length }}</span>
        </button>
      </header>

      <div class="feedback-filters">
        <el-input v-model="keyword" :prefix-icon="Search" clearable :placeholder="activeTab === 'questions' ? '搜索问题、用户或知识库...' : activeTab === 'feedbacks' ? '搜索反馈内容、问题或知识库...' : '搜索归档摘要或知识库...'" />
        <el-select v-if="activeTab==='questions'" v-model="qaStatus" clearable placeholder="全部回答状态"><el-option label="回答完成" value="completed"/><el-option label="未检索到可靠依据" value="no_answer"/><el-option label="回答失败" value="failed"/></el-select>
        <el-select v-else-if="activeTab==='feedbacks'" v-model="feedbackStatus" clearable placeholder="全部处理状态"><el-option label="待处理" value="pending"/><el-option label="处理中" value="processing"/><el-option label="已解决" value="resolved"/></el-select>
        <el-select v-model="timeRange"><el-option label="全部时间" value=""/><el-option label="今天" value="today"/><el-option label="近 7 天" value="7d"/><el-option label="近 30 天" value="30d"/></el-select>
      </div>

      <div v-if="activeTab==='questions'" class="records-scroll" tabindex="0" aria-label="问答记录表格，可左右拖动底部滑条">
        <el-table class="records-table" :data="visibleQuestions" row-key="question">
          <el-table-column label="问题摘要" min-width="310"><template #default="{row}"><div class="question-cell"><b>{{row.question}}</b><small>{{row.user}}　·　会话：{{row.session}}</small></div></template></el-table-column>
          <el-table-column prop="kb" label="知识库范围" min-width="185" />
          <el-table-column label="回答状态" width="135"><template #default="{row}"><StatusPill :tone="row.tone" :text="row.state"/></template></el-table-column>
          <el-table-column label="引用" width="70" align="center"><template #default="{row}"><b :class="{ faint: !row.refs }">{{row.refs}}</b></template></el-table-column>
          <el-table-column prop="time" label="提问时间" width="120" />
          <el-table-column prop="latency" label="响应耗时" width="90" />
          <el-table-column label="操作" width="140"><template #default="{row}"><el-button class="detail-action" text @click="openDetail(row)">查看详情</el-button></template></el-table-column>
        </el-table>
      </div>

      <div v-else-if="activeTab==='feedbacks'" class="records-scroll" tabindex="0" aria-label="用户反馈表格，可左右拖动底部滑条">
        <el-table class="records-table" :data="visibleFeedbacks" row-key="question">
          <el-table-column label="问题与反馈说明" min-width="360"><template #default="{row}"><div class="question-cell"><b>{{row.question}}</b><small>{{row.message}}</small></div></template></el-table-column>
          <el-table-column label="反馈类型" width="110"><template #default="{row}"><span class="feedback-type">{{row.type}}</span></template></el-table-column>
          <el-table-column prop="kb" label="关联知识库" min-width="180" />
          <el-table-column label="状态" width="105"><template #default="{row}"><StatusPill :tone="row.tone" :text="row.state"/></template></el-table-column>
          <el-table-column prop="owner" label="处理人" width="90" />
          <el-table-column prop="time" label="提交时间" width="115" />
          <el-table-column prop="updated" label="最近处理" width="115" />
          <el-table-column label="操作" width="105"><template #default="{row}"><el-button text @click="openDetail(row)">{{row.state==='已解决'?'查看':'立即处理'}}</el-button></template></el-table-column>
        </el-table>
      </div>

      <div v-else class="records-scroll" tabindex="0" aria-label="问答归档摘要表格，可左右拖动底部滑条">
        <el-table class="records-table archive-table" :data="visibleArchives" row-key="id">
          <el-table-column prop="date" label="归档日期" width="130" />
          <el-table-column prop="knowledgeBaseName" label="知识库" min-width="180" />
          <el-table-column label="问答统计" min-width="210"><template #default="{row}"><div class="archive-counts"><b>{{row.questionCount}} 条</b><small>正常 {{row.answeredCount}} · 未命中 {{row.noAnswerCount}} · 失败 {{row.failedCount}}</small></div></template></el-table-column>
          <el-table-column label="处理结果" min-width="190"><template #default="{row}">已解决反馈 {{row.resolvedFeedbackCount}} 条<br>已补充知识缺口 {{row.supplementedGapCount}} 项</template></el-table-column>
          <el-table-column prop="summary" label="归档摘要" min-width="560" />
        </el-table>
      </div>

      <footer class="workspace-footer"><span>当前显示 {{activeTab==='questions'?visibleQuestions.length:activeTab==='feedbacks'?visibleFeedbacks.length:visibleArchives.length}} 条记录（最多加载最近 100 条）</span><span class="scroll-copy">{{activeTab==='archives'?'原始问答在 72 小时后清理，此处长期保留统计摘要':'左右拖动表格底部滑条查看全部列'}}</span></footer>
    </section>

    <el-dialog v-model="detailOpen" class="feedback-detail-dialog" width="min(680px, calc(100vw - 24px))" align-center append-to-body>
      <template #header><div class="detail-title"><span class="detail-icon"><el-icon><Select /></el-icon></span><div class="detail-copy"><h3>{{activeTab==='questions'?'问答质量详情':'反馈处理详情'}}</h3><p>关联问题、引用来源与处理结论</p></div><div class="detail-status"><StatusPill :tone="selectedRecord?.tone" :text="selectedRecord?.state"/></div></div></template>
      <div v-if="selectedRecord" class="detail-body">
        <section><label>用户问题</label><p class="question-box">{{selectedRecord.question}}</p></section>
        <section><label>关联知识库</label><div class="detail-kb"><span>{{selectedRecord.kb}}</span><small>RAG 混合检索 · Dense + BM25</small></div></section>
          <section class="answer-summary"><label>回答与检索摘要</label><p v-if="activeTab==='questions'">{{detailData?.answer?.content || selectedRecord.answerPreview || '暂无回答正文'}}</p><p v-else>{{detailData?.comment || selectedRecord.message}}</p></section>
        <template v-if="activeTab==='feedbacks'">
          <section><label>用户补充说明</label><p class="feedback-message">{{selectedRecord.message}}</p></section>
          <section><label>处理结论 *</label><el-select v-model="resolution" placeholder="请选择处理结论" style="width:100%"><el-option v-for="item in resolutionOptions" :key="item.value" :label="item.label" :value="item.value" /></el-select></section>
          <section><label>内部处理备注</label><el-input v-model="note" type="textarea" :rows="3" placeholder="记录核验过程、修复动作或需协同的责任部门..." /></section>
          <div class="notify-row"><div><b>向用户发送处理结果通知</b><small>解决后通过站内消息告知用户</small></div><el-switch v-model="notifyUser" /></div>
        </template>
      </div>
      <template #footer><el-button @click="detailOpen=false">关闭</el-button><el-button v-if="activeTab==='feedbacks' && selectedRecord?.status==='pending'" @click="claimFeedback">领取反馈</el-button><el-button v-if="activeTab==='feedbacks' && selectedRecord?.status!=='resolved'" class="primary-btn" @click="submitResolution">保存并标记处理完成</el-button></template>
    </el-dialog>
  </div>
</template>

<style scoped>
.feedback-metrics{margin-bottom:16px}.feedback-metrics :deep(.metric-card){min-height:108px}.feedback-workspace{min-width:0;overflow:hidden}.workspace-tabs{display:flex;gap:4px;padding:13px 17px 0;border-bottom:1px solid #eff4ff}.workspace-tabs button{position:relative;display:flex;align-items:center;gap:7px;padding:11px 15px 14px;border:0;background:transparent;color:#64748b;font-size:11px;font-weight:700;cursor:pointer}.workspace-tabs button:after{content:"";position:absolute;right:10px;bottom:-1px;left:10px;height:2px;border-radius:99px;background:transparent}.workspace-tabs button.active{color:#0f172a}.workspace-tabs button.active:after{background:#0051d5}.workspace-tabs button>span{padding:2px 6px;border-radius:999px;background:#f1f5f9;color:#64748b;font-size:8px}.workspace-tabs .danger-count{background:#fef2f2;color:#dc2626}.feedback-filters{display:grid;grid-template-columns:minmax(260px,1.4fr) repeat(3,minmax(150px,.65fr));gap:8px;padding:14px 17px;background:#fff}.records-scroll{min-width:0;overflow-x:scroll;overflow-y:hidden;padding-bottom:6px;border-top:1px solid #f8fafc;scrollbar-width:auto;scrollbar-color:#64748b #e2e8f0}.records-scroll:focus-visible{outline:2px solid rgba(0,81,213,.3);outline-offset:-2px}.records-scroll::-webkit-scrollbar{height:14px}.records-scroll::-webkit-scrollbar-track{border-radius:999px;background:#e2e8f0}.records-scroll::-webkit-scrollbar-thumb{min-width:80px;border:3px solid #e2e8f0;border-radius:999px;background:#64748b}.records-scroll :deep(.records-table){min-width:1600px}.records-scroll :deep(.detail-action){min-width:72px;padding-inline:10px;white-space:nowrap}.question-cell{display:flex;flex-direction:column;gap:5px}.question-cell b{color:#0f172a;font-size:10px}.question-cell small{max-width:430px;color:#64748b;font-size:8px;line-height:1.45}.feedback-type{display:inline-flex;padding:5px 7px;border-radius:7px;background:#fef2f2;color:#dc2626;font-size:8px;font-weight:700}.workspace-footer{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 17px 14px;color:#64748b;font-size:9px}.scroll-copy{color:#0051d5;font-weight:700;white-space:nowrap}.detail-title{width:100%;min-width:0;display:grid;grid-template-columns:38px minmax(0,1fr);align-items:center;column-gap:10px;row-gap:7px;padding-right:36px}.detail-icon{grid-row:1/3;width:38px;height:38px;display:grid;place-items:center;border-radius:10px;background:#eff4ff;color:#0051d5}.detail-copy{min-width:0}.detail-title h3,.detail-title p{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.detail-title h3{margin:0;font-size:14px}.detail-title p{margin:4px 0 0;color:#94a3b8;font-size:9px}.detail-status{grid-column:2;justify-self:start;min-width:0}.detail-status :deep(.status-pill){max-width:none;overflow:visible;text-overflow:clip}.detail-body{display:grid;gap:14px}.detail-body section>label{display:block;margin-bottom:7px;color:#64748b;font-size:9px;font-weight:700}.question-box,.feedback-message{margin:0;padding:11px;border-radius:9px;background:#f8fafc;color:#334155;font-size:10px;line-height:1.65}.feedback-message{background:#fffbeb;color:#92400e}.detail-kb{display:flex;justify-content:space-between;padding:10px;border-radius:9px;background:#eff4ff;color:#0051d5;font-size:9px}.detail-kb small{color:#64748b}.answer-summary{padding:12px;border:1px solid #eff4ff;border-radius:10px}.answer-summary p{margin:0;color:#64748b;font-size:9px;line-height:1.65}.notify-row{display:flex;align-items:center;justify-content:space-between;padding:11px;border-radius:9px;background:#f8fafc}.notify-row>div{display:flex;flex-direction:column}.notify-row b{font-size:9px}.notify-row small{margin-top:3px;color:#94a3b8;font-size:8px}
.feedback-filters{grid-template-columns:minmax(260px,1.4fr) repeat(2,minmax(150px,.65fr))}
.archive-counts{display:flex;flex-direction:column;gap:4px}.archive-counts b{font-size:11px}.archive-counts small{color:#64748b;font-size:8px}.records-scroll :deep(.archive-table){min-width:1200px}
@media(max-width:900px){.feedback-filters{grid-template-columns:1fr 1fr}}
@media(max-width:620px){.feedback-filters{grid-template-columns:1fr}.workspace-tabs button{flex:1;justify-content:center;padding-inline:8px}.workspace-footer{align-items:flex-start;flex-direction:column;gap:8px}.scroll-copy{white-space:normal}.detail-kb{align-items:flex-start;flex-direction:column;gap:5px}}
</style>
