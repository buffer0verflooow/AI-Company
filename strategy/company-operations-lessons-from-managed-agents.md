---
tags: [department, strategy, operations, lessons]
created: 2026-08-12
source: 飞书课件《从零构建第一个托管式 AI Agent》(evidence/managed-agents-course-2026-08-11/)
---

# 从"托管式 Agent"课件看公司运营:运行时责任边界启示录

> 来源: Claude Managed Agents 视频课件(8 单元)。表面是 Agent 工程, 内核是**运行时责任边界**——"哪些责任被托管, 哪些仍由你负责"。公司运营(蜂群+产线+知识闭环)与它是同构的:公司本身就是一个"托管式 Agent 系统"。

---

## 0. 一句话

**课件问的是"模型调用与生产 Agent 之间的责任边界",公司该问的是"自动化与人工之间的责任边界"——哪些运营责任被托管(脚本/Agent 执行),哪些仍必须由人负责(审批/发布/战略判断)。这个边界画错了, 规模和风险同时失控。**

---

## 1. 三层演进映射:公司的"Messages → SDK → Managed"在哪

课件: Messages API(原始能力) → Agent SDK(可编程 harness) → Managed Agents(运行时托管)。

公司对应:
| 课件层级 | 公司对应物 | 现状 |
|---|---|---|
| Messages API | 裸工具调用(脚本、curl、单个工具) | ✅ 大量存在, 一次性脚本 |
| Agent SDK | 自动化管线(company_operator / content_hermes_executor / security_intel) | ✅ 已成型, 有进度/状态 |
| Managed Agents | **公司整体运营** —— 谁托管"每日该做什么" | ⚠️ **缺口**: company_operator 已有雏形(自主循环→机会队列→运营账本), 但"哪些责任托管、哪些留人"没有成文边界 |

**课件原话映射**: "开发者提供任务、配置和领域工具逻辑; 托管 harness 负责把它跑起来的运行时。"
**公司版**: 人提供战略方向、选题判断、审批授权; 自动化负责采集、撰写、分类、入库、记账。

**具体动作**:
1. [x] 写 `operations/runtime-responsibility-matrix.md`: 每个运营动作标 [人]/[自动化]/[人审自动化] — 2026-08-12 完成, 六类 40+ 动作; 核心结论: 唯一硬性人工点 = 公众号正式发布/付费/删除/外部提交; 其余自动化或人审自动化。文档含 3 个已知边界缺口(executor 误报时序/蜂群报告对外审批点/自动修复范围)
2. 现有清单(初步): 采集=全自动; 撰写初稿=自动化+人审; 发布=人(外部动作); 产品方向收敛=人+蜂群调研辅助。

---

## 2. 三件套映射:Agent / Environment / Session = 选题池 / 工作区 / 任务实例

课件: Agent(定义) / Environment(执行空间) / Session(一次持续任务, 绑定两者+资源)。

公司对应:
| 课件原语 | 公司对应物 | 说明 |
|---|---|---|
| Agent | 选题池条目(赛道/通道/素材源/商业化意图) + company_operator_config | 定义"它是什么、能做什么" |
| Environment | 公司工作区(company/ 目录、凭证、网络策略) | 工具真正运行的空间; 网络/凭证边界要显式 |
| Session | content-jobs 目录、cron job、swarm run | 一次具体任务, 绑定 Agent 定义 + 环境 + 资源(素材包/知识库) |

**课件关键洞察**: Agent ID / Environment ID / Session ID 不是同一生命周期; 资源(素材)通过 Session 挂载, 不混进 Agent 长期定义。

**公司现状对照**:
- ✅ 已做到: content-jobs 每个任务独立目录(≈Session 隔离); 素材包独立于选题池存放(资源与定义分离)
- ⚠️ 缺口: **没有统一的"Session 生命周期"视图** —— 任务从"待定→撰写→QA→发布→入库"的状态流转靠人脑/文件碎片记忆, 没有显式状态机

