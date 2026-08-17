# AI 安全攻击面梳理报告（reporter 交付）

> Run: company-worker (reporter) | 日期: 2026-08-12
> 角色: reporter — 汇总已验证证据、区分事实/推断/存疑、影响评估绑定证据链
> 任务意图: 攻防不分家 — 先理清 AI 安全的攻击面，再反推防守方案
> 证据标记: ✅=本机可核验（KB/技能/公司文档）｜⚠️=单来源或引用待核｜❌=未验证或反证
> 更新: 2026-08-12 — 合入高置信知识 KB f83f0731（scanner-02 第 3 轮补盲交付, L3, cross_validation=1.0）＋ KB 3a1c4e34（scanner-01 实体资产扫描, L3, 单来源, reporter 已逐行代码复核）

---

## 0. 结论摘要（TL;DR）

**攻击面已从「模型层」迁移到「Agent 行动层」——这是最高优先级的结构性判断。**
LLM 不再是纯文本生成器，而是持有工具、记忆、凭证、自主执行能力的行动者，攻击面因此扩展为八面：**输入面、模型面（降级）、记忆/上下文面、工具面、身份面、供应链面、通信面、输出面**。其中「模型降级」与「记忆投毒」为侦察交付（KB e4cf0962）新合入的攻击面，均有内部实测证据。

按已验证证据强度排序的攻击面优先级：
1. **CRITICAL — AI 网关/基础设施劫持**（LLM Heist + LiteLLM 供应链事件，2026 年真实发生，非理论）
2. **CRITICAL — Agent 身份与凭证滥用**（69% 凭证共享，事件率相差 22.6pp）
3. **HIGH — Prompt 注入**（实际评估中约 45% 发现占比，工具/数据可达）
4. **HIGH — 工具滥用/过度授权（Excessive Agency）**（合法工具非预期使用，可达 RCE/越权）
5. **MEDIUM — 供应链投毒（模型/依赖/CI-CD）**（单点污染、多点爆炸）
6. **MEDIUM — 输出面未消毒**（LLM 输出被下游 XSS/SQLi/命令注入消费）
7. **MEDIUM — 模型降级攻击**（scanner 独立排序 P5 差异化位：分类器误报→静默降级→旧攻击复活，中文内容市场空白）
8. **MEDIUM — 记忆/上下文投毒（ASI06）**（攻击链已实测：PoisonedRAG 90% 操控、空库更易写入）

**自有系统是最近、最可验证的攻击面（本次合入新增）** — 公司 swarm-knowledge 蜂群 2026-08-11 审计实证 8 项漏洞（F1-F8，KB df15f698），此前攻击面地图完全缺失「自家攻击面」这一块；另补托管式 Agent 运行时信任边界（ASI07 通信面的具体实例）。实体资产扫描（KB 3a1c4e34）进一步补齐两个维度：**已验证加固面 5 项**（S1-S5，非新发现）与 **新缺口 M1/M2/L1/L2** — 其中 **M1 conversation_summary 间接注入链是 F1-F8 之外的独立新发现**（本机代码行全链实证）。详见 §1 E9/E10/E11。

**防守方案必须从攻击面反推**，核心原则：规划/执行分离、最小权限身份、全量遥测、输出消毒、爆炸半径控制。详见 §5。

---

## 1. 证据链

### E1. ✅ 攻击面分类框架 — OWASP LLM Top 10 v2.0 + ASI 2026
- **来源**: 本地技能 `llm-security` → `references/owasp-llm-top10.md`（zhaoxuya520/reverse-skill，本机可读）
- **判定标准**: 权威行业框架，可直接作为攻击面清单
- **关键内容**:
  - LLM Top 10: LLM01 Prompt Injection / LLM02 敏感信息泄露 / LLM03 供应链 / LLM04 数据模型投毒 / LLM05 输出处理不当 / LLM06 Excessive Agency / LLM07 系统提示词泄露 / LLM08 向量嵌入弱点 / LLM09 幻觉误报 / LLM10 无界消耗（DoS/Denial-of-Wallet）
  - ASI 2026（Agentic）: ASI01 目标劫持 / ASI02 工具滥用 / ASI03 身份权限滥用 / ASI04 Agentic 供应链 / ASI05 意外代码执行（RCE 链）/ ASI06 记忆上下文投毒 / ASI07 不安全 Agent 间通信 / ASI08 级联故障 / ASI09 人类信任操纵 / ASI10 Rogue Agents
