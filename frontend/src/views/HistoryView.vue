<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Download, Edit, FolderOpened, MoreFilled, Plus, Search, Star, View } from '@element-plus/icons-vue'
import PageHeader from '../components/PageHeader.vue'
import StatusPill from '../components/StatusPill.vue'
import { authenticatedRequest } from '../stores/auth'

const router = useRouter()
const query = ref('')
const range = ref('全部')
const kbId = ref('')
const deleteOpen = ref(false)
const sessions = ref([])
const knowledgeBases = ref([])
const selectedSession = ref(null)
const loading = ref(false)
const page = ref(1)
const total = ref(0)
const pageSize = 20
let searchTimer = null
const filtered = computed(() => sessions.value.filter(s => !query.value || `${s.title}${s.text}`.includes(query.value)))
const groups = computed(() => [...new Set(filtered.value.map(s => s.group))])

const rangeValues = { 全部: '', 今天: 'today', 最近7天: '7d', 本月: '30d' }
const formatDate = (value) => value
  ? new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(new Date(value))
  : '暂无消息'
const dateGroup = (value) => {
  if (!value) return '较早'
  const date = new Date(value)
  const now = new Date()
  if (date.toDateString() === now.toDateString()) return '今天'
  const yesterday = new Date(now); yesterday.setDate(now.getDate() - 1)
  if (date.toDateString() === yesterday.toDateString()) return '昨天'
  return date.getFullYear() === now.getFullYear() ? `${date.getMonth() + 1}月` : `${date.getFullYear()}年`
}

const normalizeSession = (item) => ({
  ...item,
  group: dateGroup(item.lastMessageAt || item.createdAt),
  kb: item.knowledgeBases?.length > 1 ? `${item.knowledgeBases.length} 个知识库` : item.knowledgeBases?.[0]?.name || '未绑定',
  text: item.knowledgeBases?.map(base => base.name).join('、') || '暂无知识库信息',
  count: `${item.messageCount || 0} 条消息`,
  source: `${item.knowledgeBases?.length || 0} 个知识库`,
  time: formatDate(item.lastMessageAt || item.createdAt),
  active: item.favorite,
})

const loadSessions = async () => {
  loading.value = true
  const params = new URLSearchParams({ page: String(page.value), pageSize: String(pageSize) })
  if (query.value.trim()) params.set('q', query.value.trim())
  if (kbId.value) params.set('kbId', kbId.value)
  if (rangeValues[range.value]) params.set('timeRange', rangeValues[range.value])
  try {
    const envelope = await authenticatedRequest(`/conversations?${params}`, { includeEnvelope: true })
    sessions.value = (envelope.data || []).map(normalizeSession)
    total.value = envelope.pagination?.total || 0
  } catch (error) {
    ElMessage.error(error.message || '会话历史加载失败')
  } finally {
    loading.value = false
  }
}

const openSession = (session) => router.push({ path: '/chat', query: { conversationId: session.id } })

const toggleFavorite = async (session) => {
  try {
    const updated = await authenticatedRequest(`/conversations/${session.id}`, {
      method: 'PATCH', body: { favorite: !session.favorite },
    })
    Object.assign(session, normalizeSession(updated))
  } catch (error) {
    ElMessage.error(error.message || '收藏操作失败')
  }
}

const rename = async (session) => {
  try {
    const { value } = await ElMessageBox.prompt('请输入新的会话标题', '重命名会话', { inputValue: session.title, inputPattern: /\S+/, inputErrorMessage: '标题不能为空' })
    const updated = await authenticatedRequest(`/conversations/${session.id}`, { method: 'PATCH', body: { title: value.trim() } })
    Object.assign(session, normalizeSession(updated))
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(error.message || '重命名失败')
  }
}

const confirmDelete = (session) => {
  selectedSession.value = session
  deleteOpen.value = true
}

const removeSession = async () => {
  if (!selectedSession.value) return
  try {
    await authenticatedRequest(`/conversations/${selectedSession.value.id}`, { method: 'DELETE' })
    deleteOpen.value = false
    selectedSession.value = null
    ElMessage.success('会话已删除')
    await loadSessions()
  } catch (error) {
    ElMessage.error(error.message || '删除失败')
  }
}

