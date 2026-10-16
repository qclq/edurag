<script setup>
import { reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { KNOWLEDGE_BASE_CATEGORIES } from '../constants/knowledgeBases'
import { authenticatedRequest, useAuth } from '../stores/auth'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue', 'created'])
const { state } = useAuth()
const saving = ref(false)
const form = reactive({ name: '', description: '', businessCategory: '规章制度' })

watch(() => props.modelValue, (open) => {
  if (open) Object.assign(form, { name: '', description: '', businessCategory: '规章制度' })
})

const close = () => emit('update:modelValue', false)

const create = async () => {
  if (saving.value) return
  const name = form.name.trim()
  if (!name) return ElMessage.warning('请输入知识库名称')
  saving.value = true
  try {
    const created = await authenticatedRequest('/admin/knowledge-bases', {
      method: 'POST',
      body: {
        name,
        description: form.description.trim() || null,
        businessCategory: form.businessCategory || null,
        selectable: true,
        ownerIds: state.user?.id ? [state.user.id] : [],
        acl: [{ isPublic: true, canSearch: true, canPreview: true, canDownload: false, canManage: false }],
        retrieval: {
          chunkSize: 800,
          chunkOverlap: 100,
          topK: 5,
          similarityThreshold: 0.55,
          rerankEnabled: true,
          hybridSearchEnabled: true,
        },
      },
    })
    emit('created', created)
    close()
  } catch (error) {
    ElMessage.error(error.message || '知识库创建失败')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    title="新建知识库"
    width="min(520px, 92vw)"
    :close-on-click-modal="false"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <el-alert title="创建后会自动选中，可立即上传资料并开始入库。" type="info" show-icon :closable="false" />
    <el-form label-position="top" class="quick-kb-form" @submit.prevent="create">
      <el-form-item label="知识库名称 *">
        <el-input v-model="form.name" maxlength="200" placeholder="例如：学生事务与奖助政策" @keyup.enter="create" />
      </el-form-item>
      <el-form-item label="业务分类">
        <el-select v-model="form.businessCategory" style="width:100%">
          <el-option v-for="category in KNOWLEDGE_BASE_CATEGORIES" :key="category" :label="category" :value="category" />
        </el-select>
      </el-form-item>
      <el-form-item label="说明">
        <el-input v-model="form.description" type="textarea" :rows="3" maxlength="5000" show-word-limit placeholder="说明该知识库收录的内容范围" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="close">取消</el-button>
      <el-button type="primary" :loading="saving" @click="create">创建并选中</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.quick-kb-form { margin-top:18px; }
</style>
