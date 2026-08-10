# 信息助手前端

这是 Information Agent 的独立前端目录，默认通过 Vite 代理连接本地文章订阅 API；订阅、文章、已读和收藏状态由本地 FastAPI 服务写入 SQLite。当前没有登录系统，不提供多用户隔离或跨设备同步。

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

界面采用 RSS 阅读器常见的三栏工作区：左侧管理阅读视图、研究运行和订阅源，中间筛选文章队列，右侧阅读正文并打开上下文提问面板。移动端在文章列表和正文之间切换。

```text
src/
├── App.tsx                  # 路由入口
├── app/                     # 路由表与导航路径
├── api/                     # 订阅、文章和阅读状态 HTTP 客户端
├── components/             # 应用壳与按页面划分的组件目录
├── data/localState.ts      # 空的本地初始状态
├── hooks/                  # 弹层交互逻辑
├── types.ts                # 前端领域类型
└── styles.css              # Tailwind 入口与全局阅读样式
```

## 开发命令

```bash
npm install
npm run dev
npm run check
npm run build
```

## 提交格式

提交标题参考 BiliManager，使用 `type(scope): 中文说明` 格式，例如：

```text
feat(frontend): 建立前端基础框架
fix(frontend): 修复移动端布局
chore(frontend): 更新构建依赖
```

仓库根目录的 pre-commit 钩子会在前端文件变化时自动运行 `npm run precommit`，依次检查 TypeScript、ESLint 与 Prettier。首次开发前需要在仓库根目录运行 `pre-commit install --overwrite`；完整构建仍通过 `npm run build` 单独验证。