**具体动作**: 给 content-jobs 加统一状态字段(见 §4), 让"哪些任务在跑/卡在哪/已终止"一眼可见。

---

## 3. "脑—手"解耦: 规划与执行分离 = 委派铁律的理论基础

课件: Agent loop(推理循环)与工具执行(沙箱)分离 —— 凭证隔离更清、执行器不与循环绑死、按需启动。

公司对应: 主 Agent(规划/判断) vs 委派执行(Codex/Claude CLI/蜂群 worker)。**用户已有"委派铁律"(委派任务时主 Agent 严禁动手)——这正是"脑手解耦"的运营版**。

**课件补充的洞察**: "工具执行端保持最小接口: 输入工具名和参数, 返回结构化结果或明确错误。"
**公司版**: 委派出去的任务要有清晰的输入契约(目标/上下文)和输出契约(结构化结果), 失败要返回"明确错误"而不是静默成功。

**公司现状**:
- ✅ 已做到: 委派铁律、任务包(context 自包含)
- ⚠️ 缺口: 委派失败的"错误形状"不统一 —— 有的返回报告, 有的返回半成品, 有的超时静默。应统一为"明确错误: 失败原因 + 已做部分 + 建议重试方式"

**具体动作**: 委派任务模板加"失败返回格式"约定(参考课件 handle_tool 的"未知工具返回明确错误, 不静默成功")。

---

## 4. 事件流与状态机: 公司最缺的一层

课件: Session 说的事件(send/receive 协议) + 状态机(idle↔running→rescheduling→terminated)。"一次请求只返回最终答案无法表达正在查什么、失败在哪; 事件流才可观察、可恢复。"

**公司现状**: 
- content-jobs 有 progress.json(15%→100%) 和 status.json —— **有状态, 但状态语义不完整**: 没有 rescheduling(重试)状态, 没有 terminated 的原因记录, 没有"事件日志"(每个阶段发生了什么)
- cron job 有执行状态(executed/ok/error) —— 有
- **缺统一可观测性**: "本周公司做了什么"无法从系统里查, 只能翻文件/问记忆

**课件金句**: "把事件追加到日志, 而不是只保存最终答案。出了问题才能解释它看过什么。"

**具体动作**:
1. content-jobs 状态机补全: `pending → running → qa → review → published → archived`, 加 `retrying`(失败重试)和 `terminated`(不可恢复, 记录原因)。
2. 每条任务事件落一个 `events.jsonl`(阶段变更/QA 结果/发布状态), 与 progress.json 并存。
3. 月度复盘直接查事件流, 不靠回忆。

---

## 5. context engineering: 产出质量的主杠杆(公司最强洞察)

课件: "开发者花大量时间决定上传哪些文件、怎样组织它们、工具返回什么证据 —— 这是 context engineering。**上下文工程是 Agent 质量的主要杠杆**。"

**公司对应**: 给 Worker/蜂群的素材包(source.md)、选题池的约束、风格规范、知识库检索结果 —— 全是 context engineering。

**公司已实践的**:
- ✅ 素材包化(evidence/ 每篇文章独立素材包, 含事实+出处+写作约束)—— A9/C11 都是这个模式
- ✅ 知识库闭环(已发布文章→wiki→capture→蜂群 KB)—— 上下文跨任务复用
- ✅ 选题池约束(赛道/商业化意图/时效窗口)

**课件补充的洞察**: "上传更多数据不一定更好。上下文要可检索、可解释、可控。"
**公司版**: 素材包不是越厚越好 —— 每个素材包应有"事实层(带出处)+ 约束层(写作要求)+ 边界层(不许虚构/必须验证)"的结构, 而不是原文堆砌。

**具体动作**: 素材包模板化 —— 统一三区块结构(事实/约束/边界), 让每个 Worker 拿到的是"工程化上下文"而不是"资料夹"。

---

