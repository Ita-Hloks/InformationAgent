# Information Agent 前端

这是 Information Agent 的独立前端目录，当前版本只提供 React 页面框架和本地视图状态，不连接后端、不发送网络请求，也不包含 API 类型或请求客户端。

## 技术选型

- React 19
- TypeScript
- Vite
- Tailwind CSS 4
- ESLint 和 Prettier

运行时只保留 React、React DOM 和 Tailwind CSS。路由、状态管理、请求库、图标库和 API 层在后续需求明确后再引入。

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
