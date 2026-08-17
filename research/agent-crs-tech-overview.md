# AI 二进制漏洞挖掘调研：Agent CRS 技术全景与落地路线

> 调研日期：2026-08-12
> 来源：AIxCC SoK 论文（arXiv 2602.07666）、FuzzingBrain 论文（arXiv 2509.07225）、
> Team Atlanta ATLANTIS 技术报告、Trail of Bits Buttercup 开源文档、DARPA 官方评分指南、
> 蜂群 run 7d8cb7f0（知识 2104b021/7147c687/974afc3a）
> 状态：持续维护中（后续讨论会继续追加）

---

## 1. Agent CRS 概念与历史

CRS = Cyber Reasoning System（网络推理系统），DARPA 概念：全自动漏洞发现-修补系统。

| 代际 | 时间 | 形态 | 冠军 |
|---|---|---|---|
| CGC 2016 | 第一代 | 无 LLM，确定性程序分析（符号执行+concolic） | Mayhem（ForAllSecure，$2M） |
| AIxCC 2025 | 第二代 | LLM 时代，agentic CRS | ATLANTIS（Team Atlanta，$4M） |

AIxCC 决赛（2025-08，DEF CON 33）：7 支 CRS，143 小时无人值守，53 个挑战项目
（48 计分 + 5 无 harness），24 个 OSS-Fuzz 仓库（14C+10Java），63 个 CPV。
检出 54/63=86%（半决赛 37%）、修补 68%、另发现 25 个 0day（10 项目，48% 被修补）。
每队 $85K 云 + $50K LLM credits。7 支全部开源。

结构性变化：LLM 从"辅助工具"升级为"编排核心"，agentic CRS 开源化。

## 2. 通用管线

```
任务（源码仓库）
  → 构建（多 sanitizer：ASAN/UBSAN/CFI）
  → 发现（fuzzer 集群 + LLM 加速）
  → PoV 去重（orchestrator）
  → 根因分析（LLM + 程序模型上下文）
  → 补丁生成（多 agent 协作）
  → 补丁验证（重跑 fuzzer + 构建检查）
  → 输出（PoV + 已验证补丁）
```

## 3. 三强架构

### ATLANTIS（Team Atlanta，冠军，$4M）
- Kubernetes 分布式，CP-MANAGER 编排漏洞生命周期
- 引擎：ATLANTIS-Multilang（LLM 上下文感知字典，71.2% 已验证 PoV）、
  ATLANTIS-C/Java（多 fuzzer ensemble + Java sink 中心分析）
- 补丁：8 个不同策略 patcher agent 并行（ensemble），5 节点
- 数据：1003 PoV → 118 验证通过 → 47 补丁（87.2% 成功）→ 43/70 漏洞、31 正确补丁、4 个 0day
- 定制 LLM 用 GRPO 微调"多轮代码上下文检索"

### Buttercup（Trail of Bits，亚军，$3M）
- 理念：混合方法，AI 用在适合 AI 的小子问题上
- 发现：libFuzzer+Jazzer + AI 输入生成器加速；上下文：tree-sitter+CodeQuery 程序模型
- 补丁：7 个独立 agent 流水线（RCA→fix 策略→patch 创建→reflection）
- 验证：每补丁重跑 fuzzer + 构建检查
- 数据：28 漏洞、19 补丁、90% 准确率、219 分、$181/分、只用非推理 LLM
- 开源版可跑在笔记本

### Branch Flipper（Theori，季军，$1.5M）
- 覆盖率反馈的 LLM 突破 fuzz 阻塞点
- LLM 成本约为前两名一半

## 4. SoK 教训（arXiv 2602.07666，KF1-9）

- KF1：赢比赛 = 研究+工程+策略三平衡；稳定性是最基本要求（3 队因稳定性崩盘）
- KF2：分数会骗人——低分可能是能力差距也可能是宕机/策略；TI 被准确率惩罚 16.3 分掉到第三
- KF3：LLM 补覆盖引导 fuzzing 短板——delta 定向检测（15/22）和约束重场景
  （regex/URL 编码/Unicode 归一化/zlib/符号链接混淆/数学守卫）