- **实测数据分布**（⚠️ 单来源，来源未标注具体出处，对外引用前需核）: LLM01 ~45% / LLM06(敏感泄露) ~20% / LLM08(过度授权) ~15% / 其余 ~20%

### E2. ✅ CRITICAL — LLM Heist：劫持 LiteLLM AI 网关（embracethered.com 2026-08-03）
- **来源**: Swarm KB 条目（knowledge_type=tool_usage, L2），已翻译整理并发布公众号 2026-08-09
- **判定标准**: 一手安全研究来源 + 公司已发布
- **关键内容**:
  - AI 网关 = 密钥集中点 + 流量中继点 → **AI 基础设施中攻击收益最高的目标**
  - 四种对抗目标: ① IP/数据窃取（截获上下文/PII/机密）② 未授权推理（盗用凭证烧账户）③ 响应伪造与工具调用注入（向 AI 客户端注入文本/工具调用）④ 模型蒸馏与行为克隆
  - 攻击面: 网关即中间人 — 流量重路由、截获、修改一站式
  - 防御缺口: 网关侧缺乏流量完整性监控是普遍现状

### E3. ✅ CRITICAL — LiteLLM 供应链攻击：2500 家企业 / 43.4 万条 CI/CD 流水线暴露（FreeBuf 2026-08-11）
- **来源**: Swarm KB 条目（security-intel 情报采集捕获），已发布公众号 2026-08-11
- **判定标准**: 情报采集捕获 + 公司已发布
- **关键内容**: AI 网关的供应链攻击 = 单点污染、多点爆炸；CI/CD 流水线是放大器（流水线里的密钥即提款机）。**2026 年真实事件，非理论。**

### E4. ✅ HIGH — Agent 身份与凭证现状（VentureBeat Pulse Research, 107 企业, 2026-06）
- **来源**: `company/projects/security-exploration/ai-agent-security-pilot-content-brief.md`（2026-07-21 就位）
- **关键数据**:
  - 18% 确认发生 AI 代理安全事件 + 36% near-miss = **54% 企业已中招**（注意: 是叠加值，非纯确认）
  - 仅 32% 给每个代理独立限域身份；**69% 存在凭证共享**
  - 有凭证共享的企业事件率 **63.5%** vs 全独立身份 **40.9%** — 相差 22.6pp（⚠️ 推测性差异，全独立身份组仅 22 家样本，非因果证明）

### E5. ✅ HIGH — 采用与控制剪刀差（ETR 2026 State of Security Study, 517 企业）
- 37% 已部署/测试 AI 代理（2025 年 27%），仅 **3%** 有大规模代理专用安全控制，**20% 完全无控制**

### E6. ✅ HIGH — 可见性危机（CSA State of AI Cybersecurity 2026, 1500+ 安全领导者）
- **92%** 缺乏 AI 代理身份全面可见性；**95%** 怀疑自己能否检测/控制被攻陷的代理

### E7. ✅ 治理/攻防双信号（公司战略评估，2026-08-08 已核验）
- **来源**: `company/strategy/ai-security-direction-evaluation.md`（reporter 交付，c61e5c0d）
- CISA + NSA + 五眼对 agentic AI 发布联合公告（政府级行动信号）
- OWASP ASI 2026 已进入实战: 安全内参《面向 AI 智能体的红队测试实战》（secrss.com/articles/90244）
- CSA《The AI Agent Governance Gap》点名治理框架空白

