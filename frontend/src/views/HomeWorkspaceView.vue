<script setup>
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  ArrowRight, ArrowUp, ChatDotRound, Close, Collection, Document,
  MagicStick, UploadFilled, WarnTriangleFilled,
} from '@element-plus/icons-vue'
import QuickKnowledgeBaseDialog from '../components/QuickKnowledgeBaseDialog.vue'
import { authenticatedRequest, useAuth } from '../stores/auth'

const { state } = useAuth()
const router = useRouter()
const question = ref('')
const questionInput = ref(null)
const categories = ref([])
const hotQuestions = ref([])
const knowledgeBases = ref([])
const quickDocuments = ref([])
const serviceStatus = ref(null)
const loading = ref(false)
const submitting = ref(false)
const createKbOpen = ref(false)
const suggestionsOpen = ref(false)
const isExternalUser = computed(() => state.user?.userType === 'external')

const questionMaxLength = computed(() => state.bootstrap?.questionLimits?.maxLength || 500)
const readyDocumentCount = computed(() => knowledgeBases.value.reduce((sum, item) => sum + (item.readyDocumentCount || 0), 0))
const primaryLabel = computed(() => knowledgeBases.value.length ? '发送问题' : state.user?.canAccessAdmin ? '创建首个知识库' : '暂无可检索知识库')
const canSubmit = computed(() => {
  if (submitting.value) return false
  if (!knowledgeBases.value.length) return Boolean(state.user?.canAccessAdmin)
  return Boolean(question.value.trim())
})
const visibleQuestions = computed(() => hotQuestions.value.slice(0, 6))
const visibleCategories = computed(() => categories.value.slice(0, 5))
const retrievalReady = computed(() => Boolean(serviceStatus.value?.embedding?.configured))
const answeringReady = computed(() => Boolean(serviceStatus.value?.answering?.configured))

const loadHome = async () => {
  loading.value = true
  try {
    const data = await authenticatedRequest('/home')
    knowledgeBases.value = data.accessibleKnowledgeBases || []
    hotQuestions.value = data.hotQuestions || []
    quickDocuments.value = data.quickDocuments || []
    serviceStatus.value = data.serviceStatus
    categories.value = (data.categories || []).map(title => ({
      title,
      count: hotQuestions.value.filter(item => item.category === title).length,
    }))
  } catch (error) {
    ElMessage.error(error.message || '首页数据加载失败')
  } finally {
    loading.value = false
  }
}

const submit = async () => {
  if (!knowledgeBases.value.length) {
    if (state.user?.canAccessAdmin) createKbOpen.value = true
    else ElMessage.warning('当前账号没有可检索的知识库，请联系管理员授权')
    return
  }
  const content = question.value.trim()
  if (!content) {
    ElMessage.warning('请输入要咨询的问题')
    questionInput.value?.focus?.()
    return
  }
  submitting.value = true
  let conversation = null
  try {
    conversation = await authenticatedRequest('/conversations', {
      method: 'POST',
      body: { title: content.slice(0, 30) },
    })
    await authenticatedRequest(`/conversations/${conversation.id}/messages`, {
      method: 'POST',
      body: {
        content,
        clientRequestId: crypto.randomUUID?.() || `home-${Date.now()}-${Math.random().toString(16).slice(2)}`,
      },
    })
    question.value = ''
    await router.push({ path: '/chat', query: { conversationId: conversation.id } })
  } catch (error) {
    if (conversation?.id) {
      try { await authenticatedRequest(`/conversations/${conversation.id}`, { method: 'DELETE' }) } catch { /* Best-effort cleanup for an empty conversation. */ }
    }
    ElMessage.error(error.message || '检索请求提交失败')
  } finally {
    submitting.value = false
  }
}

const handleComposerKeydown = (event) => {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return
  event.preventDefault()
  submit()
}

const handleKnowledgeBaseCreated = async (created) => {
  await loadHome()
  ElMessage.success('知识库已创建，请上传资料后开始提问')
}

