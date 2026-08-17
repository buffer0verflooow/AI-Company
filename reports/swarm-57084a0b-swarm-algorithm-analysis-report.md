# 蜂群算法现状分析 — Reporter 报告（讨论素材）

> Run: 57084a0b-dea9-414a-a678-3a2d1d786360 (intent=analyze)
> 角色: reporter | 日期: 2026-08-13
> 目的: 为"分析当前系统蜂群算法并讨论"提供已验证证据汇总
> 证据来源: src/swarm/*.py + src/governance/*.py 代码核实 + swarm_knowledge.db 实盘数据 + 知识库历史实验

---

## 1. 结论摘要（MEDIUM 优先级 — 讨论型任务，无漏洞）

当前蜂群算法是**"SQLite 信息素 + 定时器编排"型 stigmergy 系统**，八层机制在代码中全部落地
（市场领取 / 任务图 / 信号板 / spawn 去重 / worker 信号 / Controller LLM 判决 / Power
Schedule / 治理循环），但**实盘数据暴露两个结构性断点**：

1. **spawn_requests 151 条全部停留在 pending**（0 fulfilled）—— 自动派生的闭环从未在真实
   run 中走通；实际 worker 全部由公司侧手动/脚本生成，Orchestrator 的 spawn 执行路径没有
   活跃消费者。
2. **controller_decisions 表 0 行** —— Controller LLM 判决层从未产出过任何持久化决策，
   理论上最关键的"智能控制层"在实盘中是空转的（可能从未被 tick，或 LLM 调用从未成功）。

因此可讨论的讨论焦点不是"算法设计缺什么"，而是**"设计如何被实盘绕过、以及如何让闭环真正闭合"**。
知识库历史对照实验（MARBLE / BountyBench）同时提供了蜂群>单agent 的正面证据和"单 LLM 全局
推理在诊断类任务上反超蜂群"的反面证据，是讨论的核心锚点。

---

## 2. 证据链

每条发现 = 代码位置 + 输出摘录 + 判定标准，独立可复核。

### F1. 八层机制在代码层全部存在（事实）

| 机制 | 代码锚点 | 核实方式 |
|---|---|---|
| 定时器编排 (7 tick) | `orchestrator.py:101-159` run_loop，2s/5s/10s/15s/30s/60s/60s | 代码直读 |
| 工作市场领取 | `work_queue.py:220+` poll/claim，pending→claimed 原子切换 | 代码直读 |
| 任务图 DAG | `task_graph.py` create/add/publish_ready_nodes + 收据验证 | 代码直读 |
| 图级信号板 | `signal_board.py` publish_signal/get_signals (append-only, 图级作用域) | 代码直读 |
| spawn stigmergy | `spawner.py` request_spawn→claim→fulfilled + dedup_key 去重 | 代码直读 |
| Worker 信号流 | `signals.py` record_worker_signal / detect_loops (滑动窗口) / MinHash novelty | 代码直读 |
| Controller LLM | `controller.py` tick() llm/rules 双模式，kill/boost/spawn/redirect | 代码直读 |
| 治理循环 | `governance/engine.py` promotion/decay/distill + verification pipeline | 代码直读 |

### F2. spawn 闭环未闭合（事实，DB 实证）

```
SELECT status, COUNT(*) FROM spawn_requests GROUP BY status;
pending: 151, fulfilled: 0, rejected: 0, expired: 0
```

- 近 30 条全部由 work-market / researcher-02 发起，reason 均为"任务市场积压: X 有 N 个待处理任务"
- 判定标准: 若 Orchestrator 的 `_tick_spawn` + spawn_handler 正常消费，应有 fulfilled/rejected。
  151/151 pending 说明 spawn 请求只有生产者（capture/work-market），没有活跃消费者（长驻
  Orchestrator 未运行，或 handler 未设置时本应 mark_spawn_rejected 但也没有触发）。
- 交叉验证: `controller.py:_execute_spawn` 是唯一"controller 来源"的 spawn 写入路径，而
  controller_decisions=0 → 该路径也从未执行。

### F3. Controller 判决 0 行（事实，DB 实证）

```
SELECT decision_type, COUNT(*) FROM controller_decisions GROUP BY decision_type;
(空，0 行)
```

- 表存在（schema 已迁移），但无任何记录。`controller.py:tick()` 每次决策都会
  `_record_decision` 持久化，0 行 = tick 从未成功执行过（或从无 run 挂过长驻 orchestrator）。
- 推断: 当前运行模式是 `scripts/agent_worker.py` 单步领取 + 公司侧 executor 执行
  （本 worker 即此模式），Orchestrator 的 run_loop 未在实盘常驻 → Controller/governance/
  spawn 消费三个 60s tick 全部空转。

### F4. 信号流是实盘唯一活跃的闭环（事实，DB 实证）

```
worker_signals: 1022 条 (finding 202 + heartbeat 820)
swarm_behaviors: adaptation 149, optimization 1
agent_profiles: scanner 4 / analyst 2 / researcher 2 / exploiter 1 / reporter 1 (均 active)
```

- finding 信号有 202 条 → capture→record_signal_from_capture 链路真实工作；
  但 behavior 记录中 emergence(涌现) 为 0，optimization 仅 1 → 信号数据没有被上层
  (Controller/治理) 消费的痕迹。

### F5. 蜂群 vs 单 agent 对照实验（事实，知识库 L2/L3 条目）

| 实验 | 结果 | 条目 |
|---|---|---|
| MARBLE 100 任务 (同任务集同模型) | 蜂群 100/100 F1=1.000 vs 单 agent 70/100 F1=0.873，净增 30 任务 | KB L2 |
| MARBLE 数据库诊断类 | 单 LLM 全局推理 6/7 优于蜂群分工 4/7（verifier 视野窄→误报） | KB L2 |
| BountyBench 6 bounty pilot | 单 agent 3/6 vs 蜂群 3~4/6，n=3 样本波动淹没差异 | KB L2 |

- 判定标准: 蜂群价值是**任务相关**的——并行覆盖型（100 任务 F1 提升）胜出，窄视野诊断型
  反而输给单 LLM 全局推理。这是"蜂群何时值得用"的最强实证。

### F6. 已知架构盲区（事实，docs/ARCHITECTURE-BLINDSPOTS.md + role-gap-analysis）

- 五层盲区叠加: 模型层（低频模式识别不稳 0~33%）→ 执行层（从零构造=盲猜）→ 假设库层
  （CWE 粒度=覆盖天花板）→ 目标状态层（verify 只认特定状态）→ 统计层（n=3 波动 1/3~2/3）
- 角色缺口: Queen（Controller 是战术执行器，无战略层/跨 run 仲裁/历史学习）、Prophet（完全
  缺失，无跨 run 模式发现与 L4 提炼）、Guide（无独立逃生机制，kill→spawn 同类=同方法论撞墙）

---

## 3. 影响评估（讨论视角）

| 维度 | 当前状态 | 影响 | 前置条件 |
|---|---|---|---|
| 自动派生 (stigmergy spawn) | 设计完整、实盘 0 消费 | 蜂群无法自行扩编，"涌现"不存在；规模上限=预置 worker 数 | 长驻 Orchestrator + spawn_handler |
| 智能控制 (Controller) | LLM+rules 双模式就绪、0 决策 | 无 kill/boost/spawn 干预，卡死 worker 靠人工清理 | 60s tick 被调度 + LLM 端点可用 |
| 信号流 | 1022 条真实记录 | 是当前唯一可信的"蜂群行为观测层"，可作为讨论的事实基础 | 无（已工作） |
| 治理循环 | 60s tick 未实盘运行 | DIKW 提升/信息素衰减/独立验证全部休眠 → KB 质量维护缺位 | orchestrator 常驻 |
| 价值结论 | MARBLE: 蜂群>单agent；诊断类: 单agent>蜂群 | 讨论时应按任务类型分场景，而非"蜂群是否优于单 agent"一刀切 | 对照实验方法论已就绪 |

---

## 4. 不确定性（显式列出）

1. **待验证**: controller_decisions=0 的确切根因——是无法确认是否曾有 run 以长驻
   Orchestrator 模式运行；若公司侧从未启用该模式，则属"功能未部署"而非"功能故障"。
2. **待验证**: spawn_requests 151 条 pending 是否被 `expire_old_requests` 的 TTL(10min)
   清理过——若 orchestrator 从未 tick，则 151 条是跨多 run 的累积量，需按 run 分组复核。
3. **存疑**: F5 的 MARBLE 100/100 结果来自单一实验配置，未确认是否经过 P5 独立复现；
   BountyBench 的 n=3 波动本身即表明结论对样本量敏感。
4. **推断**: 当前实盘"蜂群"实际运行模式 = 公司 Router 派发 + agent_worker.py 单步执行，
   与文档所述 Orchestrator 常驻模式是两套并行路径；推断依据是 DB 中 agent_profiles
   均由外部生成（无 spawn_requests→fulfilled 血缘），非 100% 确认（待验证 agent_profiles
   的 created 来源字段）。

---

## 5. 修复建议（可落地，绑定证据链）

按讨论优先级排序：

1. **让闭环闭合（对齐 F2/F3）**：将 Orchestrator run_loop 接入公司侧常驻进程（或为
   spawn_requests 增加一个独立消费者），使 F2 的 151 条 pending 能被消费、F3 的
   Controller 能出决策。验证标准：新 run 中 spawn_requests 出现 fulfilled，controller_decisions
   出现首行。
2. **讨论任务分层（对齐 F5）**：用知识库已有对照实验作为讨论基准——并行覆盖型任务
   用蜂群，窄视野诊断型任务保留单 agent 或"先单 agent 后蜂群验证"的递进模式；
   避免在未区分任务类型的情况下讨论"蜂群好不好"。
3. **补元角色缺口（对齐 F6）**：Queen 的战略层（跨 run 仲裁/历史学习）与 Prophet
   （跨 run 模式提炼）是讨论中已确认的缺口；建议先在讨论中明确这两个角色由
   Controller 扩展还是新增独立组件承担。
4. **给信号流接消费端（对齐 F4）**：1022 条 worker_signals 是现成数据，建议先跑一次
   离线统计（按 agent 的 quality/novelty 分布），验证 Controller 的规则模式阈值
   (0.25/0.7/0.1) 在真实信号分布下是否合理——这是无风险、立即可做的验证步骤。
5. **修复建议均绑定证据链**：每项落地后以 DB 行数变化（spawn fulfilled 数、
   controller_decisions 行数、behavior emergence 数）作为闭合判据。

---

## 附: 本报告证据可复核性

- 所有代码锚点为 `src/swarm/` 与 `src/governance/` 实际文件行号，可 `git log` 追溯
- 所有 DB 数字来自 swarm_knowledge.db 实时查询（2026-08-13 会话内执行）
- 知识库条目引用: MARBLE 蜂群对照、BountyBench pilot、架构盲区五层、角色缺口分析
- 未新增任何发现，未执行任何外部探测；仅汇总既有代码/DB/知识库证据
