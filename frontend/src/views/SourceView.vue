<script setup>
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowLeft, Close, Download, Warning } from '@element-plus/icons-vue'
import StatusPill from '../components/StatusPill.vue'
import { authenticatedRawRequest, authenticatedRequest } from '../stores/auth'

const router = useRouter()
const route = useRoute()
const loading = ref(false)
const citation = ref(null)
const documentData = ref(null)
const chunks = ref([])
const context = ref(null)
const permissions = ref({ canPreview: false, canDownload: false })
const previousCitationId = ref(null)
const nextCitationId = ref(null)

const loadSource = async () => {
  const citationId = typeof route.query.citationId === 'string' ? route.query.citationId : null
  const documentId = typeof route.query.documentId === 'string' ? route.query.documentId : null
  if (!citationId && !documentId) return
  loading.value = true
  try {
    if (citationId) {
      const data = await authenticatedRequest(`/citations/${citationId}`)
      citation.value = data.citation
      documentData.value = data.document
      chunks.value = data.chunk ? [data.chunk] : []
      context.value = data.context
      permissions.value = data.permissions
      previousCitationId.value = data.previousCitationId
      nextCitationId.value = data.nextCitationId
    } else {
      const data = await authenticatedRequest(`/documents/${documentId}/preview`)
      documentData.value = data.document
      chunks.value = data.chunks || []
      permissions.value = { canPreview: true, canDownload: false }
    }
  } catch (error) {
    ElMessage.error(error.message || '来源详情加载失败')
  } finally {
    loading.value = false
  }
}

const download = async () => {
  if (!documentData.value) return
  try {
    const response = await authenticatedRawRequest(citation.value?.downloadUrl || `/documents/${documentData.value.id}/download`)
    const blobUrl = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = blobUrl
    link.download = documentData.value.displayName || 'document'
    link.click()
    URL.revokeObjectURL(blobUrl)
  } catch (error) {
    ElMessage.error(error.message || '文档下载失败')
  }
}

const report = async (type) => {
  if (!citation.value) return
  try {
    const { value } = await ElMessageBox.prompt('可补充具体问题，便于管理员核验（可留空）', '报告引用问题', { inputType: 'textarea', inputPlaceholder: '例如：该条款已于近期更新' })
    await authenticatedRequest(`/citations/${citation.value.id}/reports`, { method: 'POST', body: { type, comment: value || null } })
    ElMessage.success('问题已提交，感谢反馈')
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(error.message || '反馈提交失败')
  }
}

const goCitation = (id) => id && router.push({ path: '/source', query: { citationId: id } })

onMounted(loadSource)
</script>

