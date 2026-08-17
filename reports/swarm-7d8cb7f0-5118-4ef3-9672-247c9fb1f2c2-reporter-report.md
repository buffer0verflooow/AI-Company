# Swarm Run 7d8cb7f0 — Reporter 报告（终版：合入 2104b021 + 7147c687 + 974afc3a）

- run_id: 7d8cb7f0-5118-4ef3-9672-247c9fb1f2c2
- swarm_name: company-research-7_c108a6 | intent=research | 状态: running
- client_objective: 重新发起这个调研任务（原始调研问题见 §2.1）
- 角色: reporter | 模型画像: default-reporter-writer (client/writer)
- 本报告性质: **终版合入稿** — 已合入本 run 全部 3 条高置信知识：2104b021（researcher-01 四路线全景）、
  7147c687（researcher-02 技术细节/成熟度矩阵）、974afc3a（researcher-01 第二轮：六路线深挖 + H1/H2 裁定 +
  新证据 LLMxCPG/BinAbsInspector/Claroty Team82/VULTURE）。取代 14:13 中间态稿（3dc02bd4）与 14:25 主体稿。
  researcher 3/3 全部 completed，无在途研究任务（§2.6）。
- 元知识核验: 598e4a3e（@14:34:02，source_task=b60e5c2f，reporter-01）为本报告"主体稿→终版"更新 diff 的
  自捕获，非独立研究产出；已逐行比对，其全部 127 条 b 侧变更行均已在本文件中（0 缺失），合入即完成，
  本实例仅补记溯源（§6）。
- 元知识核验: f6afe8ed（@14:38:14，source_task=83b2fc1a，reporter-01）为 598e4a3e 合入工作流（读库脚本 +
  比对脚本 + 报告更新 diff + 结构化结果）的自捕获，非独立研究产出；其报告文件段 b 侧 4 行均已在本文件中
  （0 缺失），合入即完成，本实例仅补记溯源（§6）。
- 日期: 2026-08-12（更新于 14:35 前后，数据截至 14:27:11）

## 1. 结论摘要

1. **[HIGH] 调研产出全部落地（3/3 researcher）**：2104b021 @14:15:25、7147c687 @14:20:06、974afc3a
   @14:27:11 全部完成并入库（均 L3 tool_usage、trust_vector={logic 0.6, base 0.6, cross_validation 1.0}、
   pheromone=1.0、status=active）；无在途研究任务。此前中间态稿 "无新增研究产出" 已被取代；本稿即终版
   （§5-P0-1 由"待 52729f74 完成"改为"已完成"）。
2. **[HIGH] 研究核心结论（worker 自报、置信度原样保留）**：
   - 2025 主线 = **动态 fuzz + LLM**（coverage-guided fuzzer + sanitizer，LLM 做种子生成/变异导向/编排），
     AIxCC 决赛 7 队技术表佐证（置信度高，r2）；外部证据整体支持 H2（动态 fuzz 瓶颈=基础设施缺失），
     H1/H2 仍为推断（974afc3a 裁定，§2.14）；
   - 用户点名的**语法树/代码图方向** = 程序分析工具产出结构化信息（AST/CFG/PDG/CPG）→ LLM 做语义判断、
     查询生成、误报过滤，**不是独立方法**（置信度高）；二进制侧**无 AST 直接对等物**，对等物=反编译 IR
     （Ghidra P-code/LLVM IR）（974afc3a 明确表述，§2.14 路线 B/C）；
   - **反编译+SAST 是辅助而非主线**（推断，r1+r2 一致：LLM4Decompile v2 re-executability 46–65%；
     AIxCC 决赛队几乎未把"反编译后 SAST"当主力）；
   - **Agent 化端到端（CRS）是当前最强实证**：Big Sleep 2024-10 发现 SQLite 栈缓冲区下溢、2025-07 野外
     利用前拦截 CVE-2025-6965；AIxCC 决赛（2025-08-08）7 支 CRS 检出 54/63=86% 合成漏洞、修补 68%、
     另发现 18 个真实开源漏洞（4 源一致，974afc3a）；
   - **二进制 agentic 实证出现**：Claroty Team82（2026-06）Claude + Ghidra MCP 十分钟从 UPX 壳固件重建
     "已修复但细节未公开"漏洞（974afc3a，与"反编译→AI 分析"最贴的实践案例）；
   - **能力上限 = 语义恢复质量**：混淆/加壳/反编译失真时全链路失效（三源一致）。
3. **[HIGH] 三源合流**：两独立 researcher 三份交付核心结论一致（§2.15），证据集互补（r1 两轮：E1–E18 表 +
   LLMxCPG/IRIS/BinAbsInspector/Team82/VULTURE；r2: CQLLM/SAST-Genius/FuzzingBrain/AIxCC SoK）。分歧仅存
   数据口径（LLM4Decompile 87% vs 46–65%；LLMVulDecompiler 91.7% vs S&P 2024 负面评测），各报告均注明不可混用。