### E8. ✅ HIGH — 侦察交付合入：模型降级面 + 记忆投毒面（scanner-01 → KB e4cf0962, L3）
- **来源**: Swarm KB 条目 `e4cf0962-fbf2-4c3b-aa2d-3c03ffb972a1`（scanner-01 侦察交付, L3 tool_usage, 2026-08-12 本 run 交叉验证）；本机 wiki 已发布文 `company/wiki/articles/fable5-model-downgrade-memory-attack.md`（KB 条目 da16d2ca）
- **判定标准**: 本机 wiki 文章可读（✅ 本机可核验）+ KB L3 条目
- **新攻击面 A — 模型降级（层 2 模型面）**:
  - 攻击链: 触发 forbidden topic → 安全分类器误报 → **静默降级 Opus 4.8** → 旧攻击复活（Fable5 文内部实测, community issue #66728）
  - 核心结论: **模型降级 = 攻击面开关** — 降级把新模型防御带回旧水平；攻击者只需找到降级触发，无需新漏洞
- **新攻击面 B — 记忆/上下文投毒（层 3, ASI06）**:
  - 记忆劫持链: 对抗图 → 工具调用劫持 → 虚假记忆持久化 ×4（Fable5 文内部实测）
  - PoisonedRAG: 百万语料中 5 篇恶意文档 → 90% 操控成功率（⚠️ 内部实测/单来源）
  - 反直觉点: **空记忆库更易写入**（降门槛效应）
  - 图片谜题输入劫持记忆工具 ASR 5/10（Fable5 文内部实测）
- **工具面补充**: MCP 服务器通常比 memory 工具更易被劫持（通用工具方差大，本线实测观察）
- **scanner 独立优先级排序**（P0-P5，与 §0 交叉印证）: P0 注入+工具滥用 / P1 身份凭证 / P2 网关供应链 / P3 记忆投毒 / P4 输出注入 / **P5 模型降级（差异化位，中文内容市场空白）**
- **scanner 已确认未验证项**（勿写结论）: Mordor Intelligence CAGR 数字（URL 截断，与 §3 一致）、ASI 攻击占比分布（来自 skill 参考非一手）

### E9. ✅ 合入 — 自有蜂群基础设施实证漏洞（F1-F8）：「自家攻击面」是地图最大盲区（scanner-02 → KB f83f0731, L3）
- **来源**: Swarm KB 条目 `f83f0731-1f34-4c02-8b68-20f21bcaab49`（scanner-02 第 3 轮补盲交付, L3 tool_usage, 2026-08-12, cross_validation=1.0）+ KB `df15f698-109e`（2026-08-11 蜂群安全审计, L1, cross_validation=1.0, 已核验存在）
- **判定标准**: 本机内部审计实证 + KB 交叉验证（两条独立条目互相印证）
- **关键内容**: 2026-08-11 蜂群安全审计实证 8 项自家漏洞，前两轮 scanner 交付与既有报告均未纳入：

| ID | 发现 | 对应 ASI 2026 |
|---|---|---|
| F1 | 知识库投毒→L4蒸馏→规则注入 Agent 提示，全链路已实证（priority=88 活跃规则进 spawn 上下文） | ASI06 记忆上下文投毒 |
| F2 | build_task_context/信号板将 KB 原文与指令纯文本拼接，无数据-指令隔离，executor 输出经 summary 回流成间接注入环 | ASI01/ASI06 |
| F3 | tool_policy(shell/network/write) 零强制，executor 以用户全权限运行 | ASI02 工具滥用 |
| F4 | 自动验证管道靠内容正则计分（IP/CVE/工具名），可伪造 | ASI10 Rogue Agents |
| F5 | agent 身份自报无所有权校验（claim/complete 可冒用） | ASI03 身份滥用 |
| F6 | spawn reason/run_id 模板插值未校验格式 | ASI03 |
| F7 | swarm_knowledge.db 0644 同机可读 | 数据面 |
| F8 | USER_CORRECTION FTS5 未 sanitize 可崩溃（已复现 OperationalError） | DoS 面 |