<template>
  <div class="source-page">
    <section class="answer-preview">
      <div class="trace"><span>会话追踪 · 引用溯源</span><small>{{ citation ? `引用 ${citation.index}` : '文档预览' }}</small></div>
      <article v-if="citation" class="source-answer panel"><header><span>{{ citation.index }}</span><div><b>回答引用片段</b><small>已定位到原始文档上下文</small></div></header><p>{{ citation.quote }}</p><div v-if="context?.before" class="answer-tip">上文：{{ context.before }}</div><div v-if="context?.after" class="answer-tip">下文：{{ context.after }}</div><footer><span>相关度 {{ citation.similarityScore ? `${Math.round(citation.similarityScore * 100)}%` : '—' }}</span><span>{{ citation.section || '未标注章节' }}</span></footer></article>
      <article v-else class="source-answer panel"><el-empty :description="loading ? '正在加载来源…' : '从回答引用入口进入可查看对应上下文'" /></article>
    </section>

    <aside class="source-drawer">
      <header class="drawer-head"><div><h3>引用来源详情</h3><p>文档位置、上下文与授权信息</p></div><el-button text circle :icon="ArrowLeft" @click="router.back()" /><el-button text circle :icon="Close" @click="router.push('/home')" /></header>
      <div class="drawer-scroll">
        <template v-if="documentData">
          <div class="access-bar"><StatusPill tone="success" text="有权预览" /><small>{{ permissions.canDownload ? '允许下载原件' : '当前权限仅允许在线预览' }}</small><el-button v-if="permissions.canDownload" :icon="Download" @click="download">下载原件</el-button></div>
          <section class="doc-meta panel"><div class="doc-meta-title"><span>DOC</span><div><h2>{{ documentData.displayName }}</h2><p>{{ documentData.documentNo || '无发文编号' }} · {{ documentData.knowledgeBase?.name }}</p></div><StatusPill :text="documentData.statusLabel" /></div><div class="meta-grid"><div><small>业务分类</small><b>{{ documentData.category || '未分类' }}</b></div><div><small>生效日期</small><b>{{ documentData.effectiveAt || '未标注' }}</b></div><div><small>切片数量</small><b>{{ documentData.chunkCount }}</b></div><div><small>最近更新</small><b>{{ documentData.updatedAt ? new Date(documentData.updatedAt).toLocaleDateString('zh-CN') : '—' }}</b></div></div><p class="authority-line">当前内容来自你有权访问的知识库，所有操作均受权限策略约束。</p></section>
          <section class="document-reader panel"><header><div><b>原文定位</b><span>{{ citation?.section || `${chunks.length} 个可预览切片` }}</span></div><div><el-button text :disabled="!previousCitationId" @click="goCitation(previousCitationId)">上一条</el-button><el-button text :disabled="!nextCitationId" @click="goCitation(nextCitationId)">下一条</el-button></div></header><article v-for="chunk in chunks" :key="chunk.id" class="paper"><code>#{{ chunk.chunkIndex + 1 }} · {{ chunk.section || '正文' }} · {{ chunk.pageStart ? `第 ${chunk.pageStart} 页` : '页码未标注' }}</code><h4 v-if="chunk.title">{{ chunk.title }}</h4><p :class="{ highlight: citation?.chunkId === chunk.id }">{{ chunk.content }}<label v-if="citation?.chunkId === chunk.id">当前引用</label></p></article><el-empty v-if="!chunks.length" description="该位置暂无可预览文本" /></section>
          <div v-if="citation" class="drawer-actions"><el-button :icon="Warning" @click="report('outdated')">内容可能过期</el-button><el-button :icon="Warning" @click="report('incorrect')">引用可能不准确</el-button></div>
        </template>
        <el-empty v-else :description="loading ? '正在加载来源…' : '未指定有效的引用或文档'" />
      </div>
    </aside>
  </div>
</template>

