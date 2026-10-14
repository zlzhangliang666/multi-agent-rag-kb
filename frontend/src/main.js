import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'

import App from './App.vue'
import router from './router'
import './style.css'

const app = createApp(App)

// Element Plus（中文语言包），全量注册，页面中无需再单独 import 组件
app.use(ElementPlus, { locale: zhCn, size: 'default', zIndex: 3000 })
app.use(router)

app.mount('#app')
