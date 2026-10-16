<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Collection, DataAnalysis, Document, Download, Refresh, User, Warning } from '@element-plus/icons-vue'
import PageHeader from '../components/PageHeader.vue'
import MetricCard from '../components/MetricCard.vue'
import StatusPill from '../components/StatusPill.vue'
import { authenticatedRequest } from '../stores/auth'

const range = ref('近7天')
const dashboard = ref({ metrics: {}, trend: [], satisfaction: {}, taskStatus: {} })
const gaps = ref([])
const anomalies = ref([])
const knowledgeBaseCount = ref(0)
const pendingFeedbackCount = ref(0)
const loading = ref(false)
const rangeMap = { 今日: 'today', 近7天: '7d', 近30天: '30d' }
const maxTrend = computed(() => Math.max(1, ...dashboard.value.trend.map(item => item.questions || 0)))

const loadDashboard = async () => {
  loading.value = true
  const key = rangeMap[range.value]
  const results = await Promise.allSettled([
    authenticatedRequest(`/admin/dashboard?range=${key}`),
    authenticatedRequest('/admin/knowledge-bases?page=1&pageSize=1', { includeEnvelope: true }),
    authenticatedRequest('/admin/feedbacks?status=pending&page=1&pageSize=1', { includeEnvelope: true }),
    authenticatedRequest(`/admin/knowledge-gaps?range=${key}&pageSize=5`, { includeEnvelope: true }),
    authenticatedRequest('/admin/anomalies?pageSize=5', { includeEnvelope: true }),
  ])
  if (results[0].status === 'fulfilled') dashboard.value = results[0].value
  if (results[1].status === 'fulfilled') knowledgeBaseCount.value = results[1].value.pagination?.total || 0
  if (results[2].status === 'fulfilled') pendingFeedbackCount.value = results[2].value.pagination?.total || 0
  if (results[3].status === 'fulfilled') gaps.value = results[3].value.data || []
  if (results[4].status === 'fulfilled') anomalies.value = results[4].value.data || []
  if (results[0].status === 'rejected') ElMessage.error(results[0].reason?.message || '概览数据加载失败')
  loading.value = false
}

const exportReport = () => {
  const metrics = dashboard.value.metrics || {}
  const rows = [['指标', '数值'], ['知识库总数', knowledgeBaseCount.value], ['文档总数', metrics.documentCount || 0], ['问答总量', metrics.questionCount || 0], ['回答率', `${Math.round((metrics.answerRate || 0) * 100)}%`], ['满意度', `${Math.round((metrics.satisfactionRate || 0) * 100)}%`], ['待处理反馈', pendingFeedbackCount.value], ['失败任务', metrics.failedTaskCount || 0]]
  const csv = rows.map(row => row.map(value => `"${String(value).replaceAll('"', '""')}"`).join(',')).join('\r\n')
  const url = URL.createObjectURL(new Blob([`\uFEFF${csv}`], { type: 'text/csv;charset=utf-8' }))
  const link = document.createElement('a'); link.href = url; link.download = `管理概览-${range.value}.csv`; link.click(); URL.revokeObjectURL(url)
}

const markGapSupplemented = async (item) => {
  try {
    await ElMessageBox.confirm(
      `确认“${item.knowledgeBaseName}”中这个知识缺口已经完成补充吗？`,
      '标记为已补充',
      { confirmButtonText: '确认已补充', cancelButtonText: '取消', type: 'success' },
    )
    await authenticatedRequest(`/admin/knowledge-gaps/${item.id}/supplement`, { method:'POST', body:{ note:null } })
    ElMessage.success('已标记为已补充；该记录将在 72 小时后归入摘要')
    await loadDashboard()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(error.message || '更新预警状态失败')
  }
}

watch(range, loadDashboard)
onMounted(loadDashboard)
</script>

