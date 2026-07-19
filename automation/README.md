---
tags: [automation, router, hermes, swarm]
created: 2026-07-15
updated: 2026-07-15
---

# 公司自动路由

## 组成

| 文件 | 作用 |
|------|------|
| `company_router.py` | 对话分类、授权门禁、去重、蜂群提交、状态与结果注入 |
| `swarm_hermes_executor.py` | 将市场任务交给隔离的 Hermes Worker 执行 |
| `content_hermes_executor.py` | 公司通用执行、文章和视频独立 Worker，强制产出文件、结构化结果与质量门结果 |
| `company_result_notifier.py` | 轮询 Run、恢复失效 Runner、把终态结果主动回传原会话 |
| `hermes_company_result_notifier.py` | 部署到 Hermes scripts 目录的 Cron 入口 |
| `knowledge_promotion_gateway.py` | Swarm 知识进入 Wiki 前的验证、脱敏、披露与人工审批门禁 |
| `finance_ledger.py` | 实际收支、赏金预测、模型成本和完成 Run 的证据化分账 |
| `operations_control.py` | 产品线经营账本、TVCR 提案审批和运营实验状态机 |
| `tvcr_daily_review.py` | 汇总前一经营日证据并调用独立 TVCR Agent 生成报告与提案 |
| `hermes_tvcr_daily_review.py` | TVCR 每日 Cron 入口 |
| `company_operator.py` | 主动发现经营机会、评分排队、执行低风险内部任务并记录结果 |
| `hermes_company_operator.py` | 每日公司自治经营 Cron 入口 |
| `company_operator_config.json` | 自治边界、单周期预算和长期经营任务 |
| `market_radar.py` | 采集公开外部信号、清洗去重、多来源佐证并生成市场脉冲 |
| `hermes_market_radar.py` | 每日市场雷达 Cron 入口 |
| `market_radar_config.json` | 公开查询白名单、AnySearch 固定来源、资格门和评分规则 |
| `router_config.json` | 正式路径、并发上限、自动执行开关 |
| `tests/` | 分类、审批、幂等和真实 Swarm 客户端契约测试 |

## Hermes 接入

Router 注册为全局 `pre_llm_call` Shell Hook：

```yaml
hooks:
  pre_llm_call:
    - command: /home/pwn/workspace/company/automation/company_router.py --hook
      timeout: 30
```

Hook 每轮只注入私有上下文，不修改原始用户消息。安全任务会得到 `run_id`；同一 `session_id + message_hash` 只允许创建一个 Run。Router 会从 Hermes `sessions.json` 解析原始平台、聊天和线程，供后台结果定向回传。

## 后台回传与自愈

Hermes Cron 任务 `company-product-result-notifier` 每分钟运行一次，无 LLM 成本：

1. 查询公司 Router 创建的未交付 Run；
2. 同步 Swarm 终态；
3. Runner 失效且 Run 仍在运行时，按重启上限恢复；
4. 完成或失败后，仅向配置允许的平台投递；
5. 投递得到明确成功响应后，才写入 `proactive_delivered=1`；
6. 同时把通知镜像到 Hermes 会话记录，后续对话能够看到该结果。

当前正式配置只允许主动投递到 `weixin`。可重试的网络失败保留错误、尝试次数并按退避策略重试；来源平台不在白名单时会立即进入 `terminal`，不再空耗 50 次重试，同时把完整通知写入 `operations/runtime/delivery-dead-letters.jsonl`，供管理者恢复或改道投递。

```bash
python3 automation/company_result_notifier.py --list-terminal --limit 20
```

## 安全知识晋升

`knowledge_promotion_gateway.py` 默认只扫描并创建候选，不把 Swarm 原始正文复制到公司库。进入 `wiki/promoted/` 必须依次满足：

1. 自动验证阈值或人工复核；
2. 脱敏稿再次通过确定性敏感信息扫描；
3. 明确提供 `public` 披露/修复状态；
4. 指定 reviewer 人工批准；
5. 最后单独执行 promote。

首次正式扫描：66 条 active 知识中 64 条因敏感内容阻断，2 条需要进一步验证，0 条自动晋升。