const focusQuestion = async () => {
  suggestionsOpen.value = false
  await nextTick()
  questionInput.value?.focus?.()
}

const chooseHotQuestion = async (item) => {
  question.value = item.question
  await focusQuestion()
}

const chooseCategory = async (item) => {
  const recommendation = hotQuestions.value.find(questionItem => questionItem.category === item.title)
  question.value = recommendation?.question || `请介绍${item.title}相关的最新规定和办理流程`
  await focusQuestion()
}

onMounted(loadHome)
</script>

<template>
  <div class="page-content home-workspace">
    <header class="workspace-header">
      <div class="workspace-title">
        <span class="workspace-mark"><el-icon><ChatDotRound /></el-icon></span>
        <div><b>新对话</b><small>基于校园知识库回答</small></div>
      </div>
      <el-button v-if="!isExternalUser" text @click="router.push('/history')">查看历史会话 <el-icon><ArrowRight /></el-icon></el-button>
    </header>

    <main class="conversation-stage">
      <section class="empty-conversation">
        <span class="assistant-orb"><el-icon><MagicStick /></el-icon></span>
        <h1>你好，{{ state.user?.name }}</h1>
        <p>今天想从校园知识库中了解什么？</p>
      </section>

      <div class="composer-area">
        <Transition name="recommendation">
          <section v-if="suggestionsOpen" class="recommendation-popover" aria-label="推荐内容">
            <header>
              <div><span><el-icon><MagicStick /></el-icon></span><p><b>从推荐内容开始</b><small>选择一个问题，或直接在下方输入</small></p></div>
              <button type="button" aria-label="关闭推荐内容" title="关闭" @click="suggestionsOpen = false"><el-icon><Close /></el-icon></button>
            </header>

            <div v-if="!knowledgeBases.length" class="setup-callout warning">
              <el-icon><WarnTriangleFilled /></el-icon>
              <p><b>还没有可检索的知识库</b><small>{{ state.user?.canAccessAdmin ? '先创建知识库并上传资料，即可开始问答。' : '请联系管理员创建并授权知识库。' }}</small></p>
              <el-button v-if="state.user?.canAccessAdmin" size="small" @click="createKbOpen = true">创建知识库</el-button>
            </div>
            <div v-else-if="readyDocumentCount === 0" class="setup-callout">
              <el-icon><Document /></el-icon>
              <p><b>当前知识库尚无已入库文档</b><small>上传资料后，回答才能提供准确引用。</small></p>
              <el-button v-if="state.user?.canAccessAdmin" size="small" @click="router.push('/admin/upload')">上传资料</el-button>
            </div>

            <div v-if="visibleCategories.length" class="category-chips">
              <button v-for="item in visibleCategories" :key="item.title" type="button" @click="chooseCategory(item)">
                {{ item.title }}<span v-if="item.count">{{ item.count }}</span>
              </button>
            </div>

            <div v-if="visibleQuestions.length" class="prompt-grid">
              <button v-for="item in visibleQuestions" :key="item.id" type="button" @click="chooseHotQuestion(item)">
                <span>{{ item.question }}</span><el-icon><ArrowRight /></el-icon>
              </button>
            </div>
            <div v-else-if="!loading" class="recommendation-empty">暂无推荐问题，直接输入你想了解的内容即可。</div>

            <div v-if="quickDocuments.length" class="quick-document-row">
              <span>常用资料</span>
              <button v-for="doc in quickDocuments.slice(0, 3)" :key="doc.id" type="button" @click="router.push({ path: '/source', query: { documentId: doc.id } })">
                <el-icon><Document /></el-icon>{{ doc.displayName }}
              </button>
            </div>
          </section>
        </Transition>

        <section class="ask-composer" :class="{ submitting }">
          <div class="composer-meta">
            <span class="auto-scope"><el-icon><Collection /></el-icon>自动检索全部授权资料</span>
            <button type="button" class="suggestion-toggle" :class="{ active: suggestionsOpen }" @click="suggestionsOpen = !suggestionsOpen">
              <el-icon><MagicStick /></el-icon><span>推荐</span>
            </button>
          </div>

          <el-input
            ref="questionInput"
            v-model="question"
            class="composer-input"
            type="textarea"
            resize="none"
            :autosize="{ minRows: 3, maxRows: 7 }"
            :maxlength="questionMaxLength"
            placeholder="询问校园制度、办事流程或资料内容…"
            @keydown="handleComposerKeydown"
          />

          <footer>
            <div class="composer-actions">
              <button v-if="state.user?.canAccessAdmin" type="button" aria-label="上传资料" title="上传资料" @click="router.push('/admin/upload')"><el-icon><UploadFilled /></el-icon></button>
              <span>{{ question.length }}/{{ questionMaxLength }}</span>
              <span class="keyboard-hint">Shift + Enter 换行</span>
            </div>
            <button class="send-button" type="button" :disabled="!canSubmit" :aria-label="primaryLabel" :title="primaryLabel" @click="submit">
              <el-icon v-if="!submitting"><ArrowUp /></el-icon><span v-else class="send-spinner" />
            </button>
          </footer>
        </section>

        <div class="workspace-status">
          <span><i :class="{ ready: retrievalReady }" />向量检索 {{ retrievalReady ? '已就绪' : '待配置' }}</span>
          <span><i :class="{ ready: answeringReady }" />回答模型 {{ answeringReady ? '已就绪' : '待配置' }}</span>
          <small>AI 回答可能有误，重要事项请核对引用原文</small>
        </div>
      </div>
    </main>

    <QuickKnowledgeBaseDialog v-model="createKbOpen" @created="handleKnowledgeBaseCreated" />
  </div>