## 6. "可恢复 ≠ 结果正确": 验证铁律的理论根基

课件: "恢复的是会话和事件上下文, 业务动作仍需幂等性、权限和人工校验。"

**公司对应**: 产线 QA 四门(Gate 1 事实核查→Gate 4 预览) + 蜂群 P5 验证(独立 Agent curl 复现)。**公司已有验证铁律, 课件提供了理论依据**: 恢复上下文 ≠ 业务正确, 必须独立验证。

**课件补充的洞察**: "把推荐动作直接变成自动修复"是误区 —— 权限、回滚、审批、影响面要单独设计。

**公司现状**: ✅ 已内化(验证铁律、approval 门、发布需人工)。

---

## 7. 一次一个扩展: 产品孵化的节奏控制

课件: "能力越多, 权限和上下文边界越复杂; 扩展应一次只引入一个, 并配套可观测性和回滚。"

**公司对应**: 产品孵化管线(信号→验证→实验品→付费)—— 当前 3 个产品信号(AI 网关检测/模型降级监控/EDR 评估)。

**课件洞察**: Subagents 不是加数量就行(要定义父子任务契约); Memory 不是自动变聪明(要有保留/删除规则); Vaults 不消除权限设计。

**公司版**: 
- 产品孵化一次只推 1 个实验品, 配套"验收标准 + 回滚条件"(课件 Outcomes rubric 思路)
- 蜂群扩展同理: 新角色/新机制一次一个, 先定验收(参考 MARBLE 100/100 模式)

---

## 8. 边界意识: 演示 ≠ 生产(公司内容与产品的诚实性)

课件: "视频没有展示或证明的内容也要写在边界上" —— 真实 Datadog 接入、自动修复、生产部署、性能 SLA 全部标注"需另行验证"。

**公司对应**: 公众号文章里的演示结论、蜂群战绩、产品信号 —— 都要标注边界: 哪些是"演示观察", 哪些是"生产结论"。

**公司已实践的**: ✅ A9 文章明确标注"视频演示中的结论, 不是生产事实证明"; 选题池素材源可追溯。

**课件补充**: 这个边界意识本身就是**内容差异化** —— 中文安全圈普遍缺这种诚实性标注。

---

## 9. 立即动作清单(按优先级)

1. [x] 写 `operations/runtime-responsibility-matrix.md` — 2026-08-12 完成, 15 项动作矩阵 + 边界决策规则
2. [x] content-jobs 状态机补全(pending/running/qa/review/published/archived + retrying/terminated + events.jsonl) — 2026-08-12 完成:
   - `automation/content_job_state.py` — 人机交互状态推进工具(show/review/publish/archive/retry/terminate), 转换合法性校验, 每次转换写 events.jsonl + lifecycle.json
   - `content_hermes_executor.py` — 新增 append_event(), 关键节点(started/worker_finished/completed/terminated)记事件, 完成后状态=review
   - `scripts/wechat_push_v2.py` — 推送成功后自动 publish 转换
   - `automation/backfill_job_states.py` — 历史 57 个 job 回填(completed→review, failed→terminated)
   - 查看: `python3 automation/content_job_state.py <job_dir> show`
3. [x] 素材包模板化: 事实/约束/边界 三区块统一结构 — 2026-08-12 完成: `marketing/evidence/TEMPLATE.md` + 4 个素材包补头(byoedr/llm-heist/ucpd/a9), C11 为样板; article-production.md 已挂规范
4. [x] 委派任务模板加"失败返回格式"约定 — 2026-08-12 完成: agent-roster.md §委派失败返回格式(原因/已做/重试 + 5 铁律)
5. [ ] 月度复盘改用事件流数据(替代人脑回忆)
6. [~] 产品孵化收敛: 3 信号收敛决策已定(首选=模型降级监控, 弃选处置 + 验收标准 + 回滚条件), 见 `strategy/product-incubation-pipeline.md` — 实验品开发待用户确认方向
