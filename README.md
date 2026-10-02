<div align="center">

# Information Agent

面向本地 RSS 阅读器的证据优先信息工作流

[![CI](https://img.shields.io/github/actions/workflow/status/Ita-Hloks/InformationAgent/ci.yml?branch=main&style=for-the-badge&label=CI)](https://github.com/Ita-Hloks/InformationAgent/actions/workflows/ci.yml)

</div>

## 这是什么？

Information Agent 提供本地 RSS/Atom 阅读器、文章正文快照、摘要、文章问答和文章研究。文章研究从当前文章快照开始，调用通用 Agent 进行阶段化核验，并保存尝试、错误、部分结果和引用。

## 快速开始

需要 Python 3.11 或更高版本：

```bash
git clone https://github.com/Ita-Hloks/InformationAgent.git
cd InformationAgent
python -m venv .venv
python -m pip install -r requirements.txt
```

配置 `LLM_API_KEY` 后，可以运行一次性 CLI：

```bash
python -m information_agent.cli collect "人工智能" "https://www.geekpark.net/rss" --limit 5
```

本地阅读器服务：

```powershell
python -m uvicorn information_agent.api:app --host 127.0.0.1 --port 8001
```

前端开发服务器：

```powershell
cd frontend
npm install
npm run dev
```

数据库默认位置为 `data/information_agent.db`，可使用 `INFORMATION_AGENT_DB_PATH` 指定其他路径。

## 工作流

```mermaid
flowchart LR
    A[RSS/Atom 订阅] --> B[刷新来源]
    B --> C[文章正文快照]
    C --> D[阅读器当前文章]
    D --> E[摘要、问答]
    D --> F[自动或手动文章研究]
    F --> G[通用 Agent 阶段与尝试]
    G --> H[结论、引用、部分结果或停止状态]
    D --> I[显式舆情分析]
```

一次性 CLI 仍提供：

| 命令 | 用途 |
| --- | --- |
| `collect` | 采集、规范化、筛选并输出 JSON |
| `analyze` | 一次性采集并分析 |
| `plan` | 一次性生成搜索计划 |
| `search` | 一次性采集、规划并联网回答 |
| `opinion-run` | 对阅读器文章主动运行舆情分析 |
| `opinion-status` | 读取文章舆情状态 |
| `verify-search` | 验证联网搜索配置 |

## 文章研究 API

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/api/articles/{article_id}/research` | 获取研究历史元数据 |
| `GET` | `/api/articles/{article_id}/research/{run_id}` | 获取指定运行及 Agent 详情 |
| `POST` | `/api/articles/{article_id}/research` | 创建或复用自动/手动研究 |
| `POST` | `/api/articles/{article_id}/research/{run_id}/stop` | 停止指定研究运行 |

约束：

- 研究必须绑定 `article_id` 和 `snapshot_id`
- 同一文章、同一快照最多一个排队或运行中的任务
- 自动任务不会自动重跑
- 手动任务在上一条结束后可以形成新的历史记录
- 历史列表只返回元数据，详情按需读取
- 当前快照没有结果时不展示其他快照结果

## 舆情分析

当前后端最小 MVP 使用 `POST /api/articles/{article_id}/opinion/references`：

```text
文章全文 → 提取 1～2 组检索关键词 → B站视频搜索 → Agent相关性筛选 → 返回列表
```

关键词提取围绕文章核心对象、事件和主题，不要求文章具有争议。复用已有查询计划契约，`queries` 返回原文依据、目标问题、关键词组合和检索目的。每组查询最多取 5 个候选，按视频标识和 URL 去重。Agent 根据正文及候选标题、简介、标签等元数据逐个判断，不读取评论、字幕或视频内容。

响应包含 `article_id`、`snapshot_id`、`content_hash`，以及：

- `queries`：查询词及原文依据
- `candidates`：全部候选视频及元数据
- `selections`：逐个候选的 `video_id`、`decision` 和中文 `reason`
- `selected_video_ids`：程序从 `selected` 决策计算出的选中列表
- `status`、`status_reason`、`errors`：执行结果与失败信息

`decision` 为 `selected`（内容接近）、`rejected`（无关）或 `uncertain`（信息不足）。允许没有选中视频；正常筛选后无人选中为 `completed/no_matches`。筛选失败为 `partial/selection_failed`，保留候选但不默认选中。部分搜索失败时保留可用候选并筛选，返回 `partial/partial_search`。关键词生成、搜索、筛选共享一次请求的 300 秒预算，单次模型请求仍受公共调用层时限约束。

调用方式（将 `<article_id>` 替换为 `GET /api/articles` 返回的文章 ID）：

```powershell
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8001/api/articles/<article_id>/opinion/references' |
    ConvertTo-Json -Depth 12 |
    Set-Content -Encoding UTF8 'log/video-references.json'
```

此接口到候选筛选结果为止，不创建评论分析任务，也不写入舆情运行表；需要保留验收结果时保存返回 JSON。LLM 调用沿用项目现有日志配置。

已有评论分析是另一项显式操作：`opinion-run` 或 `POST /api/articles/{article_id}/opinion`。普通文章只采集筛选通过的视频；B站视频或专栏来源可直接采集。它分析最近 72 小时的公开评论样本，不代表总体民意，也不证明文章主张为真。

## 配置

| 变量 | 用途 |
| --- | --- |
| `LLM_API_KEY` | 主模型凭据 |
| `LLM_BASE_URL` | OpenAI 兼容 API 根地址 |
| `LLM_MODEL` | 主模型名称 |
| `SEARCH_LLM_API_KEY` | 联网搜索模型凭据 |
| `SEARCH_LLM_MODEL` | 联网搜索模型名称 |
| `SEARCH_LLM_BASE_URL` | 联网搜索服务地址 |
| `SEARCH_LLM_ADAPTER` | 搜索协议；官方 OpenAI Responses API 使用 `openai_responses_web_search` |
| `BILIBILI_COOKIE` | 舆情分析所需的可选 Cookie |
| `INFORMATION_AGENT_DB_PATH` | SQLite 数据库路径 |

没有 LLM 配置时仍可使用订阅、刷新和文章阅读接口。

文章研究的可验证来源必须来自搜索服务响应中的真实 `web_search_call`，不能只依赖模型正文自行填写的 URL。使用官方 OpenAI Responses API 时，将 `SEARCH_LLM_ADAPTER` 设为 `openai_responses_web_search`，并将 `SEARCH_LLM_BASE_URL` 设为 `https://api.openai.com/v1`。服务返回 `web_search_call` 和 URL 引用后，研究结果才会被标记为有证据；只有正文 URL 或没有搜索调用时会保留为证据不足。

## 项目结构

```text
InformationAgent/
├── information_agent/
│   ├── collection/            # RSS/Atom 与网页正文采集
│   ├── normalization/         # 文章规范化与快照内容
│   ├── orchestration/         # CLI、文章研究和 Agent 编排
│   ├── opinion/               # 舆情分析业务
│   ├── storage/               # SQLite 快照与生命周期持久化
│   ├── api/                   # FastAPI 阅读器接口
│   ├── cli.py                 # CLI 入口
│   └── serialization.py       # JSON 输出
├── frontend/                  # React 阅读器
├── tests/                     # 定向和回归测试
├── projectFlow.md             # 当前数据流与边界
└── .env.example               # 环境变量示例
```

## 验证

后端定向测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

前端检查：

```powershell
cd frontend
npm run typecheck
npm run lint
npm run format:check
npm run build
```