4. **[MEDIUM] 交付链路风险未消**：route_events 仍 suspected_dead（runner 心跳缺失）；29 条死信中 14 条
   "could not resolve original conversation" + **15 条微信渠道投递故障**（修正中间态稿 §2.8 的全量归因错误）。
5. **[LOW] E17 本地实证已核验成立**：binary-analysis-failure-modes.md 存在于
   `~/.hermes/skills/productivity/company-operations/references/`（66 行，7 失败模式逐条代码核实：
   6 成立/1 与代码矛盾，"~10% 准确率"成立；三螺丝修复=验证脱钩/前置/对抗接线，与自家 P5 replay 验证同向）。

## 2. 证据链（每条均可独立复核）

### 2.1 调研主题（原始问题）✅ 事实
- 来源: company_router.db route_events，session=20260812_171115_aa6fbe，09:12:15+00:00
  "检索一下当前利用AI进行二进制漏洞挖掘的方法有哪些？我要的是比如通过语法树、代码图等方式，要技术细节"
  （route=company/action=main_agent）。
- 判定: 该消息即研究问题本体；13:28:35 "重新发起这个调研任务" 仅重发指令，不含新问题。

### 2.2 误派为 recon（根因 1）✅ 事实（已修复）
- route_events 09:20:06+00:00 route=security/action=dispatch_swarm → run 49063587（company-recon-5_aa6fbe,
  intent=recon, completed, tokens=36438）；用户侧佐证 13:10:01/13:13:02 "只是一个研究任务…改路由"。
- 本次重发验证修复生效: route_events 1e907853（session=20260812_210117_c108a6）route=**research**,
  action=dispatch_swarm, confidence=0.84, reason="matched research product-line vocabulary" → 7d8cb7f0。

### 2.3 重发 run 首次尝试疑似死亡（suspected_dead）✅ 事实
- route_events 13:28:35 → run 7d8cb7f0；status=suspected_dead，error="no heartbeat; runner presumed dead
  after restart budget exhausted"（§2.6 快照时仍未解除）。

### 2.4 researcher 角色注册 CHECK 崩溃（根因 2）✅ 事实（已修复）
- 日志: `sqlite3.IntegrityError: CHECK constraint failed: role IN ('scanner','analyst','exploiter',
  'reporter','orchestrator','custom')` — 注册即崩 ×3。修复后 agent_profiles 已含 researcher-01/02
  （role=researcher, model=default-researcher-balanced）。

### 2.5 "调研跑完了吗" 误判重复派发 ✅ 事实（已修复）
- route_events 13:54:17 → 新 run 323d31af（同一调研双份执行）；善后 status=cancelled、任务 failed、
  result_summary 注明 "cancelled":"duplicate run - router misdispatch 2026-08-12"。
- 回归测试: test_company_router.py 新增 test_ma_question_does_not_redispatch，全量 248 tests OK。

### 2.6 当前运行状态（14:35 快照）✅ 事实
- swarm_runs.status=running（updated_at=14:08:26，tokens_spent=60753）；conversation_summary
  （14:27:11 后刷新）: completed/reporter=**2**, completed/researcher=**3**, running/reporter=1,
  pending/reporter=1；Knowledge: L3 tool_usage=**3**, L2 tool_usage=2。
- agent_tasks（run 7d8cb7f0）: 65650f7c researcher-01 **completed**（captured 2104b021）；
  8df7a3b7 researcher-02 **completed**（captured 7147c687）；**52729f74 researcher-01 completed**
  （captured **974afc3a**，14:20:06→14:27:11）；b60e5c2f reporter-01 running（14:27:11，本实例）；
  6a20b739 reporter pending（无 agent）；4 条 failed 为 run 323d31af 善后任务（§2.5）。

### 2.7 researcher-01 交付入库（知识 2104b021）✅ 事实
- 记录: id=2104b021-3128-4ad7-a51c-d585e4644fd3，L3 tool_usage，source_run_id=7d8cb7f0，
  source_task_id=65650f7c，created_at=2026-08-12 14:15:25，tags=["cve-2025-6965","ghidra"]，
  trust_vector={logic_soundness 0.6, base_confidence 0.6, cross_validation 1.0}，pheromone=1.0。
- task result_summary.artifact_verification: artifacts=[], ok=true — 交付为**文本入库**，无独立文件产物。
- 内容: 《AI 驱动的二进制漏洞挖掘方法全景（技术细节版）》（四路线 + E1–E18 证据表 + 建议）。
- 注: 入库条目 title/content 头部混入审批超时噪声（capture 机制原样收录 agent 输出），交付主体在尾部；
  本报告仅采信交付主体部分。

### 2.8 researcher-02 交付入库（知识 7147c687）✅ 事实
- 记录: id=7147c687-3501-4655-a88a-395fd87fdb02，L3 tool_usage，source_task_id=8df7a3b7，
  created_at=14:20:06，tags=["cve-2021-20294"]，trust_vector 同上（cross_validation=1.0）。