- KF4：管线健壮性是隐形瓶颈（构建破坏、1TB 磁盘、复现标准不一致、错误 sanitizer、去重粒度过粗）
- KF5：未解漏洞 = 推理缺口（多步加密变换）或 fuzz 管线局限（timeout 去重坍缩）
- KF6：单 agent 基线（Claude Code/MultiRetrieval）38-46% 语义错误率
- KF7：高准确率 CRS 压到 16-21%（multi-PoV 验证+补丁后 fuzzing+reflection）
- KF8：补丁失败常是部署时配置权衡（AT 30 分钟超时被 wireshark 构建吃光）
- KF9：补丁没解 = 推理缺口（ReDoS）或管线局限（严重依赖 crash stack trace）

## 5. 评分设计（衡量能力）

S_Challenge = AM × (S_PoV + S_Patch + S_SARIF + S_Bundle)

- 权重 = 给开发者省/费的时间：Patch 最高、PoV 次之、SARIF 最低、Bundle 走极端（+7 或罚）
- 时间衰减：立即提交满分，拖到最后一刻减半
- 准确率乘数（AM）：90% 几乎不罚；50% 减 6%；40% 减 13%（非线性鼓励准）
- 补丁验证：最小补丁集 + 特异性优先 + 必须修复所有队的所有 PoV
- 防污染：CPV 多为手工合成模仿历史 N-day；8 个无 CPV 挑战测 0-day

## 6. AIxCC 技术全景（两条互补发现管线）

### 6.1 Fuzzing Pipeline 组件
| 技术 | 说明 | 队数 |
|---|---|---|
| 赛前语料库 | ClusterFuzz/OSS-Fuzz/GitHub 种子配对 harness | 5 |
| 种子生成 agent | LLM 分析 harness 推断输入格式，写 Python 脚本产输入 | 6 |
| 解决覆盖率阻塞 | LLM 生成输入突破平台期 | 4 |
| 变异器/生成器 | LLM 生成变异器/文法（testlang/libFDP/Nautilus） | 3 |
| 语义反馈 | LLM 生成 IJON 式标注 | 1（SP） |
| 改进 sanitizer | 增强 Java sanitizer | 2 |
| 字典生成 | AT 实时 LLM、SP AFL++dict2file+CodeQL、42/LC 自建 | 4 |
| 定向 fuzzing | AT 自定义距离、42 LLVM/WALA 切片到 sink | 2 |
| 并行 fuzzing | 全队多实例+语料同步 | 7 |
| 新增 C/JVM fuzzer | AFL++/libAFL/自研 | AT/SP/42/LC |
| Concolic 混合 | SymCC（C）+ 自研 Java 引擎 | AT |

### 6.2 LLM-Based PoV 管线
- 两步：Bug 候选识别过滤（LLM+CodeQL/Semgrep/Infer+sink 列表）→ PoV 生成 agent
- 独特过滤：TI 用 logprobs 置信度；SP/LC 加权投票
- 3 队分解 reach agent（驱动到 sink）+ exploit agent（构造触发）
- 4 队注入 CWE 特定引导
- 提交策略：尽快提交唯一 PoV（重复只罚时间不罚准确率）

### 6.3 补丁管线
```
loop([RCA] → Generate → Validate) → Dedup → Submit
```
- 架构：Multi-Arch（AT 8 patcher ensemble）/ Multi-Agent（TB RCA→策略→创建→反思）/
  Single-Agent（FB 23 策略、42 16 组超参、LC DSPy 模型升级）
- 验证：构建+PoV 复现+项目测试（全队）；提交前多 PoV 重验（AT/FB/TB）；LLM-as-Judge（AT/FB/SP）；
  post-patch fuzz（FB/SP）；构建缓存（AT/SP）
- 最小补丁集：AT/TB/SP/42 计算；无 PoV 补丁（TI/FB/LC）延迟提交

