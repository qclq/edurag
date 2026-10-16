<script setup>
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowRight, Collection, EditPen, WarnTriangleFilled } from '@element-plus/icons-vue'
import QuickKnowledgeBaseDialog from '../components/QuickKnowledgeBaseDialog.vue'
import { authenticatedRequest, useAuth } from '../stores/auth'

const { state } = useAuth()
const router = useRouter()
const question = ref('')
const kb = ref('')
const kbSelect = ref(null)
const categories = ref([])
const hotQuestions = ref([])
const knowledgeBases = ref([])
const quickDocuments = ref([])
const serviceStatus = ref(null)
const loading = ref(false)
const submitting = ref(false)
const createKbOpen = ref(false)
const questionMaxLength = computed(() => state.bootstrap?.questionLimits?.maxLength || 500)
const readyDocumentCount = computed(() => knowledgeBases.value.reduce((sum, item) => sum + (item.readyDocumentCount || 0), 0))
const primaryLabel = computed(() => knowledgeBases.value.length ? '开始检索' : state.user?.canAccessAdmin ? '创建首个知识库' : '暂无可检索知识库')

const loadHome = async () => {
  loading.value = true
  try {
    const data = await authenticatedRequest('/home')
    knowledgeBases.value = data.accessibleKnowledgeBases || []
    hotQuestions.value = data.hotQuestions || []
    quickDocuments.value = data.quickDocuments || []
    serviceStatus.value = data.serviceStatus
    categories.value = (data.categories || []).map((title, index) => ({
      title,
      count: hotQuestions.value.filter(item => item.category === title).length,
      tone: ['blue', 'green', 'amber'][index % 3],
      text: '查看该业务分类下的常见咨询',
    }))
    if (!kb.value && knowledgeBases.value.length) kb.value = knowledgeBases.value[0].id
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
    return
  }
  if (!kb.value) {
    ElMessage.warning('请先选择一个知识库')
    return
  }
  submitting.value = true
  let conversation = null
  try {
    conversation = await authenticatedRequest('/conversations', {
      method: 'POST',
      body: { knowledgeBaseIds: [kb.value], title: content.slice(0, 30) },
    })
    await authenticatedRequest(`/conversations/${conversation.id}/messages`, {
      method: 'POST',
      body: {
        content,
        knowledgeBaseIds: [kb.value],
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

const handleKnowledgeBaseCreated = async (created) => {
  await loadHome()
  kb.value = created.id
  ElMessage.success('知识库已创建，请先上传资料；也可以继续测试问答流程')
}

const chooseHotQuestion = (item) => {
  question.value = item.question
  if (item.knowledgeBaseId && knowledgeBases.value.some(base => base.id === item.knowledgeBaseId)) {
    kb.value = item.knowledgeBaseId
  }
}

const chooseCategory = (item) => {
  const recommendation = hotQuestions.value.find(questionItem => questionItem.category === item.title)
  question.value = recommendation?.question || `请介绍${item.title}相关的最新规定和办理流程`
}

const focusScope = async () => {
  document.querySelector('.ask-panel')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  await nextTick()
  kbSelect.value?.focus?.()
}

onMounted(loadHome)
</script>

<template>
  <div class="page-content home-layout">
    <section class="home-main">
      <div class="welcome-panel panel">
        <div class="welcome-state"><i />认证服务已连接 <span>当前数据来自后端接口</span></div>
        <h1>你好，{{ state.user?.name }}！今天想了解哪些校园规章制度或办事指南？</h1>
        <p>回答优先依据校方知识库，并逐条标注官方文件引用出处与最新修订时间。</p>
        <div class="scope-bar"><el-icon><Collection /></el-icon><span><b>当前生效检索范围</b>{{ knowledgeBases.length ? `${knowledgeBases.length} 个可访问知识库` : '暂无可用知识库' }}</span><el-button text @click="focusScope">调整范围</el-button></div>
      </div>

      <section class="category-section">
        <div class="section-head"><div><span>业务场景快速分类</span><small>{{ loading ? '正在加载…' : '来自当前有效推荐' }}</small></div></div>
        <div v-if="categories.length" class="category-grid">
          <article v-for="item in categories" :key="item.title" class="category-card" tabindex="0" @click="chooseCategory(item)" @keydown.enter="chooseCategory(item)">
            <div :class="['category-icon', `tone-${item.tone}`]"><el-icon><Collection /></el-icon></div>
            <span>{{ item.count }} 个推荐问题</span><h3>{{ item.title }}</h3><p>{{ item.text }}</p>
          </article>
        </div>
        <el-empty v-else description="暂无业务分类数据" />
      </section>

      <section class="hot-section">
        <div class="section-head"><div><span>热门高频咨询</span><small>由后端统计接口提供</small></div></div>
        <button v-for="item in hotQuestions" :key="item.id" @click="chooseHotQuestion(item)"><i />{{ item.question }}<el-icon><ArrowRight /></el-icon></button>
        <el-empty v-if="!hotQuestions.length" description="暂无热门咨询数据" />
      </section>

      <article class="feature-story panel">
        <div class="story-image"><span>使用提示</span></div>
        <div><label>智能检索小贴士</label><h3>问题中可包含年份、所属部门和具体诉求</h3><p>系统会根据你的授权范围检索正式文档，并在回答中展示可追溯的引用来源。</p></div>
      </article>
    </section>

    <aside class="ask-panel panel">
      <div class="ask-title"><span><el-icon><EditPen /></el-icon></span><div><h3>智能问答发起台</h3><p>支持多轮关联与切片定位</p></div><i /></div>
      <label>选择目标知识空间</label>
      <el-select ref="kbSelect" v-model="kb" style="width:100%" placeholder="暂无可用知识库" :disabled="!knowledgeBases.length">
        <el-option v-for="item in knowledgeBases" :key="item.id" :label="item.name" :value="item.id" />
      </el-select>
      <el-alert v-if="!knowledgeBases.length" class="setup-alert" title="尚未创建知识库，检索和文档入库暂不可用。" type="warning" show-icon :closable="false">
        <template #default><el-button v-if="state.user?.canAccessAdmin" link type="primary" @click="createKbOpen = true">立即创建首个知识库</el-button><span v-else>请联系管理员创建并授权知识库。</span></template>
      </el-alert>
      <el-alert v-else-if="readyDocumentCount === 0" class="setup-alert" title="当前知识库还没有已入库文档，回答将没有可引用资料。" type="info" show-icon :closable="false">
        <template #default><el-button v-if="state.user?.canAccessAdmin" link type="primary" @click="router.push('/admin/upload')">前往上传文档</el-button></template>
      </el-alert>
      <label>详细问题描述 <small>{{ question.length }}/{{questionMaxLength}}</small></label>
      <el-input v-model="question" type="textarea" :rows="7" :maxlength="questionMaxLength" placeholder="请描述你想了解的制度、条件或办理事项，包含年份与身份信息会更准确..." @keydown.enter.exact.prevent="submit" />
      <div class="ask-tools"><span>Enter 发送，Shift + Enter 换行</span></div>
      <el-button class="primary-btn ask-submit" :loading="submitting" :disabled="knowledgeBases.length ? !question.trim() : !state.user?.canAccessAdmin" @click="submit">{{ primaryLabel }} <el-icon><ArrowRight /></el-icon></el-button>
      <p class="disclaimer"><el-icon><WarnTriangleFilled /></el-icon>免责声明：AI 生成内容均基于校方上传文档，重要学籍与财务决策请以校内最新正式红头文件及主管办公室通知为准。</p>
      <div class="quality-grid"><div><small>向量检索</small><strong>{{ serviceStatus?.embedding?.configured ? '可用' : '待配置' }}</strong><span>{{ serviceStatus?.embedding?.model || '—' }}</span></div><div><small>回答模型</small><strong>{{ serviceStatus?.answering?.configured ? '可用' : '待配置' }}</strong><span>{{ serviceStatus?.answering?.model || '—' }}</span></div></div>
      <div class="quick-docs"><b>常用规章速查单</b><p v-if="!quickDocuments.length">暂无常用文档数据</p><p v-for="doc in quickDocuments" :key="doc.id" role="button" tabindex="0" @click="router.push({path:'/source',query:{documentId:doc.id}})" @keydown.enter="router.push({path:'/source',query:{documentId:doc.id}})">{{ doc.displayName }} <i>{{ doc.category || '文档' }}</i></p></div>
    </aside>
    <QuickKnowledgeBaseDialog v-model="createKbOpen" @created="handleKnowledgeBaseCreated" />
  </div>
</template>

<style scoped>
.home-layout { display:grid; grid-template-columns:minmax(0,1fr) 330px; gap:20px; }
.home-main { min-width:0; display:flex; flex-direction:column; gap:21px; }
.welcome-panel { padding:25px 28px 20px; background:linear-gradient(120deg,#fff 55%,#f2f7ff); }
.welcome-state { display:flex; align-items:center; gap:7px; color:#059669; font-size:10px; font-weight:700; }
.welcome-state i { width:6px; height:6px; border-radius:50%; background:#10b981; }
.welcome-state span { margin-left:auto; color:#94a3b8; font-weight:500; }
.welcome-panel h1 { max-width:850px; margin:17px 0 9px; font-size:29px; line-height:1.4; letter-spacing:-.025em; }
.welcome-panel > p { margin-bottom:18px; color:#64748b; font-size:13px; }
.scope-bar { display:flex; align-items:center; gap:10px; padding:11px 13px; border-radius:10px; background:#f8fafc; color:#0051d5; }
.scope-bar span { flex:1; display:flex; flex-direction:column; gap:3px; color:#64748b; font-size:11px; }
.scope-bar b { color:#334155; }
.section-head div { display:flex; align-items:baseline; gap:12px; }
.section-head span { font-size:16px; font-weight:700; }
.section-head small { color:#94a3b8; }
.category-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:12px; margin-top:13px; }
.category-card { position:relative; min-height:152px; padding:17px; border-radius:14px; background:#fff; box-shadow:var(--shadow); cursor:pointer; transition:.18s; }
.category-card:hover { transform:translateY(-2px); box-shadow:0 9px 26px rgba(15,23,42,.08); }
.category-icon { width:37px; height:37px; display:grid; place-items:center; border-radius:10px; }
.category-card > span { position:absolute; top:18px; right:17px; color:#94a3b8; font-size:10px; }
.category-card h3 { margin:13px 0 6px; font-size:14px; }
.category-card p { margin:0; color:#64748b; font-size:11px; line-height:1.55; }
.hot-section { padding:0 3px; }
.hot-section .section-head { margin-bottom:10px; }
.hot-section button { width:100%; display:flex; align-items:center; gap:9px; padding:8px 12px; margin-top:7px; border:0; border-radius:999px; background:#fff; color:#334155; text-align:left; font-size:11px; cursor:pointer; }
.hot-section button i { width:5px; height:5px; border-radius:50%; background:#0051d5; }
.hot-section button .el-icon { margin-left:auto; color:#94a3b8; }
.feature-story { display:flex; gap:18px; padding:18px; }
.story-image { width:190px; min-height:110px; display:flex; align-items:flex-end; padding:12px; border-radius:11px; background:linear-gradient(150deg,#c6d7d0,#8c7c5e 58%,#29373f); color:white; }
.story-image span { font-size:11px; font-weight:700; }
.feature-story > div:last-child { flex:1; }
.feature-story label { color:#0051d5; font-size:10px; font-weight:700; }
.feature-story h3 { margin:7px 0; font-size:14px; }
.feature-story p { margin:0; color:#64748b; font-size:11px; line-height:1.65; }
.ask-panel { position:sticky; top:88px; height:max-content; padding:20px; }
.ask-title { display:flex; align-items:center; gap:10px; padding-bottom:15px; margin-bottom:15px; border-bottom:1px solid #eff4ff; }
.ask-title > span { width:36px; height:36px; display:grid; place-items:center; border-radius:10px; background:#06080c; color:white; }
.ask-title h3 { margin:0 0 3px; font-size:14px; }.ask-title p{margin:0;color:#64748b;font-size:10px}.ask-title>i{width:7px;height:7px;margin-left:auto;border-radius:50%;background:#10b981}
.ask-panel > label { display:flex; justify-content:space-between; margin:14px 0 7px; color:#334155; font-size:11px; font-weight:700; }.ask-panel>label small{color:#94a3b8;font-weight:500}
.ask-tools { display:flex; align-items:center; margin-top:6px; }.ask-tools span{margin-left:auto;color:#64748b;font-size:10px}
.ask-submit { width:100%; height:42px; margin-top:7px; }
.setup-alert { margin-top:10px; }
.disclaimer { display:flex; gap:7px; margin:14px 0; padding:10px; border-radius:9px; background:#eff4ff; color:#64748b; font-size:9px; line-height:1.5; }.disclaimer .el-icon{flex:none;color:#0051d5;margin-top:2px}
.quality-grid { display:grid; grid-template-columns:1fr 1fr; gap:8px; }.quality-grid>div{padding:11px;border-radius:9px;background:#f8fafc}.quality-grid small,.quality-grid span{display:block;color:#64748b;font-size:9px}.quality-grid strong{display:block;margin:4px 0;color:#059669;font-size:18px}.quality-grid em{font-size:9px;font-style:normal}
.quick-docs { padding-top:15px; margin-top:15px; border-top:1px solid #eff4ff; }.quick-docs>b{display:flex;justify-content:space-between;font-size:11px}.quick-docs a{color:#0051d5;font-size:9px}.quick-docs p{display:flex;align-items:center;justify-content:space-between;gap:7px;margin:10px 0 0;color:#64748b;font-size:9px}.quick-docs i{font-style:normal;color:#94a3b8;font-size:8px}
@media(max-width:1180px){.home-layout{grid-template-columns:1fr}.ask-panel{position:static}.category-grid{grid-template-columns:repeat(2,1fr)}}
@media(max-width:640px){.category-grid{grid-template-columns:1fr}.welcome-panel h1{font-size:23px}.scope-bar{align-items:flex-start}.feature-story{flex-direction:column}.story-image{width:100%}}
</style>