- 内容: 四维度技术细节深挖 + 路线成熟度矩阵 + 与公司现状交叉验证（对照 18890a06/26606fa7）+ 来源分级表。

### 2.9 研究内容（合入 2104b021，r1 原文分层标注）⚠️ 外部引用未独立复现
**路线 1 — 静态·反编译增强**（伪代码 + SAST/LLM 检测）: LLM4Decompile（v2 re-executability
9b=64.9%/22b=63.6%/6.7b=52.7%，GitHub 一手）[E2]；WPeChatGPT（GPT-4 处理伪代码）[E3]；LLMVulDecompiler
（漏洞源码↔汇编配对微调，8/8 检出/0 误报，小样本）[E3]；Mythos 灰盒（伪代码+原始二进制双持）[E4]；
LATTE（TOSEM'25，首个 LLM 静态二进制污点分析）[E8]。**瓶颈** = 伪代码失真链（反编译 → 语义丢失 → 静态分析误报）。
**路线 2 — 静态·图/嵌入**（用户点名方向）: 表示层 CFG/ACFG/AST/BDG/CPG（BCSD 综述，CMC 2025）[E1]；
模型层 GNN（Order Matters/VulHawk 用 ACFG）、LSTM/CNN 指令序列（BinDeep）、Transformer/MLM（PalmTree/
BinShot）；跨平台相似性检测（固件函数↔漏洞库比对，LSH/树索引）[E1]；漏洞检测 Asteria/Asteria-Pro、
VulHawk；LLM 增强 Bin2SrcSim；**局限**: 误报>30%（跨函数数据流上下文丢失）、LLM 嵌入非确定（复现性差）[E1]。
**路线 3 — 动态·LLM 辅助 Fuzzing**（用户原述路线 2）: KernelGPT（ASPLOS'25，LLM 综合 syscall 规范，
24 个内核 bug/12 修复/11 CVE，部分合入 syzkaller）[E5]；ChatAFL（NDSS'24，协议 fuzzing，深层状态覆盖
~50%+）[E4][E5 引文]；Fuzz4All/FuzzGPT/TitanFuzz/SyzAgent [E6]；机制共性 = LLM 语义先验保持输入合法前提
下的深度变异/导向。
**路线 4 — Agent 化端到端**（当前最强实证）: Big Sleep/Naptime（读代码/搜索/调试器/写脚本 + 迭代假设 +
variant analysis 启动点；2024-10 SQLite iColumn 哨兵值 -1 栈缓冲区下溢，同日修复；2025-07 结合 GTIG 在
野外利用前拦截 CVE-2025-6965）[E9][E10]；AIxCC 决赛（2025-08-08 DEF CON 33，63 合成漏洞；Team Atlanta
$4M / Buttercup $3M（28 漏洞+19 补丁）/ Theori $1.5M（LLM 成本约前两名一半）；参赛系统全部开源）[E11][E12]；
OpenAnt（2026 arXiv，190 可利用候选）[E13]；Vulnhuntr（Protect AI，零样本调用链分析，10+ Python 0day
宣称；仅 Python/依赖 LLM API）[E14]。
**分歧点（r1 诚实标注）**: Big Sleep 官方原文"目标特定 fuzzer 目前可能至少同样有效" vs AIxCC 63 漏洞全自动
发现+补丁（fuzzer 无法独立完成）——不矛盾：Agent 强语义/变体类，fuzzer 强覆盖驱动类。
**证据表**: E1–E18（学术综述/一手 GitHub/官方博客/中文综述/论文/本地实证分级）；其中 E7 被网关截断（中置信，
r1 自标"待验证"），E17 已复核（§2.12）。