const exportSessions = () => {
  if (!sessions.value.length) return ElMessage.warning('当前没有可导出的会话')
  const escape = value => `"${String(value ?? '').replaceAll('"', '""')}"`
  const csv = ['标题,知识库,消息数,最近时间', ...sessions.value.map(item => [item.title, item.text, item.messageCount, item.time].map(escape).join(','))].join('\r\n')
  const url = URL.createObjectURL(new Blob([`\uFEFF${csv}`], { type: 'text/csv;charset=utf-8' }))
  const link = document.createElement('a'); link.href = url; link.download = `问答历史-${new Date().toISOString().slice(0, 10)}.csv`; link.click()
  URL.revokeObjectURL(url)
}

watch([query, range, kbId], () => {
  page.value = 1
  window.clearTimeout(searchTimer)
  searchTimer = window.setTimeout(loadSessions, 300)
})
watch(page, loadSessions)
onMounted(async () => {
  try { knowledgeBases.value = await authenticatedRequest('/knowledge-bases/options') } catch { knowledgeBases.value = [] }
  await loadSessions()
})
onBeforeUnmount(() => window.clearTimeout(searchTimer))
</script>

<template>
  <div class="page-content history-page">
    <PageHeader eyebrow="Session Archive · 问答归档" title="我的问答历史记录" description="溯源检索校园规章、奖助申请与日常事务的历史对话，所有引用切片与问答证据链均已沉淀归档。">
      <el-button :icon="Download" @click="exportSessions">导出当前列表</el-button>
      <el-button class="primary-btn" :icon="Plus" @click="router.push('/home')">发起新提问</el-button>
    </PageHeader>

    <div class="history-grid">
      <section class="history-main">
        <div class="history-filter panel">
          <el-input v-model="query" :prefix-icon="Search" clearable placeholder="搜索会话标题、问答内容或引用规范..." />
          <el-select v-model="kbId" placeholder="全部知识库" clearable style="width:180px"><template #prefix><el-icon><FolderOpened /></el-icon></template><el-option v-for="item in knowledgeBases" :key="item.id" :label="item.name" :value="item.id" /></el-select>
          <el-segmented v-model="range" :options="['全部','今天','最近7天','本月']" />
        </div>

        <el-empty v-if="!filtered.length && !loading" description="没有找到匹配的历史会话"><el-button @click="query=''">清除搜索条件</el-button></el-empty>
        <section v-for="group in groups" :key="group" class="session-group">
          <header><span /><b>{{ group }}</b><small>{{ filtered.filter(s=>s.group===group).length }} 场会话</small></header>
          <article v-for="session in filtered.filter(s=>s.group===group)" :key="session.id" :class="['session-card', { active:session.favorite }]" @click="openSession(session)">
            <div class="session-top"><StatusPill :tone="session.favorite ? 'info' : 'success'" :text="session.kb" /><h3>{{ session.title }}</h3><div class="session-actions" @click.stop><el-button text circle :icon="session.favorite ? Star : MoreFilled" :title="session.favorite ? '取消收藏' : '收藏'" @click="toggleFavorite(session)" /><el-button text circle :icon="Edit" title="重命名" @click="rename(session)" /><el-button text circle :icon="Delete" title="删除" @click="confirmDelete(session)" /></div></div>
            <p>{{ session.text }}</p>
            <footer><span>{{ session.count }}</span><span>{{ session.source }}</span><time>{{ session.time }}</time></footer>
          </article>
        </section>
        <div class="history-pager"><span>共 {{ total }} 条记录</span><el-pagination v-model:current-page="page" small background layout="prev, pager, next" :page-size="pageSize" :total="total" /></div>
      </section>

      <aside class="history-insight panel">
        <div class="insight-head"><div><b>会话切片洞察</b><span>当前选中会话</span></div></div>
        <el-empty description="点击左侧会话可查看完整问答与引用" /><el-button class="full-btn" @click="router.push('/home')">发起新提问</el-button>
      </aside>
    </div>

    <el-dialog v-model="deleteOpen" width="430" align-center append-to-body>
      <template #header><div class="delete-title"><span><el-icon><Delete /></el-icon></span><div><b>确认删除此会话记录？</b><small>此操作无法撤销</small></div></div></template>
      <p class="delete-copy">删除后将无法恢复该会话及其关联记录。</p>
      <div class="delete-warning"><b>不可逆风险提示</b><p>请确认后再执行删除操作。</p></div>
      <template #footer><el-button @click="deleteOpen=false">取消</el-button><el-button type="danger" @click="removeSession">确认永久删除</el-button></template>
    </el-dialog>
  </div>
</template>