- **意义**: 自家系统 = 活体靶场，8 项实证发现比任何外部框架引用都强；「AI 安全攻击面 → 防守方案」的最短验证路径就是从 F1-F8 的修复做起（蒸馏人工闸门、数据-指令隔离、executor 沙箱、claim 令牌）
- **蜂群 × ASI 2026 能力对照表（草案）**（方向评估 §4 的 24h 动作，原材料已由本交付补齐；正式表待 analyst 落成）:
  - 蜂群强项: ASI06（KB 投毒链已验证，有检测经验）、ASI08（signals 循环检测）、ASI10（P5 verifier 独立复核）、ASI01（任务图/判据化验证）
  - 蜂群盲区: ASI04（MCP/依赖供应链检测缺失）、ASI07（Agent 间通信无加密认证）、ASI09（无人类信任操纵检测）、ASI03（F5 身份校验缺失是硬伤）
  - 双栏结论: 蜂群既是「检测工具」也是「被测对象」— 对照表应双栏：能力映射 + 自省映射

### E10. ⚠️ 合入 — 托管式 Agent 运行时信任边界（KB 24d523e4, L2, 单来源）
- **来源**: Swarm KB 条目 `24d523e4-5ce3-4094-93d9-de19a30d9d7a`（托管式 AI Agent 架构课件提炼, L2 tool_usage, 2026-08-12, 已核验存在；⚠️ cross_validation=0.0，单来源）
- **关键内容**:
  - Messages→Agent SDK→Managed Agents 三层演进，**每层托管什么就新增什么可攻击信任边界**
  - 事件协议（send/receive 8 类）= Agent 通信攻击面的标准词汇表
  - 会话状态机 idle/running/rescheduling/terminated = **恢复点是攻击面**（「可恢复 ≠ 结果正确」）
  - 工具执行与 loop 分离 = MCP/自定义工具是独立信任域
- **意义**: 补强通信面（ASI07）与运行时面的架构依据 — 此前报告仅引用 ASI07 名称而无实例

### E11. ✅ 合入 — 实体资产攻击面扫描：已加固面 S1-S5 ＋ 新缺口 M1/M2/L1/L2（scanner-01 → KB 3a1c4e34, L3）
- **来源**: Swarm KB 条目 `3a1c4e34-cc1d-4cd1-ac30-75cf4903fe43`（scanner-01 实体资产扫描交付, L3 tool_usage, 2026-08-12 本 run；⚠️ cross_validation=0.0 单来源 — 但 reporter 本次已对全部关键代码行**逐行复核**，行号证据全部实存）
- **判定标准**: 本机只读代码审计（零外部探测，未写任何文件）；复核文件：worker.py / model_config.py / orchestrator.py / signal_board.py / command_executor.py / _safe_io.py
- **一、已验证加固面（S1-S5，非新发现 — 堵上的洞也要上地图）**:
  - S1 ✅ KB 内容注入 worker context → 已隔离（worker.py:472 content[:800]、:501 事件 content[:500] 均包 mark_untrusted）
  - S2 ✅ KB title→reason 链 → 已净化（orchestrator.py:299 sanitize_single_line 防换行构造第二段指令）
  - S3 ✅ MCP 供应链面 → 配置期固定 + allow 白名单 + 一次性 spawn（worker.py:561 仅注入静态配置）
  - S4 ✅ executor 子进程 → 超时保护 + 环境门 + 凭据清洗（command_executor.py 基于 proc.run_capture_async, 默认超时 1800s, 无 shell=True；company/automation/_safe_io.py:98 scrub_environment 正则去密钥 env）
  - S5 ✅ 凭证存储 → 源码零硬编码（src/ 下 api_key 硬编码 grep = 0）
