import { createApp } from 'vue'
import {
  ElBadge, ElButton, ElCheckbox, ElDatePicker, ElDialog, ElEmpty, ElForm,
  ElFormItem, ElIcon, ElInput, ElOption, ElPagination, ElProgress,
  ElRadioButton, ElRadioGroup, ElSegmented, ElSelect, ElSlider, ElSwitch,
  ElTable, ElTableColumn, ElTag, ElUpload,
} from 'element-plus'
import 'element-plus/dist/index.css'
import App from './App.vue'
import router from './router'
import './styles/theme.css'

const app = createApp(App)
const components = [
  ElBadge, ElButton, ElCheckbox, ElDatePicker, ElDialog, ElEmpty, ElForm,
  ElFormItem, ElIcon, ElInput, ElOption, ElPagination, ElProgress,
  ElRadioButton, ElRadioGroup, ElSegmented, ElSelect, ElSlider, ElSwitch,
  ElTable, ElTableColumn, ElTag, ElUpload,
]

components.forEach((component) => app.component(component.name, component))
app.use(router).mount('#app')