<style scoped>
.history-grid { display:grid; grid-template-columns:minmax(0,1fr) minmax(280px,330px); gap:19px; align-items:start; }
.history-main { min-width:0; }
.history-filter { min-width:0; display:grid; grid-template-columns:minmax(0,1fr) minmax(150px,180px) auto; gap:10px; padding:13px; margin-bottom:22px; }
.history-filter .el-segmented { min-width:0; }
.session-group { margin-bottom:23px; }.session-group>header{display:flex;align-items:center;gap:9px;margin:0 4px 10px}.session-group>header>span{width:7px;height:7px;border-radius:50%;background:#0051d5}.session-group>header b{font-size:12px}.session-group>header small{color:#94a3b8;margin-left:4px}
.session-card { padding:18px 19px; margin-top:10px; border-radius:16px; background:#fff; box-shadow:var(--shadow); cursor:pointer; transition:.18s; }.session-card:hover{transform:translateY(-1px);box-shadow:0 8px 24px rgba(15,23,42,.08)}.session-card.active{box-shadow:0 6px 24px rgba(0,81,213,.12);outline:2px solid rgba(0,81,213,.16)}
.session-top { display:flex; align-items:center; gap:9px; }.session-top h3{flex:1;min-width:0;margin:0;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.session-actions{display:flex;flex:none}.session-card>p{margin:12px 0;color:#64748b;font-size:11px;line-height:1.65;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}.session-card footer{display:flex;gap:22px;padding-top:11px;border-top:1px solid #eff4ff;color:#64748b;font-size:10px}.session-card footer time{margin-left:auto}
.history-pager { display:flex; align-items:center; justify-content:space-between; padding:15px 3px; color:#64748b; font-size:10px; }
.history-insight { position:sticky; top:88px; padding:20px; }.insight-head{display:flex;justify-content:space-between;align-items:center;padding-bottom:14px;border-bottom:1px solid #eff4ff}.insight-head>div{display:flex;flex-direction:column;gap:3px}.insight-head b{font-size:14px}.insight-head span{color:#94a3b8;font-size:9px}.history-insight dl{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin:14px 0}.history-insight dl>div{padding:10px;border-radius:9px;background:#f8fafc}.history-insight dt{color:#94a3b8;font-size:9px}.history-insight dd{margin:5px 0 0;font-size:11px;font-weight:700}.history-insight dd small{font-size:8px;color:#94a3b8}.green{color:#059669}.history-insight h4{margin:18px 0 10px;font-size:11px}.evidence{display:flex;gap:9px;padding:10px;margin-top:8px;border-radius:9px;background:#f8fafc}.evidence>span{height:max-content;padding:3px 5px;border-radius:4px;color:#dc2626;background:#fef2f2;font-size:8px;font-weight:800}.evidence p{margin:0;min-width:0}.evidence b,.evidence small{display:block}.evidence b{font-size:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.evidence small{margin-top:4px;color:#64748b;font-size:9px}.full-btn{width:100%;margin-top:14px}.distribution p{display:flex;justify-content:space-between;margin:8px 0 5px;color:#64748b;font-size:10px}.distribution div{height:5px;border-radius:99px;background:#eff4ff;overflow:hidden}.distribution i{display:block;height:100%;border-radius:99px;background:#0051d5}.delete-title{display:flex;align-items:center;gap:11px}.delete-title>span{width:36px;height:36px;display:grid;place-items:center;border-radius:10px;background:#fef2f2;color:#dc2626}.delete-title>div{display:flex;flex-direction:column}.delete-title small{color:#94a3b8;font-size:10px}.delete-copy{color:#475569;font-size:12px}.delete-warning{padding:12px;border-radius:9px;background:#fef2f2;color:#dc2626;font-size:11px}.delete-warning p{margin:5px 0 0;color:#7f1d1d;line-height:1.6}
@media(max-width:1400px){.history-filter{grid-template-columns:minmax(0,1fr) minmax(150px,180px)}.history-filter .el-segmented{grid-column:1/-1}}
@media(max-width:1180px){.history-grid{grid-template-columns:1fr}.history-insight{position:static}}
@media(max-width:640px){.history-filter{grid-template-columns:1fr}.history-filter .el-select{width:100%!important}.session-top{align-items:flex-start;flex-wrap:wrap}.session-top h3{order:2;flex-basis:80%}.session-actions{margin-left:auto}.session-card footer{flex-wrap:wrap}.session-card footer time{margin-left:0}.history-pager{align-items:flex-start;gap:10px;flex-direction:column}}
</style>
