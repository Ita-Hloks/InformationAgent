# Information Agent

输入研究主题和 RSS 地址，获取真实内容，筛选相关证据，调用 LLM 生成带引用的中文结论，最后验证引用链并输出 JSON

```text
主题 + 一个或多个 RSS 地址
  → 限时获取 RSS/Atom
  → 清洗标题和摘要
  → 规范化 URL、正文和发布时间
  → 按主题加权筛选并按 URL 去重
  → 分配证据编号
  → LLM 生成结构化结论
  → 校验引用编号和文字支持
  → JSON 报告
```

## 项目结构

```text
information_agent/
├── contracts.py              # 所有阶段共享的数据契约
├── collection/
│   ├── normalization.py      # URL、正文和发布时间规范化
│   └── rss.py                # RSS 下载、限制、解析和清洗
├── processing/
│   └── relevance.py          # 主题筛选、去重和证据编号
├── analysis/
│   ├── llm.py           	 # LLM 调用及 JSON 解析
│   └── evaluation.py         # 引用链评估
├── orchestration/
│   └── workflow.py           # 总时限与固定执行顺序
├── serialization.py          # 统一报告 JSON 数据契约
└── cli.py                    # 命令行参数和 JSON 输出
```

依赖方向固定为：

```text
contracts
  ↑
collection / processing / analysis
  ↑
orchestration
  ↑
cli
```

下层模块不能反向导入编排层。`orchestration` 负责调用其他模块，不应重新实现采集、筛选或分析逻辑。

修改公共字段前必须通知所有下游负责人，并同时更新 Mock、测试和 README。

采集结果进入主题筛选前会统一执行以下规范化：

- 只保留 HTTP(S) 文章链接，移除 URL 片段、`utm_*` 和常见点击跟踪参数
- 正文少于 20 字时丢弃；超过 500 字时截断并设置 `content_truncated`
- 将可识别的发布时间转换为 UTC `datetime`，内部精度固定到秒
- 规范化 URL 后再去重，避免跟踪参数导致同一文章被重复处理
- 相关度分数使用标题命中数乘以 2，再加正文命中数

截断不会删除来源 URL，后续网页采集器可以根据 URL 二次获取正文；当前 LLM 调用本身不会访问网页。

报告中的 `published_at` 和 `collected_at` 统一使用 RFC 3339 UTC 字符串，固定精确到分钟，
例如 `2026-07-17T02:30+00:00`。缺失的 `published_at` 输出为 `null`，不使用空字符串；
无时区的内部 `datetime` 会在序列化时被拒绝。

## 安装与运行

要求 Python 3.11 或更高版本：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

`.env` 并填写

运行 RSS：

```powershell
.\.venv\Scripts\python.exe -m information_agent.cli `
  "AI" `
  "https://hnrss.org/frontpage" `
  --timeout 60 `
  --limit 5
```

最终 JSON 包含：

- `status`：完成程度
- `analysis`：总述、带引用结论和不确定性
- `evidence`：实际送入模型的内容
- `evaluation`：引用链检查
- `errors`：RSS 或模型错误

## 开发与验收

```powershell
.\.venv\Scripts\python.exe -m pytest --basetemp .test-tmp -p no:cacheprovider
.\.venv\Scripts\python.exe -m compileall -q information_agent tests
```

测试分为两层：

1. 每个阶段使用固定数据验证自己的输入输出
2. `test_workflow.py` 使用假采集器和假分析器验证完整组合

任何模块只有在正常路径、至少一个失败路径、示例输出和下游消费都通过后才算完成

## 维护方向

### 采集可靠性

- 条件请求：`ETag`、`Last-Modified`
- 网页正文抓取作为新的 `collection/web.py`
- URL 规范化和内容哈希
- 统一发布时间格式
- 每个来源独立重试与限流

### 评估

- 增加独立 `storage/`，而不是把数据库写进采集层
- 建立跨任务文章去重
- 保存报告、错误和耗时
- 建立人工标注数据集

### 分析能力

- 固定分类表
- 多来源事件聚类
- 区分事实、观点和情绪
- 记录观点主体与对象
- 比较来源冲突
- 来源质量分级

###  Agent 编排

只有前面阶段稳定后，才允许编排层从有限动作中选择：

```text
search_saved_articles
fetch_article
summarize
compare_sources
ask_user
finish
```

同时必须设置最大轮数、总时限、费用上限和停止条件

## 维护原则

1. 确定性步骤优先使用普通代码，LLM 只处理语义任务
2. 新功能先增加独立模块，再由编排层接入
3. 没证据不生成事实结论
