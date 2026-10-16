<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Edit, Promotion, Refresh, Star, Top, ZoomIn } from '@element-plus/icons-vue'
import StatusPill from '../components/StatusPill.vue'
import { authenticatedRequest, useAuth } from '../stores/auth'

const router = useRouter()
const route = useRoute()
const { state } = useAuth()
const input = ref('')
const messages = ref([])
const generating = ref(false)
const conversation = ref(null)
const loading = ref(false)
let pollTimer = null
let disposed = false

const terminalStatuses = new Set(['completed', 'no_answer', 'stopped', 'failed'])
const assistantMessages = computed(() => messages.value.filter(message => message.role === 'assistant'))
const latestAssistant = computed(() => assistantMessages.value.at(-1) || null)
const latestCitations = computed(() => latestAssistant.value?.citations || [])
const isExternalUser = computed(() => state.user?.userType === 'external')

const formatTime = (value) => value
  ? new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(new Date(value))
  : ''

const loadConversation = async (id, { silent = false } = {}) => {
  if (!silent) loading.value = true
  try {
    const data = await authenticatedRequest(`/conversations/${id}`)
    conversation.value = data
    messages.value = data.messages || []
    generating.value = messages.value.some(message => message.role === 'assistant' && !terminalStatuses.has(message.status))
    return data
  } finally {
    if (!silent) loading.value = false
  }
}

const schedulePoll = () => {
  window.clearTimeout(pollTimer)
  if (!generating.value || !conversation.value?.id || disposed) return
  pollTimer = window.setTimeout(async () => {
    try {
      await loadConversation(conversation.value.id, { silent: true })
    } catch {
      generating.value = false
    }
    schedulePoll()
  }, 1000)
}

const ensureConversation = async (content) => {
  if (conversation.value) return conversation.value
  const created = await authenticatedRequest('/conversations', {
    method: 'POST',
    body: { title: content.slice(0, 30) },
  })
  conversation.value = { ...created, messages: [] }
  window.history.replaceState({}, '', router.resolve({ path: '/chat', query: { conversationId: created.id } }).href)
  return created
}

const send = async (preset) => {
  const content = (typeof preset === 'string' ? preset : input.value).trim()
  if (!content || generating.value) return
  generating.value = true
  try {
    const current = await ensureConversation(content)
    const result = await authenticatedRequest(`/conversations/${current.id}/messages`, {
      method: 'POST',
      body: {
        content,
        clientRequestId: crypto.randomUUID?.() || `web-${Date.now()}-${Math.random().toString(16).slice(2)}`,
      },
    })
    input.value = ''
    messages.value = [
      ...messages.value.filter(item => ![result.userMessage.id, result.assistantMessage.id].includes(item.id)),
      result.userMessage,
      result.assistantMessage,
    ]
    window.history.replaceState({}, '', router.resolve({ path: '/chat', query: { conversationId: current.id } }).href)
    schedulePoll()
  } catch (error) {
    generating.value = false
    ElMessage.error(error.message || '问题发送失败')
  }
}

const renameConversation = async () => {
  if (!conversation.value) return
  try {
    const { value } = await ElMessageBox.prompt('请输入新的会话标题', '重命名会话', {
      inputValue: conversation.value.title,
      inputPattern: /\S+/,
      inputErrorMessage: '标题不能为空',
    })
    conversation.value = await authenticatedRequest(`/conversations/${conversation.value.id}`, {
      method: 'PATCH', body: { title: value.trim() },
    })
    ElMessage.success('会话标题已更新')
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(error.message || '更新标题失败')
  }
}

const toggleFavorite = async () => {
  if (!conversation.value) return
  try {
    conversation.value = await authenticatedRequest(`/conversations/${conversation.value.id}`, {
      method: 'PATCH', body: { favorite: !conversation.value.favorite },
    })
    ElMessage.success(conversation.value.favorite ? '已收藏会话' : '已取消收藏')
  } catch (error) {
    ElMessage.error(error.message || '收藏操作失败')
  }
}

