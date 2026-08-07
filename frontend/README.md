# Information Agent 前端

这是 Information Agent 的独立前端目录，当前版本只提供 React 页面框架和本地视图状态，不连接后端、不发送网络请求，也不包含 API 类型或请求客户端。

## 技术选型

- React 19
- TypeScript
- Vite
- Tailwind CSS 4
- Lucide React
- ESLint 和 Prettier

运行时只保留 React、React DOM、Tailwind CSS 和 Lucide 图标。路由、状态管理、请求库和 API 层在后续需求明确后再引入。

## 页面结构

界面采用 RSS 阅读器常见的三栏工作区：左侧管理阅读视图、研究运行和订阅源，中间筛选文章队列，右侧阅读正文并打开上下文提问面板。移动端在文章列表和正文之间切换。

```text
src/
├── App.tsx                  # 工作区状态与组件编排
├── components/             # 侧栏、列表、阅读器、提问面板和添加源弹窗
├── data/mockData.ts        # 本地演示数据，后续由 API 适配层替换
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