- **二、新发现缺口（全带证据行号，reporter 已复核）**:
  - **M1【中-高】conversation_summary 间接注入链 — F1-F8 之外的独立新发现**: model_config.py:356 事件 content[:200] 拼入 summary → :360 写入 swarm_runs.conversation_summary → worker.py:454 原样注入下游 worker 上下文（"## Run Summary" 段, [:1200] 截断），**未包 mark_untrusted**（对比同文件 472/501 行均已标记）。攻击链: 恶意文本经 capture/agent 输出进事件表 → 进 summary → 注入下一个 agent 提示。缓解: 截断（拼接处 200 / 注入处 1200）+ 事件源多为内部结果 → 属二阶注入，需前置污染
  - **M2【中】signal_board 黑板未标记（级联信任面）**: signal_board.py Graph goal[:400] 与信号值直接拼接无标记 → worker.py:441-443 注入所有 graph-affiliated 任务。攻击链: 单个被注入的 probe 节点 → 污染整个 graph 后续 worker 上下文
  - **L1【低】Task Tool Allowlist 段直接拼入**: worker.py:549-552 — task_tools 来自 focus_params（KB 派生），未过单行净化
  - **L2【低】标记策略为「读取时包裹」而非「入库时包裹」**: capture.py 入库原始文本；安全依赖每条读取路径不漏标 — M1/M2 即为漏网证据
- **记录在案（非缺口）**: security_intel.py 11 个 RSS/API 源（freebuf/krebs/arxiv/CISA KEV）外部不可信文本进 KB，下游读取标记已覆盖（S1）；agentkey_radar_probe.py 搜索输出经 _sanitize_text 截断入库
- **意义**: ① 攻击面地图补齐「已加固」维度 — 不是所有面都裸奔，S1-S5 已有控制；② M1/M2 是「防守方案可落地的具体修复点」，比概念面更可直接写进产品线 backlog；③ 蜂群自身已是六面攻击面的活样本（输入面 capture、供应链面 MCP、工具面 executor、身份面凭据清洗），可作公众号素材实证

---

## 2. 影响评估（谁受影响 / 可达成什么 / 前置条件）

| 攻击面 | 受影响方 | 可达成危害 | 前置条件 |
|---|---|---|---|
| AI 网关劫持 (E2) | 自建 AI 网关的企业 | 数据窃取(PII/机密)、凭证盗用烧账户、响应伪造/工具注入（客户端无感）、模型蒸馏 | 网关无监控、密钥集中存放 |
| 供应链/CI-CD (E3) | 依赖 AI 网关的全部下游 | 密钥泄露→提款机、部署链路连锁沦陷 | 依赖树无扫描、流水线密钥明文 |
| 身份/凭证 (E4-E6) | 部署 Agent 的企业 | 越权操作、横向移动、被攻陷代理不可检测 | 凭证共享(69%)、无独立限域身份、无沙箱 |
| Prompt 注入 (E1) | LLM 应用/RAG/Agent | 数据泄露、工具调用越权、目标劫持、RCE 链(ASI05) | 外部内容可达上下文（网页/PDF/邮件） |
| 输出面 (E1) | 消费 LLM 输出的下游系统 | XSS/SQLi/命令注入/SSRF | 输出未消毒即渲染/执行/查询 |
| 模型降级 (E8) | 依赖安全分类器路由模型的企业 | 旧漏洞复活、防御静默回退到旧模型水平 | 分类器可被诱导误报（forbidden topic 触发降级） |
| 记忆投毒 (E8) | RAG / 长期记忆系统 | 虚假记忆持久化、检索结果被操控、后续决策污染 | 记忆库可写（空库更易）、检索链可污染 |
| 自有蜂群基础设施 F1-F8 (E9) | 公司蜂群/知识库运营 | 规则注入→Agent 行为劫持；凭证级 shell 滥用；验证管道伪造；身份冒用 | 无（✅ 审计实证，F1-F8 当前即存在） |
| 自有蜂群 M1/M2 注入链 (E11) | 蜂群全部下游 worker | 事件文本经 summary 间接注入下一个 agent 提示（M1）；单个被注入的 probe 节点污染整个 graph 上下文（M2） | 二阶注入：需先污染事件表/信号板；但当前无标记=无防线，一旦污染即可达 |
| 托管 Agent 运行时 (E10) | 使用 Managed Agents 的企业 | 会话恢复点重放、事件流劫持、工具信任域越权 | ⚠️ 课件提炼（单来源，cross_validation=0.0） |