<template>
  <div class="page-content overview-page">
    <PageHeader eyebrow="Ops & Intelligence Center" title="管理监控概览" description="系统知识库运行态势、问答质量与异常任务的实时汇总。">
      <el-segmented v-model="range" :options="['今日','近7天','近30天']" />
      <el-button :icon="Refresh" :loading="loading" @click="loadDashboard">刷新</el-button>
      <el-button class="primary-btn" :icon="Download" @click="exportReport">导出分析报告</el-button>
    </PageHeader>

    <div class="card-grid metric-grid">
      <MetricCard label="知识库总数" :value="knowledgeBaseCount" unit="个" tone="blue"><el-icon><Collection /></el-icon></MetricCard>
      <MetricCard label="已入库文档" :value="dashboard.metrics.documentCount || 0" unit="篇" tone="blue"><el-icon><Document /></el-icon></MetricCard>
      <MetricCard :label="`${range}问答总量`" :value="dashboard.metrics.questionCount || 0" :unit="`回答率 ${Math.round((dashboard.metrics.answerRate || 0) * 100)}%`" tone="green"><el-icon><DataAnalysis /></el-icon></MetricCard>
      <MetricCard label="待处理用户反馈" :value="pendingFeedbackCount" :unit="`满意度 ${Math.round((dashboard.metrics.satisfactionRate || 0) * 100)}%`" tone="amber"><el-icon><User /></el-icon></MetricCard>
    </div>

    <div class="overview-main-grid">
      <section class="panel panel-pad trend-panel">
        <div class="panel-title"><div><h2>问答服务走势与综合满意度</h2><p>24小时请求频率与检索时延波动监控</p></div></div>
        <div v-if="dashboard.trend.length" class="trend-list"><div v-for="item in dashboard.trend" :key="item.date"><span>{{ item.date }}</span><div><i :style="{width:`${Math.max(4, item.questions / maxTrend * 100)}%`}" /></div><b>{{ item.questions }} 次</b><small>完成 {{ item.answered }} · 无答案 {{ item.noAnswer }}</small></div></div><el-empty v-else description="当前时段暂无问答数据" />
      </section>

      <section class="panel panel-pad category-panel">
        <div class="panel-title"><div><h2>回答满意度分布</h2><p>来自用户对回答的主动评价</p></div><small>{{ range }}</small></div>
        <div class="satisfaction-list"><p><span>有帮助</span><b>{{ dashboard.satisfaction.helpful || 0 }}</b></p><p><span>需改进</span><b>{{ dashboard.satisfaction.unhelpful || 0 }}</b></p><p><span>未评价</span><b>{{ dashboard.satisfaction.unrated || 0 }}</b></p></div>
      </section>

      <section class="panel panel-pad gap-panel">
        <div class="panel-title"><div><h2>高频未命中问题预警（知识缺口）</h2><p>由真实问答统计接口生成</p></div></div>
        <article v-for="(item,index) in gaps" :key="item.id" class="gap-item"><span class="gap-index">{{ index + 1 }}</span><div><b>{{ item.question }}</b><p>{{ item.knowledgeBaseName }} · 累计未命中 <em>{{ item.count }}</em> 次 · 最近 {{ new Date(item.lastOccurredAt).toLocaleString('zh-CN') }}</p></div><div class="gap-actions"><StatusPill :tone="item.status==='supplemented'?'success':'warning'" :text="item.statusLabel" /><el-button v-if="item.status!=='supplemented'" size="small" type="success" plain @click="markGapSupplemented(item)">标记已补充</el-button></div></article><el-empty v-if="!gaps.length" description="当前时段暂无知识缺口" />
      </section>

      <section class="panel panel-pad task-panel">
        <div class="panel-title"><div><h2>异步处理异常任务</h2><p>入库管道中需要管理员审核或处理中的任务</p></div></div>
        <article v-for="item in anomalies" :key="item.id" class="task danger"><span><el-icon><Warning /></el-icon></span><div><header><b>{{ item.title }}</b><em>需要关注</em></header><p>{{ item.message }}</p><p class="task-action">建议：{{ item.suggestedAction }}</p><small>{{ item.knowledgeBaseName }} · {{ item.createdAt ? new Date(item.createdAt).toLocaleString('zh-CN') : '' }} · 参考编号 {{ item.referenceNo }}</small></div></article><el-empty v-if="!anomalies.length" description="当前没有需要关注的异常任务" />
      </section>
    </div>
  </div>
</template>