### 2.10 研究内容（合入 7147c687，r2 原文分层标注）⚠️ 同上
**维度 1 — 反编译+SAST+LLM**: LLM4Decompile 可重编译 ~50%；"Can Neural Decompilation Assist Vulnerability
Prediction"（arXiv 2412.07538，DeBinVul 数据集）→ 判定: AIxCC 决赛队几乎未把"反编译后 SAST"当主力 →
该路线现阶段是**辅助而非主线**（推断，置信度中）。
**维度 2 — 静态分析+LLM**（语法树/代码图角度）: CQLLM（LLM 生成 CodeQL 查询）+ 奇安信引用工作；SAST-Genius
（IEEE S&P 2025，LLM 过滤 SAST 假阳性）；奇安信综述（AST/PDG 语句级依赖预训练、CFG 分解为执行路径、GNN
补图特征、程序切片）；Atum 污点分析天花板（source→sink 有效，查不出"缺失的检查"如越权）→ 判定: 语法树/
代码图 2025 正确姿势 = 结构化信息 → LLM 语义判断/查询生成/误报过滤（**置信度高**）。
**维度 3 — 动态 fuzz+LLM**: LLM 角色三层进化（①种子生成 SeedAIchemy/FuzzLGen ②变异/导向=覆盖引导+LLM 语义
反馈 ③编排指导 FuzzingBrain（2025-09，AIxCC 第 4 名，CWE 引导，28 漏洞含 6 零日、补 14；V2 强调 Verifier
组件））；AIxCC 7 队技术表（预编译语料/种子 Agent/Bootstrap/覆盖率阻塞/语义反馈/改进 sanitizer/字典/concolic/
Added C/JVM fuzzers；最主流 = "Fuzz 生成候选→LLM PoV 生成" 与 "LLM PoV→Fuzz 验证" 双向）；符号执行支线
（AutoBug/LIFT，探索期）→ 判定: 动态侧 2025 标配 = coverage-guided fuzzer + sanitizer + LLM 种子/导向/编排
（**置信度高**）。
**维度 4 — Agent/系统级**: AIxCC 决赛细节（143 小时、7 队、53 个 OSS-Fuzz 衍生 C/Java 项目、每队 $85K 云 +
$50K LLM credits；冠军 Atlantis 392.8 分 vs 亚军 TB 219.4 领先 ~80%，制胜 = **ensemble 多技术组合 + 全时段
在线稳定性**而非单一 LLM 突破；LLM 相对基础工具 PF 额外 22 个 PoV；因构建失败/定制 harness 错过可解 CPV）；
代码库级: VulEval（依赖图 RAG 跨函数上下文）、Vulnhuntr、Naptime；中文社区: 腾讯云 ReAct-ML（产品稿，低置信）、
奇安信破壳平台 VQL → 判定: 2025 前沿 = 多 Agent CRS（识别→验证→PoV→patch 闭环），LLM 语义/编排 + 执行反馈
落地（**置信度高**）。
**成熟度矩阵（r2）**: 反编译+SAST=中（需 GPU 模型，高误报）；静态+LLM=中（需 CodeQL，本机可行性高）；
动态 fuzz+LLM=高（已验证，需装 afl++）；符号执行+LLM=低（探索期，STRIDE 未构建）；Agent CRS=高（工程量大）。
**与公司现状交叉验证（r2）**: KB 18890a06（fuzz 基础设施缺失）→ 从"补 afl++ + LLM 种子生成"切入成本最低；
KB 26606fa7（readelf CVE-2021-20294 实证，蜂群 5/5 vs 单 agent 2/5）== AIxCC 的 LLM PoV 生成管线形态，
建议固化为 fuzz+LLM 路线第一块拼图；原报告 H2（fuzz 瓶颈=基础设施缺失）获 AIxCC 技术表印证（文献级，非实测）。

### 2.11 双交付交叉印证（r1×r2；三源合流见 §2.15）✅（结论层）/ ⚠️（数字层）
- 一致结论（两独立 researcher，不同证据集）: ①反编译+SAST=辅助非主线；②语法树/代码图=结构化信息喂 LLM 非
  独立方法；③fuzz+LLM=2025 主线；④Agent/CRS=前沿实证最强；⑤上限=语义恢复质量；⑥落地建议趋同（8 CVE 靶标
  对照实验，r1 称 P0-2、r2 复用 26606fa7 .symver 语料）。
- 分歧仅存数据口径（§1-3），两报告自洽处理。

### 2.12 E17 本地实证复核 ✅ 事实
- 文件: /home/pwn/.hermes/skills/productivity/company-operations/references/binary-analysis-failure-modes.md
  （66 行，2026-08-12，company-operations skill 附属参考）。
- 内容与 r1 引用一致: 用户外部分发测试 LLM 二进制分析 ~10% 准确率；7 条失败模式对照 reverselibrary 源码
  镜像逐条行级核实，**6 条成立、1 条与代码矛盾**（analysis-reviewer 角色存在但非流水线强制环节）；核心根因
  = 验证查库比对非脱钩复现（两个同样幻觉的 agent 互相印证 → 假发现被"验证"成真）、触发门槛高、无 LLM 对抗
  角色；三螺丝修复（验证脱钩化/前置化/对抗角色接线）与自家 P5 replay 验证方向同向。
- 判定: r1 的 E17 引用成立；"语义幻觉/工具盲信/假验证"为 LLM 二进制分析的实证失败模式，支撑 §2.9 路线 4
  的"独立对抗验证"建议与 §5-P1 复核建议。

### 2.13 投递死信（修正归因）✅ 事实
- /home/pwn/workspace/company/operations/runtime/delivery-dead-letters.jsonl: 29 条 =
  14 条 "retry exhausted: could not resolve original conversation" + 7 条 "Weixin send failed: Cannot
  connect to host ilinkai.weixin.qq.com:443 ssl:default" + 8 条 "Weixin send failed: iLink sendmessage
  rate limited; cooldown active for 30.0s"。
- **修正**: 中间态报告 §2.8 称全部 29 条为 could-not-resolve — 不准确；实际 15/29 为微信渠道投递故障
  （连接失败 + 限流冷却），仅 14/29 属会话解析缺陷。