<style scoped>
.source-page{display:grid;grid-template-columns:minmax(360px,.78fr) minmax(520px,1.22fr);gap:18px;max-width:1500px;margin:0 auto}.answer-preview{min-width:0}.trace{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:18px;color:#64748b;font-size:10px}.trace>span{font-weight:700;color:#334155}.trace>small{margin-right:auto}.source-user{max-width:84%;margin-left:auto;padding:14px 16px;border-radius:15px 15px 4px 15px;background:#2a2e35;color:white;font-size:12px;line-height:1.65}.source-user small{display:block;margin-top:7px;text-align:right;color:#cbd5e1}.source-answer{margin-top:14px;padding:19px}.source-answer header{display:flex;gap:9px;align-items:center}.source-answer header>span{width:36px;height:36px;display:grid;place-items:center;border-radius:10px;background:#06080c;color:white;font-weight:800}.source-answer header>div{display:flex;flex-direction:column}.source-answer header small{margin-top:3px;color:#059669;font-size:9px}.source-answer>p{margin:15px 0 10px;color:#475569;font-size:11px;line-height:1.7}.source-answer ol{padding-left:29px}.source-answer li{position:relative;padding:7px 9px;margin:5px 0}.source-answer li b,.source-answer li span{display:block}.source-answer li b{font-size:11px}.source-answer li span{margin-top:5px;color:#64748b;font-size:10px;line-height:1.6}.source-answer li.cited{border-radius:9px;background:#eff4ff}.source-answer li em{position:absolute;top:7px;right:8px;color:#0051d5;font-size:8px;font-style:normal}.answer-tip{padding:11px;border-radius:9px;background:#fffbeb;color:#92400e;font-size:9px;line-height:1.6}.source-answer footer{display:flex;align-items:center;justify-content:space-between;margin-top:13px;color:#94a3b8;font-size:8px}.trust-card{display:flex;align-items:center;gap:10px;margin-top:13px;padding:13px;border-radius:13px;background:#fff}.trust-card>span{width:34px;height:34px;display:grid;place-items:center;border-radius:9px;background:#ecfdf5;color:#059669}.trust-card>div{flex:1}.trust-card b{font-size:10px}.trust-card p{margin:3px 0 0;color:#64748b;font-size:8px}.trust-card code{color:#059669;font-size:8px}
.source-drawer{height:calc(100vh - 96px);min-height:650px;display:flex;flex-direction:column;border-radius:18px;background:#fff;box-shadow:0 16px 40px rgba(15,23,42,.12);overflow:hidden}.drawer-head{height:64px;flex:none;display:flex;align-items:center;gap:4px;padding:0 18px;border-bottom:1px solid #eff4ff}.drawer-head>div{flex:1}.drawer-head h3{margin:0;font-size:14px}.drawer-head p{margin:3px 0 0;color:#94a3b8;font-size:9px}.drawer-head>span{margin-right:5px;color:#64748b;font-size:10px}.drawer-scroll{overflow:auto;padding:16px}.access-bar{display:flex;align-items:center;gap:9px;margin-bottom:13px}.access-bar small{flex:1;color:#64748b;font-size:8px}.doc-meta{padding:15px}.doc-meta-title{display:flex;align-items:flex-start;gap:10px}.doc-meta-title>span{padding:7px;border-radius:7px;background:#fef2f2;color:#dc2626;font-size:10px;font-weight:800}.doc-meta-title>div{flex:1}.doc-meta-title h2{margin:0;font-size:13px}.doc-meta-title p{margin:5px 0 0;color:#94a3b8;font-size:8px}.meta-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:7px;margin-top:13px}.meta-grid>div{padding:10px;border-radius:8px;background:#f8fafc}.meta-grid small,.meta-grid b{display:block}.meta-grid small{color:#94a3b8;font-size:8px}.meta-grid b{margin-top:4px;font-size:9px}.authority-line{margin:10px 0 0;padding:8px;border-radius:7px;background:#eff4ff;color:#0051d5;font-size:8px}.document-reader{margin-top:13px;overflow:hidden}.document-reader>header{display:flex;align-items:center;justify-content:space-between;padding:10px 13px;background:#f8fafc}.document-reader>header>div:first-child{display:flex;flex-direction:column;gap:3px}.document-reader>header b{font-size:9px}.document-reader>header span{color:#64748b;font-size:8px}.document-reader>header>div:last-child{display:flex;align-items:center}.paper{margin:18px;padding:24px;border:1px solid #e2e8f0;border-radius:4px;background:#fff;box-shadow:0 8px 20px rgba(15,23,42,.06);color:#475569;line-height:1.9;transition:font-size .2s}.paper code{color:#94a3b8;font-size:.75em}.paper h4{text-align:center;color:#334155}.highlight{position:relative;padding:13px;margin:13px -6px;border-radius:8px;background:#fffbeb;box-shadow:inset 3px 0 #f59e0b}.highlight label{position:absolute;right:8px;top:-10px;padding:3px 6px;border-radius:5px;background:#f59e0b;color:white;font-size:.65em;font-weight:700}.drawer-actions{display:flex;gap:7px;flex-wrap:wrap;padding-top:3px}
@media(max-width:1180px){.source-page{grid-template-columns:1fr}.source-drawer{height:auto;min-height:0}.answer-preview{display:none}}
@media(max-width:640px){.drawer-scroll{padding:11px}.access-bar{align-items:flex-start;flex-wrap:wrap}.access-bar .el-button{width:100%}.meta-grid{grid-template-columns:1fr 1fr}.doc-meta-title{flex-wrap:wrap}.doc-meta-title .status-pill{margin-left:45px}.paper{margin:10px;padding:14px}.drawer-actions .el-button{width:100%;margin-left:0}}
</style>
