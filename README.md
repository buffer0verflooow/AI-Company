# 公司集团

> 本机部署目录：`/home/pwn/workspace/company`。部署状态与数据边界见 [[DEPLOYMENT]]。

## 目录结构

```
company/
├── README.md           ← 你在这里
├── strategy/           # 公司战略、规划文档
├── engineering/        # 工程部 — 代码、架构、基础设施
├── design/             # 设计部 — UI/UX、品牌、视觉
├── marketing/          # 市场部 — 内容、推广、社交媒体
├── product/            # 产品部 — 需求、路线图、反馈
├── sales/              # 销售部 — 获客、提案、客户
├── finance/            # 财务部 — 预算、报表、分析
├── operations/         # 运营部 — 支持、法务、合规
└── projects/           # 跨部门项目（一个项目一个子目录）
```

## 工作原则

1. 每个部门独立目录，避免文件散落在 workspace 根目录
2. 跨部门项目放在 `projects/<项目名>/` 下
3. 部门产出的文档、代码、数据都在对应部门目录内
4. 使用 agency-agents-router 插件按需加载 AI 专家
5. 产品线运行数据与公司公共知识分离；敏感信息只通过审核后的摘要进入 Wiki
6. 经营问题先由 TVCR 基于真实投入产出提出运营实验，代码只作为经批准方案的实现手段

## 已安装工具

- **Agency Agents Router**: 233 个 AI 专家，按需搜索/加载/委派
  - `agency_agents_search` — 搜索专家
  - `agency_agents_load` — 加载专家上下文
  - `agency_agents_delegate` — 委派子代理执行任务
## 本机产品线

- [[operations/business-lines/article-production|文章创作分享]]
- [[operations/business-lines/video-production|视频创作]]
- [[operations/business-lines/security-exploration|安全探索与赏金]]