### 2.14 researcher-01 第二轮交付（知识 974afc3a，任务 52729f74）✅ 事实 / ⚠️ 外部引用未独立复现
- 记录: id=974afc3a-f6e8-4d60-adac-b59e998c93d5，L3 tool_usage，source_task_id=52729f74-43b9-46f1-acf0-a348faf357be，
  created_at=14:27:11，tags=["cve-2021-20294","cve-2018-5333","ghidra","ssrf"]，trust_vector 同 §2.7
  （cross_validation=1.0），pheromone=1.0。与 2104b021 同源（均 researcher-01）：第一轮全景、第二轮深挖；
  与 7147c687（researcher-02）独立互补。
- 注: 本条 title 字段为 "[任务] ⏱ Timeout — denying command"（审批超时噪声，同 §2.7 机制），交付主体在
  content 中；本报告仅采信交付主体部分。
- 取证边界（原文自标）: web_extract 故障（SSRF 守卫误判 arXiv/MDPI 为内网地址）、agentkey 执行额度耗尽
  （402）、curl 需审批且无人值守→超时拒绝 → 证据 = 搜索结果多轮交叉（标题/摘要/正文片段），未全文精读部分
  论文；结论分级 HIGH=多源一致或官方一手，MEDIUM=单源或转述。
- 内容摘要（r1 原文六路线分层）:
  **路线 A 反编译→SAST**：LLMVulDecompiler（MDPI Electronics 15(1):8，微调输出语法合法 C 兼容伪代码直接喂
  源码级静态分析器 Tencent CodeAnalysis，12 个真实漏洞检出 11=91.7% vs 豆包 66.7%，唯一漏检 CVE-2018-5333
  空指针；MEDIUM 单源小样本）；工具谱系 LLM4Decompile/DecLLM/SK2Decompile/FidelityGPT/D-LiFT/PseudoFix；
  基准 DecompileBench、BinMetric（IJCAI 2025）；ACM 2025 反编译综述：编译是信息丢失过程，LLM 只能"依据语料
  模式提出合理猜测"。对本机含义：自研 STRIDE（Sleigh P-code 读取器）是同类地基，"伪代码→SAST"链路无需装 Ghidra。
  **路线 B 语法树 AST（用户点名）**：CSUR 2025 综述三类用法（AST 切函数级片段适配上下文窗口 / AST+自然语言
  注释→结构化注释树 SCT（SCALE）/ AST+CFG+DFG 多图 DefectHunter + 图注意力 VulnArmor/GRACE）；局限=AST 不含
  数据/控制流，必须配 CFG/DFG（拼进 prompt 可显著提升识别率）；**二进制侧无直接对等物**——二进制结构化表示
  =反编译 IR（Ghidra P-code/LLVM IR，ACM Cyber UCLA 做法：二进制转 LLVM IR 再喂 LLM）；量化：41.3% 的 LLM
  漏洞检测论文用代码处理技术适配上下文窗口，但 GPT 级模型出现后模型自身增益开始超过代码处理技术增益。
  **路线 C 代码图 CPG（用户点名）**：CPG=AST+CFG+PDG 统一图（Yamaguchi 2014/Joern）；2025 标杆 LLMxCPG
  （USENIX Sec 2025）：Qwen2.5-Coder-32B 微调生成 CPGQL 查询 → Joern 污点路径+反向切片（代码量削减
  67.84%~90.93%）→ QwQ-32B-Preview 分类，F1 比 SOTA +15.40%，对代码变换鲁棒、跨函数；局限（原文自述）=
  竞态/设计类缺陷难图遍历表达、CPG 静态本质抓不到动态行为；工业混合 IRIS（ICLR 2025）LLM+CodeQL 优于单独
  CodeQL；**二进制图路线 = 反编译 IR 上做污点/切片**：BinAbsInspector（360 KeenLab，Ghidra P-code + 污点/
  抽象解释，x64/armv7/arm64）、Zetier "CodeQL for binaries"（Ghidra+Joern 桥）、ONEKEY 二进制 0-day（商业）。
  **路线 D 动态 fuzz+LLM（本机 8 靶标最相关）**：分工四类（MDPI 2026 LLM-fuzz 综述）①种子/语料生成
  （Fuzzing BusyBox USENIX Sec 2024 真实 bug、SeedMind 灰盒种子效果与字典法相当、SeedAIchemy、ECG、LLAMAFUZZ）
  ②变异算子（MetaMut ASPLOS 2024）③结构化/协议输入（ChatAFL NDSS 2024 文法级变异，9 新漏洞 vs AFLNet 3/
  NSFuzz 4；LLMgSSA；GPT-4 协议 fuzz）④harness/驱动生成（PromptFuzz；Google OSS-Fuzz 官方转向 LLM 生成
  fuzz target + agent 化 build 脚本，2025 博客）；后处理 LLM crash 分类/修复（arXiv 2411.03346）；与本机
  关系：8 靶标全为解析器类 → 真入口在输入构造，缺 afl++/honggfuzz 是 H2 的瓶颈实体。
  **路线 E 二进制相似性/1-day**：传统 ML BinDeep/UniASM/αdiff；2025 VULTURE（NDSS 2025，补丁模式相似度做
  1-day 检测）、NeurIPS 2025 通用 coder LLM 转二进制嵌入（BCSD）；对本机 8 CVE 是现成路线（全有公开补丁）
  但需二进制差分工具链。
  **路线 F 端到端 agentic**：AIxCC 决赛实证（HIGH，4 源一致）：7 支 CRS 分析 5400 万行源码、检出 54/63=86%
  （半决赛 37%）、修补 68%（半决赛 25%）、另发现 18 个真实开源漏洞（Team Atlanta 6 个）；架构样板：Trail of
  Bits 亚军 CRS（漏洞发现+上下文分析+补丁生成 7 个独立 AI agent+验证四组件）、Theori 季军 Branch Flipper
  （覆盖率反馈 LLM 突破 fuzz 阻塞点）；SoK arXiv 2602.07666；**二进制 agentic 实证**：Claroty Team82
  （2026-06）Claude Opus 4.6 + CLAUDE.md 方法论注入 + Ghidra MCP，对 UPX 壳固件 ipstweb 十分钟内发现多个
  "已修复但细节从未公开"漏洞——LLM 从二进制独立重建漏洞证据；工具化 OGhidra 3（LLNL）、DecompAI（驱动
  gdb/ghidra/objdump）、NCC Group AI vs SAST 对比实验；威胁面 GTIG 2026（攻击者用 AI 做 0-day 利用）、
  LLM agent 自主利用 25% 1-day / 13% 0-day（MEDIUM 转述）。
