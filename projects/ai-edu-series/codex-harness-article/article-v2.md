---
title: "别只盯着 Prompt：安全研究里的 LLM Harness，才是真正的生产力放大器"
cover: ./cover.jpg
---

# 别只盯着 Prompt：安全研究里的 LLM Harness，才是真正的生产力放大器

**阅读时间**：约 10 分钟

**涉及技术**：LLM Harness、MCP、Claude Code、静态分析、模糊测试、RAG、漏洞验证

**适合读者**：安全研究员、红队、AppSec、AI 工具链爱好者

**作者**：Andy Gill（ZephrFish） · [原文链接](https://blog.zsec.uk/harnessing-harnesses/)

---

如果你已经把 LLM 接进自己的安全研究流程，大概率会遇到同一个问题：模型本身越来越强，但真正跑起来时，成本、上下文、工具调用、验证链路和可复现性，仍然很容易失控。

Andy Gill 在这篇文章里聊的不是"怎么写一个更神奇的 Prompt"，而是更底层、更工程化的一层：**Harness**。

简单说，Harness 是围绕 LLM 的编排层。它决定模型看什么、用什么工具、什么时候用、怎么验证结果、哪些状态要保存、什么时候该停下来把控制权交还给人。

> 不加 harness 和验证层就想逼 LLM 干出稳定成果，有点像管理一屋子喝醉的小朋友：每个都坚信自己在帮忙，但没人互相确认，最后还会彼此绊倒。

这句话听起来很损，但做过多 Agent、多工具、多轮漏洞研究的人，应该会会心一笑。

---

## 先把概念说清楚：Harness 到底是什么？

![](https://blog.zsec.uk/content/images/2026/06/image-12.png)

*LLM 编排层的价值，往往比模型本身更值得投入。*

很多讨论会集中在 Prompt Engineering、模型选择、上下文窗口大小上，但真正的差距在于：**一个强模型，如果周围没有结构，还是会：**

- 在重复上下文上疯狂烧 token；
- 一遍遍重新发现已经知道的信息；
- 产出无法验证、无法复现的结论；
- 在多个 Agent 之间制造重复劳动甚至互相冲突。

> 只选对模型却忽略 harness，就像买了一台赛车引擎，却把它装在购物车上。

![](https://blog.zsec.uk/content/images/2026/06/image-15.png)

*不是这种"安全带 / 攀岩 harness"，是 AI 编排层。*

在 AI / LLM 语境里，Harness 指的是围绕模型的一整套**控制系统**：

- 输入数据
- 工具与 MCP
- Prompt 与角色
- 模型路由
- 状态与记忆
- 验证门禁
- 输出格式与报告

![](https://blog.zsec.uk/content/images/2026/06/image-13.png)

*Harness 结构示意：输入 → 编排 → 工具/模型 → 验证 → 输出。*

如果前一篇文章聊的是 MCP，那么这里的关键认知是：**MCP 只是 Harness 里的工具层之一。**

MCP 可以给模型提供可调用能力（执行命令、反编译二进制、查询数据库、拉取上下文），但它不负责决定：什么时候调用、以什么顺序、输入什么上下文、结果怎么归档、下一步交给哪个阶段——这些都是 Harness 的职责。

在 offensive security research 里，一个可用的 Harness 至少要能回答：

1. 当前阶段应该收集什么数据？
2. 哪个工具该被调用，为什么？
3. 当前任务适合哪个模型？
4. 实际需要多少上下文？
5. 已经知道的知识如何复用，而不是每次重跑？
6. 最关键的是：模型什么时候该停止"思考"，把控制权交还给操作者？

Andy 自己的环境里跑着 8 个 MCP server，但真正让它们像流水线一样工作的，是 Harness。

---

## 顺手看一个 Token 成本工具：TokenBurn

如果你用 Claude 又关心 token 开销，Andy 写了一个 TokenBurn，把 Claude Max 订阅映射到 API 花费感知上。

> [TokenBurn on GitHub](https://github.com/ZephrFish/TokenBurn)

![](https://blog.zsec.uk/content/images/thumbnail/TokenBurn-c7470681-7136-4bac-871b-9cd08cc10cdd)

这类工具本身也提醒我们：Harness 不只是"让模型更聪明"，它也在帮你**控制预算**。

---

## 五款可以上手参考的 Harness

下面这些项目覆盖了从静态分析到 runtime exploit loop、从多 Agent 代码审计到 threat modeling 的不同思路。

### 1. RAPTOR：把 Claude Code 变成攻防安全 Agent

> [RAPTOR on GitHub](https://github.com/gadievron/raptor)

![](https://blog.zsec.uk/content/images/thumbnail/0428b245-c9ef-4eaf-abb0-742725f8720a-dccc114e-8529-498e-9ffc-1d74d08bc288)

RAPTOR 是 Andy 自己用得最多的一个。它不是把一个大 Prompt 丢给模型然后期待模型自己发现漏洞——它围绕 Claude Code 搭了一条结构化研究流水线，把静态分析、二进制分析、fuzzing、漏洞验证、exploit generation 组织在一起。

设计上分成两层，值得留意：

**Python 执行层**：负责跑工具，可以从 CI 驱动，输出结构化 SARIF，不一定每一步都要让模型参与。

**Claude Code 决策层**：负责判断要跑什么、结果如何解释、下一步怎么推进。

这个分离很重要。安全研究流水线里，工具执行和 AI reasoning 最好能独立测试；否则出了问题，你很难判断是 Prompt 坏了、工具坏了，还是上下文喂错了。

RAPTOR 的验证链路分成 A 到 F 六个阶段：

- **A-D**：判断漏洞模式是否真实、攻击者是否能触达、代码逐行是否支持结论，最终裁定 + CVSS 评分；
- **E**：考虑二进制可利用性（ASLR、RELRO、gadget、Z3 SMT 约束求解 one-gadget）；
- **F**：最终 contradiction check，避免互相矛盾的结论被提升成发现。

Andy 把 RAPTOR 作为 Git submodule 集成，主要用静态分析阶段，以及新的 Frida 功能做 Windows 应用的动态探索。

### 2. Anthropic Reference Harness：ASAN 可复现验证

> [Defending Code Reference Harness on GitHub](https://github.com/anthropics/defending-code-reference-harness)

![](https://blog.zsec.uk/content/images/thumbnail/defending-code-reference-harness-a45d693f-af54-4106-a926-9a27c437918a)

如果说 RAPTOR 更像一条综合型安全研究流水线，Anthropic 的 reference harness 则更聚焦：**面向 C/C++ 目标，在 ASAN instrumented Docker 容器里跑 find → grade → patch。**

适合这些条件的项目：有 C/C++ 代码、有 Dockerfile、有 build script、能构建 ASAN instrumented 版本。

工作流：

- `vulnpipeline_recon` — 映射攻击面；
- `vulnpipeline_run` — 启动独立 fuzzing agents，在 ASAN build 上收集 crash PoC；
- `vulnpipeline_report` — 对 unique crash 分级（passed / borderline / DoS-only / low-impact）；
- `vulnpipeline_patch` — 生成源码修复、重构建、重放 PoC 确认问题被修掉。

亮点：每个 finding 都带一个能在 instrumented build 上复现 crash 的 binary PoC，大大缩小了"这个洞到底能不能触达"的模糊空间。

当然，AI 没有 100% foolproof，它也会产出垃圾结果。所以是强补充，不是魔法盒。

### 3. Baby Naptime：让模型和真实运行时循环互动

> [Baby Naptime on GitHub](https://github.com/faizann24/baby-naptime)

![](https://blog.zsec.uk/content/images/thumbnail/baby-naptime-967596b3-60db-486e-b187-23feb7ddcc2b)

Google Project Zero 的 Nap Time 没有正式开源，但社区有 Baby Naptime 这个简化实现。

它的思路很像 runtime exploitation loop：模型面对一个真实运行中的 C/C++ binary，不断循环：

```
提出思路 → 执行尝试 → 观察输出 → 更新假设 → 再执行
```

这比让模型只读静态代码要好得多——你给它的是人类逆向工程师拿到的同样信号，然后让它基于真实运行时数据做几十轮反馈。信号质量完全不同。

### 4. Evil Socket Audit：8 阶段漏洞发现 Pipeline

> [Audit on GitHub](https://github.com/evilsocket/audit)

![](https://blog.zsec.uk/content/images/thumbnail/audit-833c6028-019a-4f10-9d27-a7c6aea25a29)

Audit 更灵活：不强依赖干净的 build system、Docker 或 runtime instrumentation，覆盖更多语言和"现实世界里有点乱"的仓库。

它跑一个 8 阶段 Claude Code pipeline：映射代码库 → 识别 trust boundary → 回看历史安全修复 → 拆分并行 agents → 验证 findings → 去重 → trace attacker-controlled input 到 vulnerable sink → 输出报告。

比"多 Agent 代码审计"更有纪律，因为 trace 阶段必须证明攻击者可控输入确实能到达危险 sink。但不能提供 ASAN crash 复现那种 runtime certainty。Andy 试用时也调了不少地方才让流程更顺——把它看作**可配置的研究流水线**，而不是"一键出真洞"。

### 5. Visa VVAH：Threat Modeling 和 Taint-flow 优先

> [Visa Vulnerability Agentic Harness on GitHub](https://github.com/visa/visa-vulnerability-agentic-harness)

![](https://blog.zsec.uk/content/images/thumbnail/visa-vulnerability-agentic-harness-ef91c0c7-92d1-4c21-91de-74b2236463f3)

和 Audit 接近，但更强调在 hunting 之前先做 threat modeling 和 taint-flow：inventory repository → 映射 trust boundaries → 分配 specialist review lenses → adversarial second pass 验证 → SARIF + Markdown 报告。

关键态度：它把结果视为 **triage candidates**，而不是已确认漏洞。这很健康。

局限也要清楚：call graph 由 LLM seed + regex reinforce，不是完整 AST；dynamic dispatch、reflection、framework routing 可能漏掉；不像 Anthropic harness 那样通过 runtime 证明可利用性，也不像 RAPTOR 那样依赖外部分析工具和 solver checks。它适合扩大覆盖面，仍需要人工 review。

---

## 如果自己设计 Harness，该从哪里下手？

一个最常见的坑：**整条 pipeline 只用一个 system prompt。**

在真实安全研究里，每个阶段的任务完全不同：

- mapping agent 需要找入口、依赖、文件路径；
- exploit hypothesis agent 需要构造攻击思路；
- PoC review agent 需要质疑结论；
- verification agent 应该优先寻找"为什么这个 finding 是错的"。

好的做法是每个阶段都有自己的 Prompt、输入、输出格式和验证标准。比如 mapping 阶段返回结构化 JSON：

```json
{
  "files": ["src/server/auth.cpp"],
  "entry_points": ["/api/login"],
  "dependencies": ["jwt", "openssl"],
  "notes": "Authentication boundary and token parsing path"
}
```

后续阶段只拿它需要的上下文，而不是所有文件、所有日志、所有历史对话一股脑塞进去。

值得关注的项目：[Scrutineer](https://github.com/alpha-omega-security/scrutineer)，它的 `revalidate` skill 是个好例子：当 `security-deep-dive` 产出 High/Critical finding 时，`revalidate` 结合 git history 判断属于 `true_positive` / `false_positive` / `already_fixed` / `uncertain`。只有标为 `true_positive` 的才进入 `verify` 阶段——把更贵的验证资源集中在最可能真实的问题上。

---

## Context Window 不是垃圾桶，它是预算

做 Harness 时，context management 很容易被低估。很多早期流程的失败模式都是：每个阶段塞 raw files、scanner output、完整对话历史。看起来很"充分"，实际在制造噪音。

几条硬经验：

- 只检索当前 hypothesis 相关的代码路径
- 把嘈杂工具输出压缩成摘要
- 保留短 rolling summary，已解决任务的结果存到别处
- 单函数分析通常 8K tokens 左右就够
- 多 findings 综合可能需要接近 32K
- fuzzer output 和 scanner logs 先压缩到几百个真正有用的 tokens

这件事最好一开始就设计好。后面再补 context management，非常痛苦。

---

## Orchestration Layer：工具不等于流程

Andy 自己的 setup 里，orchestration layer 位于 8 个 MCP servers 之上。MCP 提供工具，编排层决定：哪个工具该被调用、调用顺序、结果如何流转、哪些内容进入下一阶段、哪些落盘成为 artifact。

他也开源了一个简化版模板：Harness Kit。

> [Harness Kit on GitHub](https://github.com/ZephrFish/harness-kit)

![](https://blog.zsec.uk/content/images/thumbnail/harness-kit-73443ad0-780e-462f-b4de-8eb685126f6a)

Harness Kit 的流程有意保持简单：

```
recon → hunt → validate → trace → report
```

- **Recon**：映射目标，整理攻击面
- **Hunt**：围绕聚焦假设做调查
- **Validate**：主动寻找 finding 错误的理由
- **Trace**：证明攻击者可控输入能否到达 vulnerable sink
- **Report**：只有通过前面 gate 的内容才进入报告

关键不在于这五个词，而在于每一阶段都要有自己的 prompt、输入、输出和 gate。不要让所有阶段共享一个巨大对话——交换结构化 artefacts 更好，方便 inspect、rerun、replace，也方便 Hunt workers 在明确 context budget 下并行执行窄任务。

模型路由也是 Harness 的职责：便宜模型做分类、整理、摘要；强模型做验证、trace、综合。编排层负责 state、gate、budget、handoff——模型只处理一个聚焦的 reasoning 任务。

---

## Harness 也需要记忆：RAG 和反馈回路

上下文管理解决的是"本次运行某个阶段该看什么"。RAG 解决的是另一个问题：**之前运行中学到的东西，下一次怎么复用？**

![](https://blog.zsec.uk/content/images/2026/06/image-14.png)

对安全研究员来说，笔记一直很重要：某个工具怎么跑、某类目标常见坑在哪里、某个语言的危险模式、历史 findings 怎么复现。同样，Harness 也需要这些"经验"。

Andy 自己搭了一个 RAG 作为中心知识库，包含过往笔记、博客文章、语言细节、工具文档等。此外还有一个 360 feedback loop：每次成功跑出 findings 后，把新发现反馈回 baseline，让后续运行建立在已有知识上，而不是每次重新开始。

---

## 总结

**LLM workflow 真正有用的部分，通常不是模型本身，而是模型周围的结构。**

一个好的 Harness 不会取代判断和验证。它提供的是一套可重复执行的方式，让判断和验证稳定发生。模型不应该负责决定完整 workflow、记住所有细节、选择所有工具、再无条件相信自己的输出——那些应该由 orchestration layer 负责。

如果你正在把 LLM 接进漏洞研究、代码审计或红队工具链里，建议把上面这些 harness / pipeline 跑起来对比一下。很多时候，差距不在模型，而在模型外面的那层"手脚架"。

> "只选对模型却忽略 harness，就像买了一台赛车引擎，却把它装在购物车上。" —— Andy Gill / ZephrFish

---

*原文：[Harnessing Harnesses - Climbing the LLM Hills](https://blog.zsec.uk/harnessing-harnesses/) by Andy Gill*