**共性前置条件**: 无遥测（工具调用/记忆/通信未记录）、无人在回路审批、权限未最小化。

---

## 3. 不确定性（显式列出，不掩盖）

- ⚠️ **E1 实测数据分布（45%/20%/15%）来源不明** — 技能 reference 未标注具体出处，对外引用前需核原始评估报告
- ⚠️ **E4 22.6pp 差异为推测性** — 全独立身份组仅 22 家，趋势明显但非因果证明；「54% 中招」是确认+near-miss 叠加值
- ⚠️ **E7 Mordor Intelligence 市场数字** — URL 因交付截断不完整，对外引用前需复核（方向评估报告已标注）
- ⚠️ **攻击现实可能滞后于炒作** — Reddit r/cybersecurity: "Has anyone actually had a security incident caused by an AI coding agent yet?"；若无大规模公开事件，需求验证周期可能拉长
- ⚠️ **「蜂群 × ASI 2026 能力对照表」原材料已齐、正式表待落成** — 方向评估 §4 的 24h 动作：scanner-02 补盲交付（f83f0731）已给出能力映射+自省映射草案，但正式对照表文档尚未产出，需 analyst 接力落成（从 §3 ❌ 升级为 ⚠️ 待落成，不再是完全缺失）
- ⚠️ **E8 内部实测数字为单来源** — ASR 5/10、PoisonedRAG 90%、记忆劫持 ×4 均出自 Fable5 内部实测文（本机 wiki 可核验，但非第三方独立复现）；对外引用须标注「内部实测」
- ⚠️ **E9 F1-F8 为单次内部审计** — df15f698 与 f83f0731 相互印证（cross_validation=1.0），但均为公司自审、非第三方独立复现；F3（tool_policy 零强制）等项待修复验证后复核
- ⚠️ **E11 (3a1c4e34) 单来源 scanner-01 自审，攻击链无 PoC** — cross_validation=0.0；reporter 已复核全部关键代码行 ✅ 实存（worker.py:454/472/501/549-552、model_config.py:356/360、orchestrator.py:299、signal_board.py goal[:400]、command_executor.py、_safe_io.py:98），但「实际触发」未复现 — M1 缓解因素：拼接处 200 / 注入处 1200 字符截断 + 事件源多为内部结果；M2 需先注入 probe 节点。修复后应补 P5 复验
- ⚠️ **E10 托管运行时为单来源课件提炼** — KB 24d523e4 cross_validation=0.0，三层演进/事件协议/状态机恢复点均出自课件，对外引用前需补第二来源
- ⚠️ **P5 验证状态** — 本报告证据为情报/调研/框架类，非可复现漏洞，无法用独立 curl 复现；KB 条目为单来源采集（已发布公众号=公司级背书，但非独立复现）

---

## 4. 修复建议（防守方案从攻击面反推）