- **H1/H2 裁定（r1 原文）**：外部证据整体支持 **H2**（解析器类漏洞主入口=输入构造/fuzz 语料，与本地
  readelf 实证同向）；H1/H2 仍是推断，未被外部数据替代——外部研究测的是源码级召回，二进制级无同口径数据。
- 分歧点（r1 显式列出）: 图结构 vs 纯 LLM 上下文（CSUR 称模型增益>代码处理技术增益，LLMxCPG 称 +15.4%；
  PrimeVul 指出流行漏洞数据集 38%~64% "漏洞"标签实际有误 → 双方评测口径不可直接对比）；反编译伪代码价值
  （LLMVulDecompiler 91.7% 实证 vs ACM 综述语义精度有限）；LLM 种子边际价值（SeedMind"与字典法相当" vs
  BusyBox/ChatAFL"显著收益"，差异在目标复杂度）；静态 vs 动态优先级（AIxCC 高检出来自源码级静态+agentic，
  不可外推为二进制级）。
- 建议（r1 原文）: ①H2 优先：补 afl++（apt/源码）+ pwncollege wrapper 作 harness + LLM 种子语料，复用
  26606fa7 .symver 构造；②STRIDE 按 BinAbsInspector 模式定位（P-code IR + 污点/抽象解释），承接路线 A/C；
  ③路线 E 低成本增量（nm/objdump 层补丁模式匹配，无需 Ghidra）；④AST 本机不立项（8 靶标无源码路径）；
  ⑤蜂群自身即路线 F 形态，AIxCC 开源 CRS（Team Atlanta/Trail of Bits 已开源）值得拉取作架构对照。
- 参考来源（974afc3a §7 分级清单，完整可复核 URL 见 KB 原文）: 一手/官方 = DARPA AIxCC 结果公告 2025-08-08
  （darpa.mil，86%/68%、18 真实漏洞、Team Atlanta 夺冠）、aicyberchallenge.com 决赛数据、Google OSS-Fuzz LLM
  研究页（target_generation + agent 化 build）、KeenSecurityLab/BinAbsInspector（GitHub）；权威会议（摘要级核验）=
  LLMxCPG（USENIX Sec 2025）、ChatAFL（NDSS 2024，9 vs 3/4 漏洞）、Fuzzing BusyBox（USENIX Sec 2024）、IRIS
  （ICLR 2025）、VULTURE（NDSS 2025）、MetaMut（ASPLOS 2024）、Fuzz4All（ICSE 2024）、SoK AIxCC（arXiv
  2602.07666）、LLM 反编译综述（ACM 2025）、LLMs in Software Security（CSUR 2025, arXiv 2502.07049）；媒体/实践 =
  Claroty Team82 "Hands Free"（2026-06）、Trail of Bits AIxCC 复盘（2025-08）、LLMVulDecompiler（MDPI Electronics
  15(1):8，MEDIUM 单源）；本地基线沿用 run 49063587 已验证结论（KB 18890a06/26606fa7/1cbe302d，非本 run 新证据）。

