import type { Article, Feed, ResearchRun } from "../types";

export const initialFeeds: Feed[] = [
  { id: "geekpark", name: "极客公园", domain: "geekpark.net", unread: 8, color: "#ef8354" },
  {
    id: "mit-tech",
    name: "MIT Technology Review",
    domain: "technologyreview.com",
    unread: 4,
    color: "#4f86c6",
  },
  { id: "verge", name: "The Verge", domain: "theverge.com", unread: 3, color: "#8f6ac8" },
  { id: "36kr", name: "36氪", domain: "36kr.com", unread: 6, color: "#2e9b72" },
  { id: "sspai", name: "少数派", domain: "sspai.com", unread: 2, color: "#d65454" },
];

export const initialArticles: Article[] = [
  {
    id: "inference-cost",
    feedId: "geekpark",
    source: "极客公园",
    author: "靖宇",
    title: "推理成本开始成为 AI 产品的第二条增长曲线",
    summary: "模型能力继续提升，但产品团队正在把注意力移向每一次回答背后的计算账单。",
    publishedAt: "10:24",
    readingMinutes: 7,
    category: "AI 产品",
    imageUrl:
      "https://images.unsplash.com/photo-1518770660439-4636190af475?auto=format&fit=crop&w=1200&q=82",
    unread: true,
    starred: false,
    body: [
      "过去一年，AI 产品的竞争焦点从“能不能回答”移动到了“能不能持续回答”。模型规模仍在增长，但决定产品是否成立的，越来越多是一次推理究竟花多少钱、等多久，以及失败时如何降级。",
      "这使缓存、模型路由和小模型协作从基础设施议题进入产品设计。用户看不到路由器，却会直接感受到延迟、稳定性和价格。推理成本不再只是财务报表里的数字，而是体验的一部分。",
      "对小团队而言，机会并不一定来自训练更大的模型。把同一个任务拆得更清楚、让普通代码承担验证和状态管理，再把真正需要语义判断的部分交给模型，往往更接近可持续的产品边界。",
    ],
  },
  {
    id: "browser-runtime",
    feedId: "mit-tech",
    source: "MIT Technology Review",
    author: "James O'Donnell",
    title: "浏览器正在变成一层更薄的应用运行时",
    summary: "新的 CSS 能力和原生 API 让交互、动画与数据呈现重新回到平台本身。",
    publishedAt: "09:48",
    readingMinutes: 9,
    category: "Web 平台",
    imageUrl:
      "https://images.unsplash.com/photo-1498050108023-c5249f4df085?auto=format&fit=crop&w=1200&q=82",
    unread: true,
    starred: true,
    body: [
      "前端平台的变化很少一次性出现在产品路线图里。它们先以一个可用的 CSS 属性或边缘 API 出现，然后在真实页面中逐步形成新的默认值。",
      "当浏览器开始原生承担视图过渡、容器查询和滚动驱动动画，应用可以减少用于协调界面的脚本。性能收益只是结果之一，更重要的是组件终于能够根据自己的上下文而不是整个视口工作。",
      "对研究工具来说，这意味着界面可以更接近内容本身：列表、阅读和注释之间的切换不必依赖复杂的页面跳转，状态也更容易被保留。",
    ],
  },
  {
    id: "research-log",
    feedId: "verge",
    source: "The Verge",
    author: "David Pierce",
    title: "小团队开始用研究日志管理不确定性",
    summary: "当信息流变快，最有价值的不是更多链接，而是知道每个判断从哪里开始。",
    publishedAt: "08:32",
    readingMinutes: 6,
    category: "知识管理",
    imageUrl:
      "https://images.unsplash.com/photo-1451187580459-43490279c0fa?auto=format&fit=crop&w=1200&q=82",
    unread: true,
    starred: false,
    body: [
      "研究日志把一次阅读从短暂的浏览变成可回看的过程。来源、问题、证据和结论被放在同一条线上，下一次判断不必重新从空白开始。",
      "这种记录的价值不在于保存所有内容，而在于保留决定发生时的上下文。团队能够区分事实、推测和仍待验证的部分，也能看见一次结论经过了哪些修正。",
      "当生成式模型参与研究时，日志还承担了更重要的职责：限制模型可以宣布什么，并让每一次工具调用、失败和重试都能被普通代码观察。",
    ],
  },
  {
    id: "agent-workflow",
    feedId: "36kr",
    source: "36氪",
    author: "宋婉心",
    title: "Agent 产品从“会调用工具”走向“可恢复工作流”",
    summary: "企业开始要求 Agent 不只完成演示，还要解释状态、失败位置和下一步。",
    publishedAt: "昨天",
    readingMinutes: 8,
    category: "Agent",
    imageUrl:
      "https://images.unsplash.com/photo-1555949963-ff9fe0c870eb?auto=format&fit=crop&w=1200&q=82",
    unread: false,
    starred: false,
    body: [
      "工具调用曾经是 Agent 产品最直观的能力证明，但真实工作流很快暴露了另一个问题：失败之后发生什么。",
      "可恢复的 Agent 把执行状态放在模型之外。每一步都有输入、输出和明确的终止条件，重试不会覆盖已有证据，用户也能知道系统停在了哪里。",
      "这类设计并不会削弱模型，反而让模型专注于最适合它的语义判断，同时让产品拥有可以测试和维护的边界。",
    ],
  },
  {
    id: "reading-habits",
    feedId: "sspai",
    source: "少数派",
    author: "化学心情下2",
    title: "从囤积到阅读：重新整理我的 RSS 输入流",
    summary: "订阅数量不是问题，缺少稳定的筛选、稍后读和回顾动作才是。",
    publishedAt: "昨天",
    readingMinutes: 11,
    category: "RSS",
    imageUrl:
      "https://images.unsplash.com/photo-1484417894907-623942c8ee29?auto=format&fit=crop&w=1200&q=82",
    unread: false,
    starred: true,
    body: [
      "RSS 最容易形成的错觉，是订阅越多就越接近完整的信息。实际使用中，过长的未读数字往往只会把阅读变成清理任务。",
      "更可靠的方法是把输入分为需要及时处理、可以稍后阅读和仅用于搜索的来源。它们拥有不同的更新频率，也不该共享同一套未读压力。",
      "阅读器如果能够把筛选、正文和笔记放在连续的界面里，就能减少在多个工具之间搬运链接的成本。",
    ],
  },
  {
    id: "source-citations",
    feedId: "mit-tech",
    source: "MIT Technology Review",
    author: "Melissa Heikkilä",
    title: "AI 搜索的下一场竞争是把答案重新绑定到来源",
    summary: "用户不只需要一个流畅答案，还需要知道证据是否真的支持其中的具体断言。",
    publishedAt: "周二",
    readingMinutes: 10,
    category: "可信 AI",
    imageUrl:
      "https://images.unsplash.com/photo-1526374965328-7f61d4dc18c5?auto=format&fit=crop&w=1200&q=82",
    unread: false,
    starred: false,
    body: [
      "搜索产品正在把引用放回答案旁边，但一个链接只能证明来源身份，不能自动证明某一句话得到了支持。",
      "真正的来源绑定需要把结论拆成可以核验的断言，再确认引用内容、时间和上下文都与断言一致。证据不足也必须成为一种可见状态。",
      "这使答案生成从一次模型调用变成有边界的工作流：检索、选择、引用和完成判断由不同步骤负责，并留下可追踪的中间结果。",
    ],
  },
];

export const researchRuns: ResearchRun[] = [
  { id: "run-01", title: "AI 推理成本", status: "completed", articleCount: 18 },
  { id: "run-02", title: "浏览器平台变化", status: "partial", articleCount: 12 },
];