</template>

<style scoped>
.home-workspace {
  min-height:calc(100vh - 120px); display:flex; flex-direction:column; overflow:hidden;
  border:1px solid rgba(226,232,240,.72); border-radius:22px;
  background:radial-gradient(circle at 50% 42%,rgba(219,234,254,.34),transparent 32%),#fff;
  box-shadow:0 18px 55px rgba(15,23,42,.055);
}
.workspace-header { height:65px; flex:none; display:flex; align-items:center; justify-content:space-between; padding:0 23px; border-bottom:1px solid #edf1f6; }
.workspace-title { display:flex; align-items:center; gap:11px; }.workspace-title>div{display:flex;flex-direction:column;gap:2px}.workspace-title b{font-size:13px}.workspace-title small{color:var(--faint);font-size:9px}
.workspace-mark { width:32px;height:32px;display:grid;place-items:center;border-radius:10px;background:#111318;color:#fff;box-shadow:0 5px 13px rgba(15,23,42,.16) }
.workspace-header .el-button { color:var(--muted);font-size:11px }.workspace-header .el-button .el-icon{margin-left:5px}
.conversation-stage { flex:1; min-height:600px; display:flex; flex-direction:column; align-items:center; padding:42px 24px 25px; }
.empty-conversation { margin:auto 0 48px; text-align:center; transition:.2s ease; }
.assistant-orb { width:52px;height:52px;display:grid;place-items:center;margin:0 auto 20px;border-radius:17px;background:#111318;color:#fff;font-size:21px;box-shadow:0 12px 30px rgba(15,23,42,.16) }
.empty-conversation h1 { margin:0;font-size:30px;letter-spacing:-.035em;line-height:1.25 }.empty-conversation p{margin:9px 0 0;color:var(--muted);font-size:14px}
.composer-area { position:relative; z-index:3; width:100%; margin-top:auto; }
.recommendation-popover { position:absolute; z-index:5; right:0; bottom:calc(100% + 13px); left:0; max-height:min(430px,calc(100vh - 390px)); padding:15px; overflow:auto; border:1px solid #e6ebf2; border-radius:18px; background:rgba(255,255,255,.97); box-shadow:0 24px 70px rgba(15,23,42,.15),0 2px 8px rgba(15,23,42,.05); backdrop-filter:blur(20px); }
.recommendation-popover>header { display:flex;align-items:center;justify-content:space-between;margin-bottom:12px }.recommendation-popover>header>div{display:flex;align-items:center;gap:10px}.recommendation-popover>header>div>span{width:30px;height:30px;display:grid;place-items:center;border-radius:9px;background:#eef4ff;color:var(--blue)}.recommendation-popover header p{display:flex;flex-direction:column;gap:2px;margin:0}.recommendation-popover header b{font-size:11px}.recommendation-popover header small{color:var(--faint);font-size:9px}.recommendation-popover>header>button{width:29px;height:29px;display:grid;place-items:center;border:0;border-radius:8px;background:transparent;color:var(--muted);cursor:pointer}.recommendation-popover>header>button:hover{background:#f1f5f9;color:var(--text)}
.setup-callout { display:flex;align-items:center;gap:10px;padding:10px 11px;margin-bottom:11px;border-radius:11px;background:#f0f7ff;color:var(--blue) }.setup-callout.warning{background:var(--amber-bg);color:var(--amber)}.setup-callout>p{flex:1;display:flex;flex-direction:column;gap:2px;margin:0}.setup-callout b{font-size:10px}.setup-callout small{color:var(--muted);font-size:9px}
.category-chips { display:flex;gap:7px;margin-bottom:10px;overflow-x:auto;scrollbar-width:none }.category-chips::-webkit-scrollbar{display:none}.category-chips button{display:flex;align-items:center;gap:6px;padding:6px 10px;border:1px solid #e6ebf2;border-radius:999px;background:#fff;color:#475569;font-size:9px;white-space:nowrap;cursor:pointer}.category-chips button:hover{border-color:#b9c7db;background:#f8fafc;color:var(--text)}.category-chips span{padding:1px 5px;border-radius:999px;background:#eff4ff;color:var(--blue);font-size:8px}
.prompt-grid { display:grid;grid-template-columns:1fr 1fr;gap:7px }.prompt-grid button{min-height:47px;display:flex;align-items:center;gap:12px;padding:10px 12px;border:1px solid #edf1f6;border-radius:11px;background:#fafbfd;color:#334155;text-align:left;cursor:pointer;transition:.16s ease}.prompt-grid button:hover{border-color:#c8d3e2;background:#fff;box-shadow:0 5px 15px rgba(15,23,42,.06);transform:translateY(-1px)}.prompt-grid button span{flex:1;font-size:10px;line-height:1.55}.prompt-grid .el-icon{flex:none;color:var(--faint)}
.recommendation-empty { padding:19px;border-radius:11px;background:#f8fafc;color:var(--faint);font-size:10px;text-align:center }
.quick-document-row { display:flex;align-items:center;gap:7px;margin-top:11px;padding-top:11px;border-top:1px solid #edf1f6;overflow-x:auto;scrollbar-width:none }.quick-document-row>span{flex:none;color:var(--faint);font-size:9px}.quick-document-row button{max-width:190px;display:flex;align-items:center;gap:5px;padding:5px 8px;border:0;border-radius:7px;background:#f1f5f9;color:#475569;font-size:8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;cursor:pointer}.quick-document-row button:hover{color:var(--blue);background:#eff4ff}
.ask-composer { padding:11px 13px 10px;border:1px solid #dfe5ed;border-radius:20px;background:#fff;box-shadow:0 18px 45px rgba(15,23,42,.12),0 2px 7px rgba(15,23,42,.04);transition:.18s ease }.ask-composer:focus-within{border-color:#aab8ca;box-shadow:0 22px 55px rgba(15,23,42,.14),0 0 0 3px rgba(0,81,213,.045)}.ask-composer.submitting{opacity:.78}
.composer-meta { display:flex;align-items:center;gap:7px }.auto-scope{display:inline-flex;align-items:center;gap:6px;min-height:28px;padding:4px 9px;border-radius:8px;background:#f4f6f8;color:#475569;font-size:9px;font-weight:650}.auto-scope .el-icon{color:var(--blue)}
.suggestion-toggle { display:flex;align-items:center;gap:5px;padding:6px 8px;border:0;border-radius:8px;background:transparent;color:var(--muted);font-size:9px;cursor:pointer }.suggestion-toggle:hover,.suggestion-toggle.active{background:#eff4ff;color:var(--blue)}
.composer-input { margin-top:4px }.composer-input :deep(.el-textarea__inner){min-height:74px!important;padding:9px 5px!important;border:0!important;background:#fff!important;box-shadow:none!important;color:var(--text);font-size:14px;line-height:1.65}.composer-input :deep(.el-textarea__inner::placeholder){color:#9aa5b4}
.ask-composer>footer { display:flex;align-items:center;justify-content:space-between;padding:3px 1px 0 }.composer-actions{display:flex;align-items:center;gap:8px;color:var(--faint);font-size:8px}.composer-actions>button{width:29px;height:29px;display:grid;place-items:center;border:0;border-radius:9px;background:#f4f6f8;color:#475569;cursor:pointer}.composer-actions>button:hover{background:#e9eef5;color:var(--text)}
.send-button { width:34px;height:34px;display:grid;place-items:center;border:0;border-radius:11px;background:#111318;color:#fff;font-size:17px;cursor:pointer;box-shadow:0 5px 12px rgba(15,23,42,.18);transition:.15s ease }.send-button:hover:not(:disabled){transform:translateY(-1px);background:#252931}.send-button:disabled{background:#e5e7eb;color:#a8b0bb;box-shadow:none;cursor:not-allowed}.send-spinner{width:13px;height:13px;border:2px solid rgba(255,255,255,.45);border-top-color:#fff;border-radius:50%;animation:spin .75s linear infinite}
.workspace-status { display:flex;align-items:center;justify-content:center;gap:14px;margin-top:11px;color:var(--faint);font-size:8px }.workspace-status span{display:flex;align-items:center;gap:5px}.workspace-status i{width:5px;height:5px;border-radius:50%;background:#cbd5e1}.workspace-status i.ready{background:#10b981}.workspace-status small{padding-left:14px;border-left:1px solid #e2e8f0;font-size:8px}
.recommendation-enter-active,.recommendation-leave-active{transition:opacity .18s ease,transform .2s ease}.recommendation-enter-from,.recommendation-leave-to{opacity:0;transform:translateY(8px) scale(.99)}
@keyframes spin{to{transform:rotate(360deg)}}
@media(max-width:960px){.home-workspace{min-height:calc(100vh - 108px)}.conversation-stage{min-height:590px;padding-inline:18px}.recommendation-popover{max-height:360px}}
@media(max-width:640px){.home-workspace{min-height:calc(100vh - 108px);border-radius:16px}.workspace-header{height:57px;padding:0 14px}.workspace-header .el-button{font-size:0}.workspace-header .el-button .el-icon{font-size:15px}.conversation-stage{min-height:560px;padding:28px 12px 17px}.empty-conversation{margin-bottom:35px}.assistant-orb{width:46px;height:46px;margin-bottom:16px}.empty-conversation h1{font-size:25px}.empty-conversation p{font-size:12px}.recommendation-popover{bottom:calc(100% + 9px);max-height:360px;padding:12px;border-radius:15px}.prompt-grid{grid-template-columns:1fr}.quick-document-row{display:none}.ask-composer{border-radius:17px}.auto-scope{max-width:220px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.keyboard-hint{display:none}.workspace-status{gap:8px;flex-wrap:wrap}.workspace-status small{width:100%;padding:0;border:0;text-align:center}}
</style>