| 攻击面 | 防守控制 | 绑定证据 |
|---|---|---|
| 输入面 (Prompt 注入) | 所有自然语言输入（含检索内容）视为不可信；规划与执行分离（解释意图的模型 ≠ 执行动作的模型） | E1 (LLM01/ASI01) |
| 工具面 (Excessive Agency) | 最小权限工具集；人在回路审批（紧迫感话术需二次确认）；工具参数消毒 | E1 (LLM06/ASI02/ASI09) |
| 身份面 (凭证共享) | 每代理独立限域身份；绑定身份/目的/范围/时效；高风险代理沙箱隔离 | E4-E6 (63.5% vs 40.9%) |
| 供应链面 (网关/依赖) | 网关流量完整性监控；密钥泄露监控；AI 依赖树扫描；CI-CD 密钥轮换 | E2, E3 (LLM Heist + 供应链事件) |
| 通信面 (Agent 间) | 加密+认证；防重放；通信内容纳入遥测 | E1 (ASI07) + E10（事件协议/状态机实例） |
| 模型面 (降级) | 降级事件监控与告警；降级后强制重评估防御基线；分类器误报审计 | E8 (Fable5, issue #66728) |
| 记忆面 (投毒) | 记忆写入防线（写入前校验/审核）；记忆库权限模型；空库初始化即最小权限 | E8 (ASI06, PoisonedRAG) |
| 输出面 | 渲染/执行/查询前消毒；下游系统不信任 LLM 输出 | E1 (LLM05) |
| 自有蜂群基础设施 (F1-F8) | 蒸馏人工闸门（F1）；数据-指令隔离（F2）；executor 沙箱 + tool_policy 强制（F3）；验证管道改为人工复核（F4）；claim 令牌/所有权校验（F5/F6）；DB 权限收紧 0644→0600（F7）；FTS5 sanitize 全覆盖（F8） | E9 (df15f698/f83f0731 审计实证) |
| 自有蜂群 M1/M2/L1/L2 (E11) | worker.py:454 conversation_summary 包 mark_untrusted（M1）；signal_board 渲染时包 mark_untrusted（M2）；task_tools 条目过 sanitize_single_line（L1）；capture 入库即包裹、读取层标记降为第二道（L2 纵深防御） | E11 (3a1c4e34 代码行实证，可 P5 复核) |
| 托管 Agent 运行时 | 恢复点校验（可恢复≠结果正确）；事件流加密+认证+防重放；MCP/工具信任域隔离 | E10 (24d523e4, ⚠️单来源) |
| 全局 | 记录一切 — 工具调用/记忆/通信作为一等安全遥测；熔断/回滚/紧急停止优先 | E1 防御原则 |

**公司落点建议（关联既有战略）**:
1. **「蜂群 × ASI 2026 能力对照表」原材料已齐，建议 analyst 落成正式文档** — 方向评估 §4 的 24h 动作：f83f0731 已提供能力映射（ASI06/08/10/01 强项）+ 自省映射（ASI04/07/09/03 盲区）草案，落成后作为产品立项文档与防守产线可测项基线
2. **自家系统 F1-F8 修复 = 最短验证路径** — 蜂群既是检测工具也是被测对象；修复计划（蒸馏闸门→数据-指令隔离→沙箱→claim 令牌）可作为 AI 安全产品首个可交付的防御验证案例
3. 工具实验形态 = AI 网关安全检测（流量异常/密钥泄露/响应完整性校验）— E2+E3 产品信号已合并
4. 内容线已就位（LLM Heist 8/9、LiteLLM 供应链 8/11 已发布公众号），继续按攻击面逐面产内容

---

## 5. 交付物

- 本报告: `/home/pwn/workspace/company/strategy/ai-security-attack-surface-report.md`
- 底层证据: Swarm KB（LLM Heist / LiteLLM 供应链条目、scanner-01 侦察 e4cf0962-fbf2、scanner-01 实体扫描 3a1c4e34、scanner-02 补盲 f83f0731、蜂群审计 df15f698、托管运行时课件 24d523e4、Fable5 条目 da16d2ca）、`llm-security` 技能 `references/owasp-llm-top10.md`、`company/wiki/articles/fable5-model-downgrade-memory-attack.md`、`company/projects/security-exploration/ai-agent-security-pilot-content-brief.md`、`company/strategy/ai-security-direction-evaluation.md`、蜂群源码（worker.py / model_config.py / orchestrator.py / signal_board.py / command_executor.py / _safe_io.py）

## 6. 边界声明

- 本报告只汇总既有已验证证据，未新增发现、未做任何外部探测（无授权目标）。
- 本次更新合入高置信知识 KB f83f0731（L3, cross_validation=1.0）与 KB 3a1c4e34（L3, 单来源）；3a1c4e34 全部关键代码行已由 reporter 逐行复核（✅ 实存），攻击链「实际触发」无 PoC，已在 §3 如实标注；其引用条目 df15f698（审计）与 24d523e4（课件提炼）均已核验存在；E10/E11 为单来源（cross_validation=0.0），已在文中显式标注 ⚠️。
- 所有 ⚠️ 项为单来源或引用待核，对外引用前须复核原始出处。