const clearMessages = async () => {
  if (!conversation.value || !messages.value.length) return
  try {
    await ElMessageBox.confirm('将清空此会话中的全部消息，且无法恢复。', '确认清空记录', { type: 'warning' })
    await authenticatedRequest(`/conversations/${conversation.value.id}/messages?confirm=true`, { method: 'DELETE' })
    messages.value = []
    generating.value = false
    ElMessage.success('会话记录已清空')
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(error.message || '清空失败')
  }
}

const stopGeneration = async () => {
  const message = [...assistantMessages.value].reverse().find(item => !terminalStatuses.has(item.status))
  if (!message) return
  try {
    const updated = await authenticatedRequest(`/messages/${message.id}/stop`, { method: 'POST' })
    messages.value = messages.value.map(item => item.id === updated.id ? updated : item)
    generating.value = false
  } catch (error) {
    ElMessage.error(error.message || '停止生成失败')
  }
}

const regenerate = async (message) => {
  generating.value = true
  try {
    const result = await authenticatedRequest(`/messages/${message.id}/regenerations`, {
      method: 'POST', body: { replaceCurrent: true },
    })
    messages.value.push(result.assistantMessage)
    schedulePoll()
  } catch (error) {
    generating.value = false
    ElMessage.error(error.message || '重新生成失败')
  }
}

const rate = async (message, value) => {
  try {
    await authenticatedRequest(`/messages/${message.id}/rating`, { method: 'PUT', body: { value } })
    ElMessage.success(value === 'helpful' ? '感谢你的反馈' : '已记录问题，管理员可在反馈中心跟进')
  } catch (error) {
    ElMessage.error(error.message || '评价提交失败')
  }
}

onMounted(async () => {
  try {
    const id = typeof route.query.conversationId === 'string' ? route.query.conversationId : null
    if (id) await loadConversation(id)
    const initialQuestion = typeof route.query.question === 'string' ? route.query.question : ''
    if (initialQuestion) await send(initialQuestion)
    else if (generating.value) schedulePoll()
  } catch (error) {
    ElMessage.error(error.message || '问答页面初始化失败')
  }
})

onBeforeUnmount(() => {
  disposed = true
  window.clearTimeout(pollTimer)
})
</script>

