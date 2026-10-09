# 校园智库前端

基于 Vue 3、Vue Router、Element Plus 与 Vite 实现，对应 `frontend_test` 中的 10 张 Stitch 高保真原型。

## 启动

```bash
npm install
npm run dev
```

生产构建：

```bash
npm run build
```

## Docker 运行

```bash
docker build -t edurag-frontend .
docker run --rm -p 8080:80 edurag-frontend
```

启动后访问 `http://localhost:8080`。镜像使用 Nginx 托管构建产物，并已配置 Vue Router history 路由回退。健康检查端点为 `/healthz`。

## 页面路由

| 原型编号 | 页面 | 路由 |
| --- | --- | --- |
| P01 | 登录与统一身份认证 | `/login` |
| U01 | 问答首页 | `/home` |
| U02 | 对话详情 | `/chat` |
| U03 | 历史会话 | `/history` |
| U04 | 引用来源预览 | `/source` |
| A01 | 管理概览 | `/admin/overview` |
| A02 | 知识库列表与管理 | `/admin/knowledge-bases` |
| A06 | 文档批量上传与配置 | `/admin/upload` |
| A07 | 文档详情与切片测试 | `/admin/document` |
| A09 | 用户与权限管理 | `/admin/users` |
| A11/A12 | 问答记录与反馈管理 | `/admin/feedback` |

页面已接入后端真实 API，包括登录态恢复、首页聚合、问答与回答轮询、会话管理、引用预览、全局搜索、通知，以及知识库、文档、任务、用户和反馈管理。访问管理路由需要对应 RBAC 权限；页面会按后端返回的权限和可见范围加载数据。