### 2.15 三源合流交叉印证 ✅（结论层）/ ⚠️（数字层）
- 三方一致（2104b021 + 7147c687 + 974afc3a，两独立 researcher 三份交付）: ①反编译+SAST=辅助非主线；
  ②语法树/代码图=结构化信息喂 LLM 非独立方法，且二进制侧无 AST 直接对等物、对等物=反编译 IR（974afc3a
  给 P-code/LLVM IR 明确表述）；③fuzz+LLM=2025 主线；④Agent/CRS=实证最强前沿；⑤能力上限=语义恢复质量；
  ⑥H2（fuzz 基础设施缺失）获两轮独立外部证据支持；⑦落地趋同：8 CVE 对照实验 + 复用 .symver 语料 + 补 afl++。
- 数字口径（不混用）: AIxCC 合成漏洞"63 个"（赛题对象总数，2104b021）vs "检出 54/63=86%"（974afc3a）——
  同一事件不同指标；奖金 $8.5M 三甲（2104b021：Atlanta $4M/Buttercup $3M/Theori $1.5M）与检出/修补率/18 真实
  漏洞（974afc3a）互补不冲突；冠军队名 Atlanta（2104b021/974afc3a）= Atlantis（7147c687）同一队（392.8 分
  vs 亚军 219.4）。
- LLMVulDecompiler 91.7%：r2 与 974afc3a 同源引用（MDPI 12 样本单源），两轮均标 MEDIUM 单源，未升级置信度。
- 新增佐证密度：974afc3a 的 LLMxCPG/IRIS/BinAbsInspector/Team82/VULTURE 与 7147c687 的 CQLLM/SAST-Genius/
  FuzzingBrain 在"代码图路线=IR 级污点/查询生成"与"fuzz+LLM=2025 标配"上互证（独立证据集 → 结论层置信度
  可上移，数字层仍待独立复核）。

## 3. 影响评估

| 影响面 | 说明 | 前置条件 |
|---|---|---|
| 调研交付 | **终版可交付**：六技术路线 + 语法树/代码图技术细节 + 成熟度矩阵 + 二进制 vs 源码级能力差距 + 落地路径（fuzz+LLM 切入 + 路线 E 增量），用户原始问题（含"技术细节"要求）已获实质回答；3/3 researcher 完成 | 无需前置（本稿即终版） |
| 结果可送达性 | route_events suspected_dead + 29 条死信（14 会话解析 + 15 微信渠道故障）→ 调研结果可能静默丢失 | 人工确认本 run 投递 / 修复 resolve_session_origin / 微信渠道恢复 |
| 研究质量风险 | 外部数字（KernelGPT 24 bug、AIxCC 63/$8.5M、LLM4Decompile 46–65% 等）为 worker 自报、未独立复现；三源交叉印证缓解单源风险，但对外发布仍需 P5 式复核 | 网络授权放开后的独立复核 |
| 落地路径 | 动态 fuzz+LLM 是 2025 主线且公司从"补 afl++ + LLM 种子"切入成本最低；readelf 实证（26606fa7）可直接固化为第一块拼图；LLM4Decompile 6.7B v2 消费级 GPU 可跑，可对比 STRIDE | 8 CVE 靶标编译产物就绪 + afl++ 安装 |

## 4. 不确定性（显式列出）

- **外部引用未独立复现**：本报告所有外部数字（E1–E18 及 r2 来源分级表、974afc3a 六路线）均为 researcher
  自报并引用一手/二手来源，本 reporter network=false 未做 P5 式独立复核；仅 E17（本地文件）与
  §2.7/2.8/2.13/2.14（本地 DB/文件直读）为本报告直接核验。
- H1（反编译→SAST 静态召回 <40%）仍为推断；H2（动态 fuzz 瓶颈=基础设施缺失）获 AIxCC 技术表文献级印证
  但非本机实测（r2 原文标注）。
- LLM4Decompile 87%（secrss 综述）vs 46–65%（GitHub v2 re-executability）— 口径不同（re-compilable vs
  re-executable），两报告均注明不可混用。
- LLMVulDecompiler 91.7% 声称 vs 奇安信引 S&P 2024（"最先进模型也未准备好用于实际检测"）— 受控数据集 vs
  真实项目口径差异，双方均不可直接作结论。
- 中文社区一手综述缺失：腾讯云 ReAct-ML/奇安信破壳为产品宣传稿（r2 低置信），仅作佐证。
- AIxCC 冠军归因（ensemble+稳定性）为 SoK 作者分析结论，官方计分不含崩溃后恢复权重（r2 原文标注）。
- 8 个 CVE 靶标除 readelf/libpng 外 6 个编译产物就绪性 — 未 shell 核验（审批拦截），本 run 未复核。
- STRIDE 引擎反编译质量 — 未构建未运行，无法评估。
- 三份交付（2104b021/7147c687/974afc3a）的外部数字均为 researcher 自报、本报告未独立复现；974afc3a 自标
  arXiv 2505.22010（VulBinLLM）与 2602.07666（SoK AIxCC）未全文精读（web_extract SSRF 守卫 + agentkey 额度
  耗尽），细节以摘要/第三方转述为准。