<template>
  <div class="page-content chat-page">
    <header class="chat-header panel">
      <div><div class="chat-title-row"><h1>{{ conversation?.title || '新对话' }}</h1><el-button v-if="conversation && !isExternalUser" text circle :icon="Edit" aria-label="重命名" @click="renameConversation" /></div><p>自动检索范围：<span v-for="item in conversation?.knowledgeBases || []" :key="item.id">{{ item.name }}</span><span v-if="!conversation">发送后自动检索全部授权知识库</span></p></div>
      <div><el-button v-if="!isExternalUser" :icon="Star" :disabled="!conversation" @click="toggleFavorite">{{ conversation?.favorite ? '取消收藏' : '收藏会话' }}</el-button><el-button v-if="!isExternalUser" :icon="Delete" :disabled="!messages.length" @click="clearMessages">清空记录</el-button><el-button class="primary-btn" :icon="Top" @click="router.push('/home')">返回问答首页</el-button></div>
    </header>

    <div class="chat-layout">
      <section class="message-stream">
        <el-empty v-if="!messages.length && !loading" description="还没有消息，在下方输入问题开始问答" />
        <template v-for="message in messages" :key="message.id">
          <article v-if="message.role === 'user'" class="user-message">
            <p>{{ message.content }}</p><small>{{ formatTime(message.createdAt) }}</small>
          </article>
          <article v-else class="answer-card panel">
            <header><span class="ai-avatar">AI</span><div><b>校园智库助手</b><small>{{ message.modelName || message.statusLabel }} · {{ formatTime(message.createdAt) }}</small></div><StatusPill :tone="message.status === 'failed' ? 'danger' : message.status === 'completed' ? 'success' : 'info'" :text="message.statusLabel" /></header>
            <p v-if="message.content" class="answer-intro">{{ message.content }}</p>
            <p v-else-if="message.error" class="answer-intro">{{ message.error.message }}</p>
            <div v-else class="retrieval-card"><div><span class="rag-icon"><el-icon><ZoomIn /></el-icon></span><p><b>正在拆解问题、检索资料并生成回答</b><small>页面会自动更新，无需手动刷新</small></p></div></div>
            <template v-if="message.citations?.length">
              <h3 class="sources-title">引用来源</h3>
              <div class="source-list"><button v-for="citation in message.citations" :key="citation.id" @click="router.push({path:'/source',query:{citationId:citation.id}})"><span>{{ citation.index }}</span><p><b>{{ citation.documentName || `引用 ${citation.index}` }}</b><small>{{ citation.section || '文档切片' }} · {{ citation.pageStart ? `第 ${citation.pageStart} 页` : '页码未标注' }}</small></p><el-icon><Top /></el-icon></button></div>
            </template>
            <footer class="answer-actions">
              <el-button v-if="terminalStatuses.has(message.status)" text @click="rate(message, 'helpful')">有帮助</el-button>
              <el-button v-if="terminalStatuses.has(message.status)" text @click="rate(message, 'unhelpful')">需改进</el-button>
              <el-button v-if="terminalStatuses.has(message.status)" text :icon="Refresh" @click="regenerate(message)">重新生成</el-button>
            </footer>
          </article>
        </template>
      </section>

      <aside class="diagnostic panel">
        <div class="diagnostic-title"><span><el-icon><ZoomIn /></el-icon></span><div><b>召回诊断与切片</b><small>实时检索质量分析</small></div></div>
        <div v-if="latestAssistant" class="scores"><div><small>检索耗时</small><strong>{{ latestAssistant.timing?.retrievalMs ?? '—' }}<em> ms</em></strong></div><div><small>总耗时</small><strong>{{ latestAssistant.timing?.totalMs ?? '—' }}<em> ms</em></strong></div><div><small>召回引用</small><strong>{{ latestCitations.length }}<em> 条</em></strong></div></div>
        <article v-for="citation in latestCitations" :key="citation.id" class="chunk" @click="router.push({path:'/source',query:{citationId:citation.id}})"><header><span>{{ citation.index }}</span><div><b>{{ citation.documentName || '引用来源' }}</b><small>{{ citation.section || '未标注章节' }}</small></div></header><p>{{ citation.quote }}</p><footer><span>相关度 {{ citation.similarityScore ? `${Math.round(citation.similarityScore * 100)}%` : '—' }}</span><a>查看来源</a></footer></article>
        <el-empty v-if="!latestAssistant" description="发送问题后显示检索诊断" />
      </aside>
    </div>

    <div class="composer panel"><div class="composer-status"><span><i />自动检索全部授权资料</span><small>{{ generating ? '正在生成回答…' : 'Enter 发送，Shift + Enter 换行' }}</small></div><div class="composer-row"><el-input v-model="input" type="textarea" :autosize="{minRows:1,maxRows:4}" :maxlength="state.bootstrap?.questionLimits?.maxLength || 500" placeholder="请输入问题" :disabled="generating" @keydown.enter.exact.prevent="send()" /><el-button v-if="generating" type="danger" plain circle aria-label="停止生成" @click="stopGeneration">■</el-button><el-button v-else class="primary-btn send-btn" circle :icon="Promotion" :disabled="!input.trim()" @click="send()" /></div><small class="composer-note">回答模型会先拆解问题，再从你有权访问的全部资料中检索并整理答案。</small></div>
  </div>
</template>