### 6.4 FuzzingBrain 23 个独立策略（90% vibe-coded，第 4 名）
- 构成：12 Full-scan + 8 Delta-scan + 2 SARIF + 1 Unharnessed
- Full-scan PoV 策略：xs0_c_full/xs0_java_full（调用图剪枝）、xs1/xs2_java_full（增强）、as0_full（整合）
- Delta-scan：xs0_delta、as0_delta（PoV）；patch_delta、patch0-3_delta、xpatch_delta（补丁）
- SARIF：sarif_POV0、xpatch_sarif；Unharnessed：generate_fuzzer（自动生成 harness）
- 模型：5 个前沿 LLM（claude-3.7、gpt-4o、claude-opus-4、o3、gemini-2.5-pro），失败自动 fallback

## 7. 调用图剪枝（FuzzingBrain 核心策略详解）

**思路**：全库扫描时候选几千个函数，LLM 不可能逐个分析。
剪枝把搜索空间缩到"fuzzer 可达 + 高嫌疑"，再让 LLM 聚焦。

三步：
1. 静态分析枚举 fuzzer 入口可达函数（剪枝）
2. LLM 打分排序（语言特定漏洞模式，C/C++ 和 Java 各一套）
3. 只对高分函数做 PoV 生成（要调用路径 → 定向提示词）

Static Analysis Service 三种查询：
1. Function Metadata：函数参数 + 完整源码
2. Reachability：入口可达的所有函数（名字/文件/起止行号）
3. Call Paths：入口→目标完整调用路径（上限 20 条；深度 C/C++ 50、Java 10）

实现：
- C/C++：LLVM bitcode → SVF 构建调用图 → BFS 构建路径（并行）
- Java：CodeQL 数据库 + 两条定制查询（处理重载/动态加载）

关键洞察：
- 剪枝利用"漏洞必在可达代码里"的确定性强约束，剪掉不可达零损失
- 防幻觉：缩到可达子集后 LLM 只能在范围内判断
- 调用路径 = "怎么让执行到达漏洞点"的路线图，比孤立函数名强得多
- 实证：FuzzingBrain 的 PoV 几乎全部来自 LLM 策略，libFuzzer 只贡献 1-2 个
- 反面教训：exhibition 烧光额度，复盘发现大量花在"到不了漏洞代码的 fuzzer Worker"上

## 8. 二进制调用图生成方法

### 8.1 工具对比
| 工具 | 类型 | 调用图能力 | 间接调用 |
|---|---|---|---|
| IDA Pro | 商业 | GenCallGdl/GenFuncGdl API | 弱（heuristics） |
| Ghidra | NSA 开源 | 自带 Function Call Graph + 脚本 API | 中（类型恢复辅助） |
| angr | 开源 | CFGFast/CFGEmulated | 强（符号执行，慢） |
| radare2/rizin | 开源 | agc/agCd（JSON/dot） | 中，轻量 |
| Binary Ninja | 商业 | API 干净 | 中上 |
| Dyninst | 开源 | 重写库+运行时 | 动态解析 |
| QEMU/PT/gdb 追踪 | 动态 | 真实执行路径 | 最准但覆盖执行过的路径 |

### 8.2 核心难点：间接调用解析
- 直接调用（call 0x401234）：静态可见，所有工具都能处理
- 间接调用（call rax）：目标运行时才定，来源 = 函数指针表/虚表/回调/PLT-GOT/switch 表
- 学术方法：类型分析（TypeAnalysis）、语义图匹配（SemanCall）、多层混合（iResolveX 2026，
  论文明说 Ghidra/IDA/angr 精度落后于学术方法）
- 保守近似：call [reg] 连到所有可达入口函数（宁可多不可漏）

### 8.3 落地三档
1. 轻量：readelf/nm/objdump 符号表 + PLT/GOT 分析（直接调用 + PLT 解析，零依赖，快）
2. 标准：rizin `aaa; agCd` 或 Ghidra headless（P-code + 类型恢复，批处理）
3. 精度：angr CFGEmulated 或 Dyninst（慢但间接调用最强，适合小靶标）