- 数据质量隐患（974afc3a 引 PrimeVul）：流行漏洞数据集 38%~64% 的"漏洞"标签实际有误 → 图结构 vs 纯 LLM
  的评测对比（CSUR vs LLMxCPG）口径不可直接比较。
- 1 个 reporter 任务 pending（6a20b739，无 agent）：本终版合入后若再派发属冗余派发（参照 §2.5 防重复模式）。
- 投递: route_events status=suspected_dead 未解除（§2.3、§2.13）。

## 5. 修复建议（绑定证据链）

1. **P0-1（HIGH）: 本稿即终版，直接交付**。3/3 researcher 完成（§2.6），三源合流（§2.15）；无在途研究
   任务，无需再等。证据: §2.6 任务状态 + §2.7/2.8/2.14 交付。
2. **P0-2（HIGH）: 人工确认本 run 结果投递**。suspected_dead（§2.3）+ 29 条死信（14 会话解析 + 15 微信渠道
   故障，§2.13）说明两类投递缺陷并存；调研完成后结果可能静默丢失。证据: §2.3、§2.13。
3. **P1（MEDIUM）: 对外发布前对关键外部数字做 P5 式独立复核**（需 network 授权）。至少复核三个一手来源：
   KernelGPT（ASPLOS'25，24 bug/11 CVE）、AIxCC 官方（63 漏洞/奖金）、Big Sleep（SQLite 0day/CVE-2025-6965）；
   复核后替换 §4 的"未独立复现"标注。证据: §2.9/2.10、§4。
4. **P1（MEDIUM）: 落地 P0-2 对照实验** — pwncollege-build 8 个 CVE 靶标上跑"静态基线 vs 动态基线
   （afl++ + LLM 种子）"，起点复用 26606fa7 的 .symver 构造语料；用实测替换 H1/H2 推断（§4）。证据:
   §2.10 交叉验证、r1 建议、§2.7/2.8。
5. **P2（LOW）: 静态侧验证 CodeQL 流水线**（LLM 生成查询 + LLM 过滤误报，CQLLM/SAST-Genius 路线）；红线 =
   纯 LLM 读伪代码不投生产。证据: §2.10 维度 2。
6. **P2（LOW): LLM4Decompile 6.7B v2 与 STRIDE 引擎对比反编译质量后再决定投入**。证据: §2.10 维度 1 +
   成熟度矩阵。
7. **P2（LOW）: 监控 6a20b739**（pending reporter），若重复派发按 §2.5 模式去重；本终版合入后不再需要
   额外 reporter 任务。证据: §2.4、§2.6。
8. **P1（MEDIUM）: STRIDE 引擎按 BinAbsInspector 模式定位**（P-code IR + 污点/抽象解释，x64/armv7/arm64），
   承接路线 A/C 的二进制图能力，避免依赖外部反编译器。证据: §2.14 路线 A/C。
9. **P1（MEDIUM）: 路线 E（1-day 补丁差分）低成本增量**：8 靶标全有公开补丁，VULTURE 式"补丁模式→相似性"
   可在 nm/objdump 层做最小版（无需装 Ghidra），直接验证 H1。证据: §2.14 路线 E。
10. **P2（LOW）: 语法树路线（AST）本机不立项**（8 靶标无源码路径）；如建源码级能力，LLMxCPG 的 CPGQL 微调
    路线为当前最优。证据: §2.14 路线 B/C。
11. **P2（LOW）: 拉取 AIxCC 开源 CRS 作架构对照**（Team Atlanta/Trail of Bits 已开源）：本蜂群形态与 CRS
    同构（发现/分析/验证组件化），作为公司安全探索产品线架构参照。证据: §2.14 路线 F。

## 6. 范围与边界声明

- 证据来源: swarm_knowledge.db 直读（2104b021/7147c687/**974afc3a**/**598e4a3e**/**f6afe8ed**/3dc02bd4/18890a06/26606fa7、swarm_runs、agent_tasks）、
  company_router.db route_events 直读、delivery-dead-letters.jsonl 直读、
  company-operations skill references/binary-analysis-failure-modes.md 全文、
  reports/swarm-49063587-reporter-report.md（中间态稿已通读）、test_company_router.py 测试结果。
- 本报告执行手段: 仅本地文件/KB 只读查询（execute_code 内嵌 sqlite3）+ 报告文件写入；未做外部探测、未发
  网络请求、未执行 shell 命令、未写知识库、未发布。
- 本报告不新增发现：研究内容类结论全部引自 researcher-01/02 交付原文（KB 2104b021/7147c687/974afc3a），
  保留其"置信度高/中/低/推断/待验证"标注；本报告新增的仅为运行状态事实与证据核验结论（§2.6–2.8、§2.12–2.14）。
- 冲突处理: 全部 researcher 任务已完成（§2.6），无在途冲突源；若后续新增交付与本报告冲突，以新证据为准
  并注明差异（§4）。