<style scoped>
.chat-page { padding-bottom:150px; }.chat-header{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:18px 20px;margin-bottom:18px}.chat-title-row{display:flex;align-items:center;gap:5px}.chat-header h1{margin:0;font-size:18px}.chat-header p{margin:6px 0 0;color:#64748b;font-size:10px}.chat-header p span{display:inline-flex;padding:3px 7px;margin:0 4px;border-radius:5px;background:#eff4ff;color:#0051d5}.chat-header>div:last-child{display:flex;gap:7px;flex:none}
.chat-layout{display:grid;grid-template-columns:minmax(0,1fr) 320px;gap:18px;align-items:start}.message-stream{display:grid;gap:14px}.day-label{text-align:center;color:#94a3b8;font-size:10px;margin:12px 0 17px}.user-message{max-width:76%;margin-left:auto;padding:15px 17px;border-radius:15px 15px 4px 15px;background:#2a2e35;color:white;box-shadow:var(--shadow)}.user-message p{margin:0;font-size:13px;line-height:1.6;white-space:pre-wrap}.user-message small{display:block;margin-top:8px;color:#cbd5e1;font-size:9px;text-align:right}
.retrieval-card{display:flex;align-items:center;justify-content:space-between;margin:13px 0;padding:12px 14px}.retrieval-card>div{display:flex;align-items:center;gap:10px}.rag-icon{width:34px;height:34px;display:grid;place-items:center;border-radius:10px;background:#ecfdf5;color:#059669}.retrieval-card p{margin:0}.retrieval-card b,.retrieval-card small{display:block}.retrieval-card b{font-size:11px}.retrieval-card small{margin-top:3px;color:#64748b;font-size:9px}
.answer-card{padding:21px}.answer-card>header{display:flex;align-items:center;gap:10px;padding-bottom:16px;border-bottom:1px solid #eff4ff}.ai-avatar{width:38px;height:38px;display:grid;place-items:center;border-radius:11px;background:#06080c;color:#fff}.answer-card>header>div{flex:1;display:flex;flex-direction:column}.answer-card>header b{font-size:12px}.answer-card>header small{margin-top:3px;color:#94a3b8;font-size:9px}.answer-intro{margin:18px 0;color:#334155;font-size:12px;line-height:1.75;white-space:pre-wrap}.answer-section{display:flex;gap:12px;margin-top:18px}.section-number{width:25px;height:25px;flex:none;display:grid;place-items:center;border-radius:7px;background:#0051d5;color:#fff;font-size:11px;font-weight:800}.answer-section>div:last-child{min-width:0;flex:1}.answer-section h2{margin:2px 0 10px;font-size:15px}.answer-section ul{padding-left:17px;margin:0}.answer-section li{margin:8px 0;color:#475569;font-size:11px;line-height:1.7}.answer-section mark{padding:1px 4px;border-radius:4px;background:#dbeafe;color:#0051d5;font-weight:700}.answer-section sup{color:#0051d5;font-weight:700}.materials-table{overflow-x:auto;border:1px solid #eff4ff;border-radius:10px}.materials-table>div{min-width:650px;display:grid;grid-template-columns:1.25fr 1fr 1.5fr .65fr}.materials-table span{padding:9px 10px;border-bottom:1px solid #eff4ff;color:#475569;font-size:9px}.materials-table .thead{background:#f8fafc}.materials-table .thead span{color:#64748b;font-weight:700}.deadline-note{display:flex;gap:10px;margin-top:19px;padding:12px;border-radius:9px;background:#fffbeb;color:#d97706}.deadline-note p{margin:0;color:#92400e;font-size:10px;line-height:1.55}.sources-title{margin-top:18px;font-size:11px}.source-list{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin-top:10px}.source-list button{display:flex;align-items:center;gap:9px;padding:10px;border:1px solid #eff4ff;border-radius:10px;background:#fff;text-align:left;cursor:pointer}.source-list button>span{width:24px;height:24px;display:grid;place-items:center;border-radius:6px;background:#eff4ff;color:#0051d5;font-size:9px}.source-list p{flex:1;min-width:0;margin:0}.source-list b,.source-list small{display:block}.source-list b{font-size:9px}.source-list small{margin-top:4px;color:#94a3b8;font-size:8px}.source-list .el-icon{color:#94a3b8}.answer-actions{display:flex;align-items:center;gap:3px;padding-top:14px;margin-top:16px;border-top:1px solid #eff4ff}.source-button{margin-left:auto}.follow-ups{display:grid;gap:7px;padding:17px 4px}.follow-ups>b{margin-bottom:2px;color:#64748b;font-size:10px}.follow-ups button{width:max-content;max-width:100%;display:flex;align-items:center;gap:7px;padding:8px 11px;border:0;border-radius:999px;background:#fff;color:#475569;font-size:10px;cursor:pointer}
.diagnostic{position:sticky;top:88px;padding:18px}.diagnostic-title{display:flex;align-items:center;gap:9px;padding-bottom:13px;border-bottom:1px solid #eff4ff}.diagnostic-title>span{width:32px;height:32px;display:grid;place-items:center;border-radius:9px;background:#eff4ff;color:#0051d5}.diagnostic-title>div{flex:1;display:flex;flex-direction:column}.diagnostic-title b{font-size:12px}.diagnostic-title small{color:#94a3b8;font-size:9px}.scores{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:12px 0}.scores>div{padding:10px;border-radius:9px;background:#f8fafc}.scores>div:last-child{grid-column:1/-1}.scores small{display:block;color:#94a3b8;font-size:8px}.scores strong{display:block;margin-top:4px;color:#0051d5;font-size:18px}.scores em{color:#64748b;font-size:9px;font-style:normal;font-weight:500}.chunk{padding:12px;margin-top:9px;border:1px solid #eff4ff;border-radius:11px;cursor:pointer}.chunk.active{border-color:#bfdbfe;background:#f8fbff}.chunk header{display:flex;gap:8px}.chunk header>span{width:23px;height:23px;display:grid;place-items:center;border-radius:6px;background:#0051d5;color:#fff;font-size:8px}.chunk header div{display:flex;flex-direction:column}.chunk header b{font-size:9px}.chunk header small{color:#94a3b8;font-size:8px}.chunk>p{color:#64748b;font-size:9px;line-height:1.65}.chunk footer{display:flex;justify-content:space-between;color:#94a3b8;font-size:8px}.chunk a{color:#0051d5;cursor:pointer}.diagnostic h4{margin:16px 0 8px;font-size:10px}.strategy{display:flex;align-items:center;gap:7px;margin:8px 0;color:#475569;font-size:9px}.strategy .el-icon{color:#059669}
.composer{position:fixed;z-index:30;right:32px;bottom:15px;left:288px;padding:12px 15px}.composer-status{display:flex;justify-content:space-between;color:#64748b;font-size:9px}.composer-status span{display:flex;align-items:center;gap:6px;color:#059669}.composer-status i{width:6px;height:6px;border-radius:50%;background:#10b981}.composer-row{display:flex;align-items:flex-end;gap:7px;margin-top:7px}.composer-row .el-textarea{flex:1}.send-btn{flex:none}.composer-note{display:block;margin-top:5px;color:#94a3b8;font-size:8px;text-align:center}
@media(max-width:1180px){.chat-layout{grid-template-columns:1fr}.diagnostic{position:static}.composer{left:274px}.chat-header{align-items:flex-start;flex-direction:column}.chat-header>div:last-child{flex-wrap:wrap}}
@media(max-width:1100px){.composer{left:18px;right:18px}.source-list{grid-template-columns:1fr}}
@media(max-width:640px){.chat-page{padding-bottom:175px}.user-message{max-width:92%}.answer-card{padding:15px}.answer-actions{flex-wrap:wrap}.source-button{width:100%;margin-left:0}.composer{bottom:8px}.chat-header h1{font-size:16px}.chat-header>div:last-child .el-button:not(.primary-btn){display:none}}
</style>
