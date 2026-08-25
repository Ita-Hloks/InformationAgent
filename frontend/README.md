# 信息助手前端

这是 Information Agent 的本地 RSS 阅读器前端。订阅、文章、已读、收藏和文章研究都通过 FastAPI 写入 SQLite；前端不直接访问数据库或模型服务。当前没有登录、多用户隔离和跨设备同步。

## 技术选型

- React 19
- TypeScript
- Vite
- Tailwind CSS 4
- Lucide React
- React Router
- ESLint 和 Prettier

开发服务器会把 `/api` 请求代理到 `http://127.0.0.1:8001`，需要先启动根目录中的 FastAPI 服务。

## 页面结构

阅读器工作区由阅读视图、文章列表和正文组成。文章研究入口位于当前文章正文下方；研究历史只显示元数据，选中后再加载该次运行的 Agent 详情。历史结果始终带有正文快照标识，当前文章不会展示其他快照的结果。

```text
src/
├── App.tsx                  # 路由入口
├── app/                     # 路由表与导航路径
├── api/                     # 订阅、文章、阅读状态和文章研究客户端
├── components/              # 应用壳、文章列表和阅读器组件
├── data/localState.ts       # 空的本地初始状态
├── hooks/                   # 弹层交互逻辑
├── types.ts                 # 前端领域类型
└── styles.css               # Tailwind 入口与全局阅读样式
```

## 开发命令

```bash
npm install
npm run dev
npm run typecheck
npm run lint
npm run format:check
npm run build
```

提交标题参考 `type(scope): 中文说明` 格式，例如：

```text
feat(frontend): 建立前端基础框架
fix(frontend): 修复移动端布局
chore(frontend): 更新构建依赖
```
