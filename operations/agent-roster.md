---
tags: [operations, agents, ai-workforce]
created: 2026-07-05
---

# 🤖 公司 AI 代理池

> 通过 Agency Agents 插件（`~/.hermes/plugins/agency-agents-router`）接入的 233 个专家 AI 代理。
> 4 个调用工具：`agency_agents_search` / `inspect` / `load` / `delegate`

## 代理分部与公司部门映射

| Agency 分部 | 代理数（估计） | 对应公司部门 | 典型用途 |
|-------------|:---:|------|------|
| engineering | ~30+ | [[engineering/README\|工程部]] | 代码撰写、技术文档、代码审查 |
| security | ~15+ | — | 安全审计、威胁分析、漏洞研究 |
| marketing | ~20+ | [[marketing/README\|市场部]] | 内容创作、电商运营、直播策略 |
| product | ~10+ | [[product/README\|产品部]] | 产品策略、需求分析 |
| sales | ~5+ | [[sales/README\|销售部]] | 技术售前、客户沟通 |
| design | ~10+ | [[design/README\|设计部]] | UI/UX、视觉设计 |
| specialized | ~30+ | — | 文档生成、开发者关系、跨领域专家 |
| spatial-computing | ~10+ | — | XR/VR/AR 交互 |
| game-development | ~10+ | — | 游戏设计、Roblox 开发 |
| project-management | ~5+ | [[operations/README\|运营部]] | 项目管理、工作室运营 |
| finance | ~5+ | [[finance/README\|财务部]] | 财务分析 |
| strategy | ~3+ | [[strategy/README\|战略部]] | 战略分析、商业策划 |

## 当前使用情况

| 用途 | 使用频率 | 代理类型 |
|------|:---:|------|
| 文章撰写（ai-edu-series） | 高频 | technical-writer, content-creator |
| 文章精选 QA 质检 | 每篇 | 各领域专家 |
| 安全研究内容编译 | 中频 | security-architect, appsec-engineer |
| 封面图/信息图 | 按需 | （通过 codex 代理） |

## 对话任务委派

Hermes 是管理对话入口，不再默认承担所有执行工作。公司 Router 将请求分成两类：

- 状态查询、解释、澄清和决策沟通由 Hermes 主 Agent 直接处理；
- 修改、实现、开发、修复、调研、整理、审计和验证等执行请求创建带 `run_id` 的隔离公司 Worker。

公司 Worker 必须声明所属部门，直接完成授权范围内的工作，运行验证，并在独立运行目录提交 `task-report.md` 和 `result.json`。需要专业拆分时，可继续使用 Agency Agents 的 search/inspect/delegate 能力，但最终验收和结构化回传由公司 Worker 负责。公开发布、付款、外部消息、不可逆操作和未授权外部安全测试仍需人工审批。

## 编程开发任务委派约定

针对编程/开发/实现类任务（代码撰写、功能开发、Bug 修复、重构等），采用以下优先级：

1. **优先 Codex**：如果 Codex CLI 有可用额度（Token/配额未耗尽），优先通过 Codex 执行编程任务；
2. **降级询问**：如果 Codex 无可用额度，Hermes 应主动询问用户是否由自身接手执行，不得在未经用户确认的情况下自动替代 Codex；
3. **非编程任务不变**：分析、调研、审计、文档、运维等非编程任务不受此约定影响，继续按原有路由规则执行。

此约定适用于公司 Worker 执行的所有编程任务，作为任务路由决策的前置检查。

## 管理规范（建议）

1. **部门归属**：每个活跃代理应归属到一个公司部门，由该部门 README 记录
2. **调用日志**：重大委派任务应记录代理类型、任务摘要、产出质量
3. **审计**：每月审查代理使用数据，淘汰未使用的分部，激活新需求分部
4. **成本**：代理调用走 Hermes 的 LLM API，需纳入 [[finance/README\|财务部]] 预算追踪