```bash
python3 automation/knowledge_promotion_gateway.py --scan
python3 automation/knowledge_promotion_gateway.py --list --status blocked_sensitive
# 人工准备完全重写的脱敏稿后：
python3 automation/knowledge_promotion_gateway.py \
  --approve <candidate_id> --reviewer <name> \
  --reviewed-file <reviewed.md> --disclosure-status public
python3 automation/knowledge_promotion_gateway.py --promote <candidate_id>
```

## 财务分账

`finance_ledger.py` 禁止把预估赏金计入实际收入。实际交易必须提供证据文件；模型会话价格未知时记录为 `unpriced`，而不是假定零成本。

首次正式同步：实际收入/支出均为 0；HackerOne forecast 为 `$10,850–$30,150`；60 个 Hermes 会话待定价。

## TVCR 经营治理

### Codex 与 Claude 原生用量采集

`operations_control.py sync-runs` 会优先读取隔离 Hermes Worker 的计数；没有 Hermes 记录时，直接读取 Codex 或 Claude Code 原生会话日志。Codex 来源为 `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` 的最终 `token_count`；Claude 来源为 `~/.claude/projects/*/*.jsonl` 中按 `message.id` 去重后的 `message.usage`。Codex 的 `input_tokens` 包含缓存输入，因此拆成非缓存输入与缓存读取；Claude 的 `input_tokens`、`cache_read_input_tokens`、`cache_creation_input_tokens` 分别写入输入、缓存读取和缓存写入。会话中包含对应任务目录或 `run_id` 才会关联；两种原生日志同时命中时选择最新会话，无法关联时保持未计量。

运行任务会同步到 `operations/runtime/operations_control.db`。系统记录 Token、耗时、工具调用、产物和质量状态，但不会猜测业务价值。采用、发布、触达和收入需要证据化补录。

每日 TVCR Agent 只生成经营报告和待审批提案，禁止直接修改代码、Prompt、配置或 SOP。用户批准后创建运营实验，由公司主 Agent 按业务、产品、流程、资源、技术的顺序落实。

```bash
python3 automation/operations_control.py sync-runs
python3 automation/tvcr_daily_review.py --date 2026-07-15
python3 automation/operations_control.py proposals
```

完整机制见 [[../operations/tvcr-governance|TVCR 经营治理闭环]]。

## 每日自治经营

`company_operator.py` 补上 Router 之前缺失的主动入口。它不等待用户消息，而是从长期经营任务、TVCR 提案、已批准实验和缺少结果的 Run 中建立机会队列；每周期按可执行积压动态分配预算，在不同来源间轮转选择，并用配置限定的并行 Worker 执行满足风险和授权边界的内部任务。默认正式配置为基础预算 1、每 2 个积压增加 1 个名额、最多 4 项、最多 2 个并行 Worker。

默认 Worker 只能向 `operations/runtime/autonomy-runs/<run_id>/` 写入产物。公开发布、付款、删除、外部安全测试和 HackerOne 提交仍必须审批。自治运行本身会作为 `source_type=autonomy` 进入经营账本，由下一轮 TVCR 评价实际价值。

```bash
python3 automation/company_operator.py --plan-only --no-delivery
python3 automation/company_operator.py --queue
```

完整机制见 [[../operations/autonomous-company|公司自治运行闭环]]。

## 外部市场雷达

`market_radar.py` 每天从公开网络、知乎/X/Reddit/公众号搜索、企业研究和招聘信号中采集市场信息。当前使用固定到提交与 ZIP 哈希的 AnySearch JSON-RPC 适配器，没有全局安装第三方 Skill，不自动注册账户，也不读取 `.env`。

数据进入经营队列前依次经过：

1. 公开查询白名单和隐私敏感搜索类型阻断；
2. URL 规范化、内容截断、提示注入标记和跨周期去重；
3. 战略相关度、商业意图、新鲜度和信源类型评分；
4. 查询主题资格词过滤；
5. 至少两个独立来源佐证后生成 `market_pulse`；
6. 公司经营者打开至少两个源文，区分正文核验、元数据核验、仅可达和搜索记录级信号，再形成验证实验。