**注意**：FuzzingBrain 是源码级调用图（SVF/CodeQL）。8 个 CVE 靶标有源码的
直接抄源码级方案（CodeQL 比二进制级更准）；纯二进制才需要二进制侧方案。

## 9. 与蜂群对照

- 蜂群本身 = 路线 F 形态（scanner/analyst/exploiter/reporter = 发现/分析/验证/报告）
- P5 验证铁律 = multi-PoV 验证 + 补丁后 fuzzing
- 差距：CRS 有确定性工具闭环（fuzzer+sanitizer 证伪机器），我们靠独立 curl 复现
- Buttercup 7-agent 补丁流水线值得抄进 exploiter 角色
- 评估体系可抄 AIxCC 分层：检出 ≠ 修复 ≠ 判断 ≠ 归组

## 10. Tai-e 评估结论（2026-08-12）

**问题**：Tai-e（https://tai-e.pascal-lab.net/）能否应用于 C/C++ 分析？

**结论：不能直接应用。** 证据：
1. 官方定位："a new static analysis framework for Java"（README 原文），GitHub 标题 "for Java and Android"
2. 输入是 Java 字节码（OOPSLA'25 论文讲新 bytecode frontend），C/C++ 无字节码
3. 核心分析全部绑定 JVM 语义：指针分析对象模型（Java 引用/类层级/虚分派）、污点分析（Java source/sink）、IR 是 Soot 系 Jimple 风格
4. 唯一沾 C 的：ICSE'25 "Cross-Language Pointer Analysis for Resolving Native Code in Java Programs"（Distinguished Paper）——是把 Java 程序里的 JNI native 调用解析出来，不是分析 C/C++ 程序本身
5. Topics：android / call-graph / java / security，无 C/C++

**深层原因**：C/C++ 指针模型与 Java 完全不同——指针算术、强制类型转换、内存布局/结构体偏移、函数指针、宏展开。Tai-e 的指针分析（CHA/RTA/Andersen 变体基于 Java 引用语义）无法表达这些。支持 C/C++ = 重写前端（对应 LLVM IR）+ 重写对象模型 + 重写指针分析 ≈ 自研 SVF。

**正确用法**：
- C/C++ 源码级：SVF（LLVM）、CodeQL、Clang Static Analyzer、Infer、Frama-C
- 二进制级：angr、Ghidra P-code（BinAbsInspector 模式）、Dyninst
- Tai-e 价值在：分析 Java/Android 时是学习指针分析/污点分析的最好教材（课程+作业+在线评测）

## 11. 路线决策（2026-08-12，用户确认）

**"静态分析→调用图构建→漏洞模式分析"路线判断**：
- 可以作为"定位器"（缩范围），不能作为"发现器"（直接找洞）——调用图负责把 LLM 引导到正确区域，LLM 做语义判断，fuzzer/独立复现做验证
- 必须与 fuzz/输入构造配对（8 个 CVE 靶标全为解析器类，H2：主入口=输入构造）
- 正确姿势：静态分析（建调用图+可达性剪枝）→ 漏洞模式预筛（低精度候选）→ LLM 语义判断 → 验证闭环

**下一步工作（两部分，均已立项）**：
1. angr/符号执行实现——先用 angr 跑通验证价值，再评估自研最小符号执行引擎
2. Tai-e 评估（已完成，见 §10，结论：不适用于 C/C++，转 SVF/CodeQL/angr）

## 12. 参考来源

- SoK: DARPA's AI Cyber Challenge (AIxCC)，arXiv 2602.07666（Georgia Tech 主导）
- All You Need Is A Fuzzing Brain，arXiv 2509.07225（Jeff Huang 团队）
- ATLANTIS: AI-driven Threat Localization, Analysis, and Triage，arXiv 2509.14589
- Buttercup 开源：github.com/trailofbits/buttercup（含 afc-buttercup 决赛版）
- FuzzingBrain 开源：github.com/o2lab/afc-crs-all-you-need-is-a-fuzzing-brain
- AIxCC 官方：aicyberchallenge.com（评分指南 PDF）
- 本地存档：company/research/aixcc-sok-arxiv-2602.07666.txt（SoK 全文提取）