<style scoped>
.overview-main-grid{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(320px,.75fr);gap:16px;margin-top:16px}.trend-panel{position:relative;min-height:420px}.chart-legend{display:flex;gap:20px;color:#64748b;font-size:9px}.chart-legend span{display:flex;align-items:center;gap:6px}.chart-legend i{width:18px;border-top:2px solid #0051d5}.chart-legend .dash{border-top:2px dashed #10b981}.line-chart{width:100%;height:245px;margin-top:6px}.grid-lines line{stroke:#eef2f7;stroke-width:1}.chart-tooltip{position:absolute;left:53%;top:115px;padding:7px 9px;border-radius:7px;background:#111827;color:#fff;box-shadow:0 4px 12px rgba(0,0,0,.2)}.chart-tooltip:after{content:"";position:absolute;left:50%;bottom:-5px;border:5px solid transparent;border-top-color:#111827;border-bottom:0}.chart-tooltip b,.chart-tooltip span{display:block;font-size:8px}.chart-tooltip span{margin-top:2px;color:#cbd5e1}.chart-axis{display:flex;justify-content:space-between;color:#94a3b8;font-size:8px}.chart-footer{display:flex;justify-content:space-between;align-items:center;padding:10px;margin-top:13px;border-radius:9px;background:#f8fafc;color:#64748b;font-size:8px}.chart-footer>span{display:flex;align-items:center;gap:6px}.chart-footer .el-icon,.chart-footer a{color:#0051d5}.chart-footer b{color:#334155}.category-panel>small{color:#94a3b8;font-size:8px}.donut-row{display:flex;align-items:center;gap:22px;padding:12px 0}.donut{width:145px;height:145px;flex:none;display:grid;place-items:center;border-radius:50%;background:conic-gradient(#0051d5 0 45%,#5d7be8 45% 67%,#93a9d7 67% 85%,#cbd5e1 85%);position:relative}.donut:after{content:"";position:absolute;width:88px;height:88px;border-radius:50%;background:#fff}.donut>span{z-index:1;display:flex;flex-direction:column;align-items:center;color:#64748b;font-size:9px}.donut b{color:#0f172a;font-size:21px}.donut-legend{flex:1}.donut-legend>div{display:grid;grid-template-columns:8px 1fr auto;align-items:center;gap:7px;margin:10px 0}.donut-legend i{width:7px;height:7px;border-radius:50%}.donut-legend p{margin:0}.donut-legend p b,.donut-legend p small{display:block}.donut-legend p b{font-size:9px}.donut-legend small{color:#94a3b8;font-size:7px}.donut-legend>div>span{font-size:9px;font-weight:700}.category-note{padding:10px;border-radius:9px;background:#f8fafc;color:#64748b;font-size:9px}.gap-item{display:flex;align-items:center;gap:11px;padding:12px;margin-top:9px;border-radius:11px;background:#f8fafc}.gap-index{width:25px;height:25px;flex:none;display:grid;place-items:center;border-radius:7px;background:#fff;color:#dc2626;font-size:10px;font-weight:800}.gap-item>div{min-width:0;flex:1}.gap-item>.gap-actions{display:flex;flex:0 0 auto;align-items:center;gap:7px}.gap-actions :deep(.status-pill){flex:none}.gap-item b{font-size:10px}.gap-item p{margin:4px 0 0;color:#64748b;font-size:8px}.gap-item em{color:#dc2626;font-style:normal}.view-all{display:block;margin-top:13px;text-align:right;color:#0051d5;font-size:9px}.task{display:flex;align-items:flex-start;gap:10px;padding:12px;margin-top:9px;border-radius:11px}.task.danger{background:#fef2f2}.task.warning{background:#fffbeb}.task>span{width:29px;height:29px;display:grid;place-items:center;border-radius:8px;background:#fff;color:#dc2626}.task>div{min-width:0;flex:1}.task header{display:flex;justify-content:space-between;gap:8px}.task b{font-size:10px}.task em{flex:none;color:#dc2626;font-size:8px;font-style:normal}.task p{margin:5px 0;color:#475569;font-size:8px;line-height:1.55}.task .task-action{padding:6px 8px;border-radius:7px;background:#fff;color:#7f1d1d}.task small{display:block;color:#94a3b8;font-size:8px;line-height:1.5;overflow-wrap:anywhere}.task-panel .el-progress{margin:6px 0}.pipeline-ok{display:flex;justify-content:space-between;align-items:center;margin-top:14px}.pipeline-ok a{color:#0051d5;font-size:8px}
.trend-list{display:grid;gap:10px;margin-top:16px}.trend-list>div{display:grid;grid-template-columns:78px minmax(90px,1fr) 48px 110px;align-items:center;gap:9px;font-size:9px}.trend-list>div>span,.trend-list small{color:#64748b}.trend-list>div>div{height:8px;border-radius:99px;background:#eff4ff;overflow:hidden}.trend-list i{display:block;height:100%;border-radius:99px;background:linear-gradient(90deg,#0051d5,#5d7be8)}.satisfaction-list{display:grid;gap:9px;margin-top:15px}.satisfaction-list p{display:flex;justify-content:space-between;margin:0;padding:12px;border-radius:9px;background:#f8fafc;color:#64748b;font-size:10px}.satisfaction-list b{color:#0f172a}
@media(max-width:1180px){.overview-main-grid{grid-template-columns:1fr}.donut-row{justify-content:center}.gap-panel{order:3}}
@media(max-width:640px){.donut-row{align-items:flex-start;flex-direction:column}.chart-tooltip{display:none}.chart-axis span:nth-child(even){display:none}.gap-item{align-items:flex-start;flex-wrap:wrap}.gap-actions{width:100%;margin-left:36px}.gap-item>.el-button{display:none}.task>.el-button{display:none}}
</style>