市场雷达每天 `08:30` 运行，公司经营者每天 `09:00` 消费脉冲。联系人/邮箱搜索、自动注册、外部联系和表单提交默认禁用。

```bash
python3 automation/market_radar.py
python3 automation/market_radar.py --pulses
```

## 路由规则

| 类型 | 默认动作 |
|------|----------|
| 公司状态问答、解释和决策沟通 | 公司主 Agent |
| 公司修改、实现、调研、整理、验证等执行请求 | 自动创建公司执行 Worker；按部门职能执行并回传 |
| 文章撰写、内部排版、三道 QA Gate | 自动提交文章产线；公开发布需审批 |
| 视频脚本、分镜、制作计划 | 自动提交视频产线；渲染按环境能力执行，公开上传需审批 |
| 本地安全分析、知识研究 | 自动提交安全蜂群 |
| 外部主动扫描/利用 | 缺少明确授权时阻断并请求 Scope |
| HackerOne 提交、外部发布、付款、删除 | 始终需要人工审批 |

## 验证命令

```bash
cd /home/pwn/workspace/company
python3 -m unittest discover -s automation/tests -v
python3 automation/company_operator.py --plan-only --no-delivery
python3 automation/market_radar.py --pulses
hermes hooks doctor
hermes hooks test pre_llm_call
hermes cron status
hermes cron list
```

## 已验证闭环

2026-07-15 使用只读本机架构分析任务验证：

```text
Hermes pre_llm_call
→ company_router
→ swarmctl task submit
→ run_id 40829ad1-8706-4869-beec-d44a0aa57c3d
→ analyst/reporter Worker 领取 3 个任务
→ 3/3 completed
→ 下一轮 Hook 注入最终结果
```

该验证同时促使正式 `swarm_knowledge.db` 执行了最新幂等迁移；迁移前备份为 `swarm_knowledge.db.bak.company-router-20260715`。

后续真实验收：

- Research Run `db6bcc86-9572-4639-a02e-fa122d44c00a`：3/3 completed，Runner 自愈 1 次，微信主动回传成功并镜像会话。
- 文章 Run `3dc43b1c-22e7-4901-ac05-ebd6164080f5`：生成 `draft.md`、`qa-report.md`，24/24 当时测试全绿，三道 Gate 通过。
- 视频预生产 `video-e2e-20260715`：生成 `video-script.md`、`storyboard.md`、`production-plan.md`；明确 Pixelle 未激活，未生成或虚报 MP4。
- 自治经营 `AUTO-RUN-1ec362301fc4`：无用户任务输入时主动选择模型成本盲区，生成成本映射、核验报告、会话明细和待审批应用包；Worker 用量与产物已写回经营账本。正式 Cron `company-daily-operator` 每日 09:00 运行。
- 市场发现 `MKT-RUN-832f3c788415 → AUTO-RUN-aa6d4405550a`：39 条公开结果经过资格门形成 3 个多来源脉冲；经营者验证最高优先级内容需求，打开 2 个独立正文并形成市场机会 Brief、反证、成功指标与停止条件。正式 Cron `company-market-radar` 每日 08:30 运行。
- 相邻市场发现 `MKT-RUN-8bb98f9b66e8 → AUTO-RUN-066cbbf59084`：48 条公开结果形成 4 个多来源脉冲；经营者跨出预设 AI 安全主题，发现 Agent 可观测性、评估、工作流、成本与治理机会。证据审计后记录为 2 个正文核验、1 个元数据核验、1 个仅可达和 1 个搜索记录级信号。

## 当前限制

- Pixelle-Video 源码已部署，但 `uv` 与正式 `config.yaml` 尚未激活；视频 Worker 当前保证脚本、分镜和制作计划，不能虚报 MP4 已渲染。
- 模型画像中的 `client/reasoning` 等逻辑模型目前映射到 Hermes 当前默认模型。
- 微信 iLink Bot 通常无法接收普通微信群事件；当前主动回传已验证和设计为公司微信私聊链路。
- 微信 iLink 主动消息存在服务端频控；Notifier 使用指数退避，避免持续重试扩大限流。
