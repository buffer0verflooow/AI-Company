---
title: "别只盯着 Prompt：安全研究里的 LLM Harness，才是真正的生产力放大器"
cover: "/home/media/workspace/company/projects/ai-edu-series/codex-harness-article/cover.jpg"
---

# 别只盯着 Prompt：安全研究里的 LLM Harness，才是真正的生产力放大器

> 阅读时间：约 12 分钟
>
> 涉及技术：LLM Harness、MCP、Claude Code、静态分析、模糊测试、RAG、漏洞验证
>
> 适合读者：安全研究员、红队、AppSec、AI 工具链爱好者
>
> 原文作者：Andy Gill（ZephrFish）
>
> 原文链接：[Harnessing Harnesses - Climbing the LLM Hills](https://blog.zsec.uk/harnessing-harnesses/)

[![ZephrSec - Adventures In Information Security](https://blog.zsec.uk/content/images/2025/05/YoutubeHeader-Recovered-1.png)](https://blog.zsec.uk/)

*图 1：ZephrSec 博客页头，原文来自 Andy Gill / ZephrFish 的安全研究博客。*

[![Andy Gill](https://blog.zsec.uk/content/images/size/w100/2017/10/ZSIcon.png)](https://blog.zsec.uk/author/andy/)

*图 2：原文作者 Andy Gill（ZephrFish）的头像。*

![Harnessing Harnesses - Climbing the LLM Hills](https://blog.zsec.uk/content/images/size/w1000/2026/06/20220808_200348.jpg)

*图 3：原文主图：Harnessing Harnesses - Climbing the LLM Hills。*

如果你已经把 LLM 接进自己的安全研究流程，大概率会遇到同一个问题：模型本身越来越强，但真正跑起来时，成本、上下文、工具调用、验证链路和可复现性，仍然很容易失控。

Andy Gill 在这篇文章里聊的不是“怎么写一个更神奇的 Prompt”，而是更底层、更工程化的一层：**Harness**。

简单说，Harness 是围绕 LLM 的编排层。它决定模型看什么、用什么工具、什么时候用、怎么验证结果、哪些状态要保存、什么时候该停下来把控制权交还给人。

> 不加 harness 和验证层就想逼 LLM 干出稳定成果，有点像管理一屋子喝醉的小朋友：每个都坚信自己在帮忙，但没人互相确认，最后还会彼此绊倒。

这句话听起来很损，但做过多 Agent、多工具、多轮漏洞研究的人，应该会会心一笑。

---

## 先把概念说清楚：Harness 到底是什么？

![LLM orchestration illustration](https://blog.zsec.uk/content/images/2026/06/image-12.png)

*图 4：原文中用于引出 LLM 编排层价值的配图。*

很多讨论会集中在 Prompt Engineering、模型选择、上下文窗口大小上，但 Andy 的观点很直接：**真正决定能力、成本和可靠性的，往往是模型外面的编排层。**

一个强模型，如果周围没有结构，还是会：

- 在重复上下文上疯狂烧 token；
- 一遍遍重新发现已经知道的信息；
- 产出无法验证、无法复现的结论；
- 在多个 Agent 之间制造重复劳动甚至互相冲突。

原文里有个很形象的比喻：

> 只选对模型却忽略 harness，就像买了一台赛车引擎，却把它装在购物车上。

![Not that kind of harness](https://blog.zsec.uk/content/images/2026/06/image-15.png)

*图 5：原文的玩笑图：不是这种“安全带 / 攀岩 harness”。*

在 AI / LLM 语境里，Harness 指的是围绕模型的一整套**控制系统**，它会管理：

- 输入数据；
- 工具与 MCP；
- Prompt 与角色；
- 模型路由；
- 状态与记忆；
- 验证门禁；
- 输出格式与报告。

![Harness Structure](https://blog.zsec.uk/content/images/2026/06/image-13.png)

*图 6：原文中的 Harness 结构示意图。*

如果前一篇文章聊的是 MCP，那么这篇文章的关键是：**MCP 只是 Harness 里的工具层之一。**

MCP 可以给模型提供可调用能力，比如：

- 执行命令；
- 反编译二进制；
- 查询数据库；
- 拉取上下文；
- 调用内部服务。

但 MCP 不负责决定：

- 什么时候调用；
- 以什么顺序调用；
- 输入什么上下文；
- 结果怎么归档；
- 下一步该交给哪个阶段。

这些都是 Harness 的职责。

![MCP vs Harness](https://blog.zsec.uk/content/images/2026/06/mcp-vs-harness.svg)

*图 7：原文对 MCP 与 Harness 关系的对比图。MCP 是工具，Harness 是调度和约束。*

在 offensive security research 里，一个可用的 Harness 至少要能帮我们回答这些问题：

1. 当前阶段应该收集什么数据？
2. 哪个工具该被调用，为什么？
3. 当前任务适合哪个模型？
4. 实际需要多少上下文？
5. 已经知道的知识如何复用，而不是每次重跑？
6. 最关键的是：模型什么时候该停止“思考”，把控制权交还给操作者？

这也是为什么 Andy 说，他自己的环境里虽然有 8 个 MCP server，但真正让它们像流水线一样工作起来的，是 Harness。

---

## 顺手看一个 Token 成本工具：TokenBurn

原文里 Andy 也提到，如果你用 Claude，又关心 token burn，可以看他做的 TokenBurn，用来把 Claude Max 订阅映射到 API 花费感知上。

[TokenBurn GitHub 仓库](https://github.com/ZephrFish/TokenBurn?ref=blog.zsec.uk)

[![GitHub icon for TokenBurn](https://blog.zsec.uk/content/images/icon/pinned-octocat-093da3e6fa40-58a80fe7-7c10-4836-bcb3-d1878c52ad74.svg)](https://github.com/ZephrFish/TokenBurn?ref=blog.zsec.uk)

*图 8：原文中 TokenBurn GitHub 卡片的图标。*

[![TokenBurn repository thumbnail](https://blog.zsec.uk/content/images/thumbnail/TokenBurn-c7470681-7136-4bac-871b-9cd08cc10cdd)](https://github.com/ZephrFish/TokenBurn?ref=blog.zsec.uk)

*图 9：原文中 TokenBurn 仓库的缩略图。*

这类工具本身也提醒我们一件事：Harness 不只是“让模型更聪明”，它也在帮你**控制预算**。

---

## 已经能上手参考的 Harness 项目

下面这些项目，是原文重点讨论的几类公开 Harness / Pipeline。它们的目标不完全相同：有的偏静态分析，有的偏 runtime exploit loop，有的偏多 Agent 代码审计，也有的强调 threat modeling 和 taint-flow。

### 1. RAPTOR：把 Claude Code 变成攻防安全 Agent

[RAPTOR GitHub 仓库](https://github.com/gadievron/raptor?ref=blog.zsec.uk)

[![GitHub icon for RAPTOR](https://blog.zsec.uk/content/images/icon/pinned-octocat-093da3e6fa40-5168488e-7c13-4d9a-94f3-4621fe28ef34.svg)](https://github.com/gadievron/raptor?ref=blog.zsec.uk)

*图 10：原文中 RAPTOR GitHub 卡片的图标。*

[![RAPTOR repository thumbnail](https://blog.zsec.uk/content/images/thumbnail/0428b245-c9ef-4eaf-abb0-742725f8720a-dccc114e-8529-498e-9ffc-1d74d08bc288)](https://github.com/gadievron/raptor?ref=blog.zsec.uk)

*图 11：原文中 RAPTOR 仓库的缩略图。*

RAPTOR 是 Andy 用得比较多的一个。它不是把一个大 Prompt 丢给模型，然后期待模型自己发现漏洞；它围绕 Claude Code 搭了一条结构化研究流水线，把这些能力组织起来：

- 静态分析；
- 二进制分析；
- fuzzing；
- 漏洞验证；
- exploit generation。

RAPTOR 的设计比较有意思：它分成两层。

**第一层是 Python 执行层。**

它负责跑工具，可以从 CI 驱动，也可以输出结构化 SARIF。也就是说，不一定每一步都要让 Claude Code 参与。

**第二层是 Claude Code 决策层。**

它负责判断要跑什么、结果如何解释、下一步怎么推进。

这个分离很重要。因为安全研究流水线里，工具执行和 AI reasoning 最好能独立测试；否则出了问题，你很难判断是 Prompt 坏了、工具坏了，还是上下文喂错了。

RAPTOR 的验证链路分成 A 到 F 六个阶段：

- A-D：判断漏洞模式是否真实、攻击者是否能触达、代码逐行是否支持结论，并给出最终裁定和 CVSS；
- E：考虑二进制可利用性，比如 ASLR、RELRO、gadget 可用性，以及通过 Z3 SMT 约束求解 one-gadget 适用性；
- F：做最终 contradiction check，避免把明显互相矛盾的结论提升成发现。

Andy 自己把 RAPTOR 作为 Git submodule 集成到环境里，主要用它的静态分析阶段，以及新的 Frida 功能来做 Windows 应用的动态探索。

### 2. Anthropic Code Reference Harness：强调 ASAN 可复现验证

[Anthropic Defending Code Reference Harness GitHub 仓库](https://github.com/anthropics/defending-code-reference-harness?ref=blog.zsec.uk)

[![GitHub icon for Anthropic reference harness](https://blog.zsec.uk/content/images/icon/pinned-octocat-093da3e6fa40-7ea2f726-1750-4554-9849-11278857dfc6.svg)](https://github.com/anthropics/defending-code-reference-harness?ref=blog.zsec.uk)

*图 12：原文中 Anthropic Reference Harness GitHub 卡片的图标。*

[![Anthropic reference harness repository thumbnail](https://blog.zsec.uk/content/images/thumbnail/defending-code-reference-harness-a45d693f-af54-4106-a926-9a27c437918a)](https://github.com/anthropics/defending-code-reference-harness?ref=blog.zsec.uk)

*图 13：原文中 Anthropic Reference Harness 仓库的缩略图。*

如果说 RAPTOR 更像一条综合型安全研究流水线，那么 Anthropic 的 reference harness 则更聚焦：**面向 C/C++ 目标，在 ASAN instrumented Docker 容器里跑 find、grade、patch。**

它比较适合那些具备这些条件的项目：

- C/C++ 代码；
- 有 Dockerfile；
- 有 build script；
- 能构建 ASAN instrumented 版本。

它的工作流大致是：

- `vulnpipeline_recon`：映射攻击面，找重点区域；
- `vulnpipeline_run`：启动独立 fuzzing agents，在 ASAN build 上跑，收集 crash PoC；
- `vulnpipeline_report`：对 unique crash 分级，比如 passed、borderline、DoS-only、low-impact；
- `vulnpipeline_patch`：生成源码修复、重构建目标、重放 PoC 确认问题被修掉。

它的亮点是：每个 finding 都带一个能在 instrumented build 上复现 crash 的 binary PoC。这样一来，finding 是否可触达、是否真实，模糊空间会小很多。

当然，Andy 也很实在地补了一句：AI 这东西没有 100% foolproof，他自己也见过它产出一些垃圾结果。所以它是强补充，不是魔法盒。

### 3. Project Zero Nap Time / Baby Naptime：让模型和真实运行时循环互动

[Project Zero Nap Time 原文](https://projectzero.google/2024/06/project-naptime.html?ref=blog.zsec.uk)

Google 的 Project Zero Nap Time 没有正式开源，但社区里有一个简化开源实现：Baby Naptime。

[Baby Naptime GitHub 仓库](https://github.com/faizann24/baby-naptime?ref=blog.zsec.uk)

[![GitHub icon for Baby Naptime](https://blog.zsec.uk/content/images/icon/pinned-octocat-093da3e6fa40-9f9a3aca-e057-429c-abd7-f579cda5d38e.svg)](https://github.com/faizann24/baby-naptime?ref=blog.zsec.uk)

*图 14：原文中 Baby Naptime GitHub 卡片的图标。*

[![Baby Naptime repository thumbnail](https://blog.zsec.uk/content/images/thumbnail/baby-naptime-967596b3-60db-486e-b187-23feb7ddcc2b)](https://github.com/faizann24/baby-naptime?ref=blog.zsec.uk)

*图 15：原文中 Baby Naptime 仓库的缩略图。*

Baby Naptime 的思路很像 runtime exploitation loop：模型不是只看静态上下文，而是面对一个真实运行中的 C/C++ binary，不断循环：

```text
提出思路 → 执行尝试 → 观察输出 → 更新假设 → 再执行
```

这种方式很像人类逆向工程师的工作状态：你不是凭空“想象”程序行为，而是让程序跑起来，观察信号，再调整策略。

几十轮真实 runtime 数据反馈之后，模型能获得的信号质量，和单纯阅读静态代码完全不同。

### 4. Evil Socket Audit：更像 8 阶段漏洞发现 Pipeline

[Evil Socket Audit GitHub 仓库](https://github.com/evilsocket/audit?ref=blog.zsec.uk)

[![GitHub icon for Evil Socket Audit](https://blog.zsec.uk/content/images/icon/pinned-octocat-093da3e6fa40-6b7d108b-07e5-4ad9-96dc-35f8ca986981.svg)](https://github.com/evilsocket/audit?ref=blog.zsec.uk)

*图 16：原文中 Evil Socket Audit GitHub 卡片的图标。*

[![Evil Socket Audit repository thumbnail](https://blog.zsec.uk/content/images/thumbnail/audit-833c6028-019a-4f10-9d27-a7c6aea25a29)](https://github.com/evilsocket/audit?ref=blog.zsec.uk)

*图 17：原文中 Evil Socket Audit 仓库的缩略图。*

Audit 和前面几个不太一样，它更像一条 pipeline，而且会把不同阶段交给不同模型。

它的优势是灵活：不强依赖干净的 build system、Docker setup 或 runtime instrumentation，可以覆盖更多语言和更多“现实世界里有点乱”的仓库。

大体上，它会跑一个 8 阶段 Claude Code pipeline：

- 映射代码库；
- 识别 trust boundary；
- 回看历史安全修复；
- 将任务拆给并行 agents；
- 验证 findings；
- 去重；
- trace attacker-controlled input 到 vulnerable sink；
- 输出报告。

它比“多 Agent 代码审计”更有纪律，因为它要求 trace 阶段证明攻击者可控输入确实能到达危险 sink。

但它也不能提供 ASAN crash 复现那种 runtime certainty。Andy 提到，他自己试用时也需要调不少地方，才能让流程更顺。所以更适合把它看成**可配置的研究流水线**，而不是“一键出真洞”的工具。

### 5. Visa VVAH：Threat Modeling 和 Taint-flow 优先

[Visa Vulnerability Agentic Harness GitHub 仓库](https://github.com/visa/visa-vulnerability-agentic-harness?ref=blog.zsec.uk)

[![GitHub icon for Visa VVAH](https://blog.zsec.uk/content/images/icon/pinned-octocat-093da3e6fa40-4df0c73e-12fb-4931-8ff3-3a51e355dc93.svg)](https://github.com/visa/visa-vulnerability-agentic-harness?ref=blog.zsec.uk)

*图 18：原文中 Visa VVAH GitHub 卡片的图标。*

[![Visa VVAH repository thumbnail](https://blog.zsec.uk/content/images/thumbnail/visa-vulnerability-agentic-harness-ef91c0c7-92d1-4c21-91de-74b2236463f3)](https://github.com/visa/visa-vulnerability-agentic-harness?ref=blog.zsec.uk)

*图 19：原文中 Visa Vulnerability Agentic Harness 仓库的缩略图。*

VVAH 和 Audit 比较接近，但它更强调在 agents 开始 hunting 之前，先做好 threat modeling 和 taint-flow 分析。

它会做这些事情：

- inventory repository；
- 映射 trust boundaries；
- 分配 specialist review lenses；
- 用 adversarial second pass 验证 findings；
- 生成 SARIF 和 Markdown 报告。

一个很关键的点是：VVAH 把结果视为 **triage candidates**，而不是已确认漏洞。

这点其实很健康。LLM-led source pipeline 做 broad coverage 很有价值，尤其是面对不常见语言、难构建仓库时；但它的局限也要摆在桌面上：

- call graph 由 LLM seed，再用 regex reinforce，不是完整 AST；
- dynamic dispatch、reflection、framework routing 可能漏掉；
- 不像 Anthropic harness 那样通过 runtime execution 证明可利用性；
- 也不像 RAPTOR 那样重度依赖外部分析工具和 solver checks。

所以，它适合扩大覆盖面，但仍然需要人工 review 和 tuning。

---

## 如果自己设计 Harness，该从哪里下手？

Andy 提到一个很常见的坑：**整条 pipeline 只用一个 system prompt。**

这在真实安全研究里基本不够用。因为每个阶段的任务完全不同：

- 代码库 mapping agent 需要找入口、依赖、文件路径；
- exploit hypothesis agent 需要构造攻击思路；
- PoC review agent 需要质疑结论；
- verification agent 应该优先寻找“为什么这个 finding 是错的”。

一个比较好的方式是：每个阶段都有自己的 Prompt、输入、输出格式和验证标准。

比如 mapping 阶段返回结构化 JSON：

```json
{
  "files": ["src/server/auth.cpp"],
  "entry_points": ["/api/login"],
  "dependencies": ["jwt", "openssl"],
  "notes": "Authentication boundary and token parsing path"
}
```

后续阶段只拿它需要的上下文，而不是把所有文件、所有日志、所有历史对话一股脑塞进去。

原文还提到一个值得看的项目：[Scrutineer](https://github.com/alpha-omega-security/scrutineer?ref=blog.zsec.uk)。它的 `revalidate` skill 是一个不错例子：当 `security-deep-dive` 产出 High 或 Critical finding 时，`revalidate` 会结合 git history 判断结果属于：

- `true_positive`；
- `false_positive`；
- `already_fixed`；
- `uncertain`。

只有被标成 `true_positive` 的 finding 才会进入 `verify` 阶段，针对当前 HEAD 做测试。这样可以把更贵的验证资源集中在最可能真实的问题上。

---

## Context Window 不是垃圾桶，它是预算

做 Harness 时，context management 很容易被低估。

很多早期流程的失败模式都是：每个阶段都塞 raw files、scanner output、完整对话历史。看起来很“充分”，实际是在制造噪音和 token 浪费。

Andy 的建议可以概括成几条：

- 只检索当前 hypothesis 相关的代码路径；
- 把嘈杂工具输出压缩成摘要；
- 保留短 rolling summary；
- 已解决任务的结果存到别处，不要一直留在上下文里；
- 单函数分析通常 8K tokens 左右就够；
- 多 findings 综合可能需要接近 32K；
- fuzzer output 和 scanner logs 最好先压缩到几百个真正有用的 tokens。

这件事最好一开始就设计好。后面再补 context management，会非常痛苦。

---

## Orchestration Layer：工具不等于流程

Andy 自己的 setup 里，orchestration layer 位于 8 个 MCP servers 之上。MCP 提供工具，编排层决定：

- 哪个工具该被调用；
- 调用顺序是什么；
- 结果如何处理；
- 哪些内容进入下一阶段；
- 哪些内容应该落盘成为 artifact。

他也开源了一个简化版模板：Harness Kit。

[ZephrFish Harness Kit GitHub 仓库](https://github.com/ZephrFish/harness-kit?ref=blog.zsec.uk)

[![GitHub icon for Harness Kit](https://blog.zsec.uk/content/images/icon/pinned-octocat-093da3e6fa40-d5512eda-9571-40ef-8b5d-b0038bc36b82.svg)](https://github.com/ZephrFish/harness-kit?ref=blog.zsec.uk)

*图 20：原文中 Harness Kit GitHub 卡片的图标。*

[![Harness Kit repository thumbnail](https://blog.zsec.uk/content/images/thumbnail/harness-kit-73443ad0-780e-462f-b4de-8eb685126f6a)](https://github.com/ZephrFish/harness-kit?ref=blog.zsec.uk)

*图 21：原文中 Harness Kit 仓库的缩略图。*

Harness Kit 里的流程有意保持简单：

```text
recon → hunt → validate → trace → report
```

这条链路很适合作为你自己搭建安全研究 Harness 的骨架：

- **Recon**：映射目标，整理攻击面；
- **Hunt**：围绕聚焦假设做调查；
- **Validate**：主动寻找 finding 错误的理由；
- **Trace**：证明攻击者可控输入能否到达 vulnerable sink；
- **Report**：只有通过前面 gate 的内容才进入报告。

关键不在于这五个词，而在于每一阶段都要有自己的 prompt、输入、输出和 gate。

另一点也很重要：不要让所有阶段共享一个巨大对话。更好的方式是交换结构化 artefacts。这样流程更容易 inspect、rerun、replace，也更方便让 Hunt workers 在明确 context budget 下并行执行窄任务。

模型路由也是 Harness 的职责：

- 便宜模型做分类、整理、摘要；
- 强模型做验证、trace、综合；
- 编排层负责 state、gate、budget、handoff；
- 模型只处理一个聚焦的 reasoning 任务。

---

## Harness 也需要记忆：RAG 和反馈回路

上下文管理解决的是“本次运行某个阶段该看什么”。而 RAG 解决的是另一个问题：**之前运行中学到的东西，下一次怎么复用？**

![Harness memory and RAG](https://blog.zsec.uk/content/images/2026/06/image-14.png)

*图 22：原文中关于 Harness 记忆与 RAG 的配图。*

对安全研究员来说，笔记一直都很重要：

- 某个工具怎么跑；
- 某类目标常见坑在哪里；
- 某个语言的危险模式；
- 历史 findings 怎么复现；
- 某个框架的路由和鉴权习惯。

同样，Harness 也需要这些“经验”。

Andy 自己搭了一个 RAG，作为中心知识库，里面包含：

- 过往笔记；
- 博客文章；
- 语言细节；
- 工具文档；
- 其他安全研究资料。

除此之外，他还有一个 360 feedback loop：每次成功跑出 findings 后，把新发现反馈回 baseline，让后续运行建立在已有知识上，而不是每次重新开始。

---

## 再看一次 Harness Kit：模板，不是全自动神器

原文结尾再次强调了 Harness Kit：它是一个 stripped-back version，用来展示结构，而不是一个完成度很高的全自动研究平台。

[Harness Kit GitHub 仓库](https://github.com/ZephrFish/harness-kit?ref=blog.zsec.uk)

[![GitHub icon for Harness Kit closing card](https://blog.zsec.uk/content/images/icon/pinned-octocat-093da3e6fa40-df86160a-9324-4e2c-a911-2f39f280c92f.svg)](https://github.com/ZephrFish/harness-kit?ref=blog.zsec.uk)

*图 23：原文结尾处 Harness Kit GitHub 卡片的图标。*

[![Harness Kit closing repository thumbnail](https://blog.zsec.uk/content/images/thumbnail/harness-kit-79264cea-1372-4127-9946-f84db8d76c73)](https://github.com/ZephrFish/harness-kit?ref=blog.zsec.uk)

*图 24：原文结尾处 Harness Kit 仓库的缩略图。*

它展示的是这些结构性能力：

- separated stages；
- structured artefacts；
- scoped context；
- validation gates；
- model routing；
- persistent state。

这其实也是整篇文章最值得带走的观点：**LLM workflow 真正有用的部分，通常不是模型本身，而是模型周围的结构。**

一个好的 Harness 不会取代判断和验证。它提供的是一套可重复执行的方式，让判断和验证稳定发生。

模型不应该负责决定完整 workflow、记住所有细节、选择所有工具、再无条件相信自己的输出。那些应该由 orchestration layer 负责。

---

## 原文相关延伸阅读

原文页尾还放了几篇 ZephrSec 相关内容。如果你想顺着作者的上下文继续看，可以从这些开始。

[![Jenny was a Friend of Mine - MCPs and Friends](https://blog.zsec.uk/content/images/size/w300/2026/04/signal-2026-04-04-115255_002.jpeg)](https://blog.zsec.uk/bullyingllms/)

*图 25：原文相关帖《Jenny was a Friend of Mine - MCPs and Friends》的封面图。*

- [Jenny was a Friend of Mine - MCPs and Friends](https://blog.zsec.uk/bullyingllms/)

[![AI Assisted Development - FAFO](https://blog.zsec.uk/content/images/size/w300/2025/08/L1061670-2.jpg)](https://blog.zsec.uk/ai-assisted-dev/)

*图 26：原文相关帖《AI Assisted Development - FAFO》的封面图。*

- [AI Assisted Development - FAFO](https://blog.zsec.uk/ai-assisted-dev/)

[![The Human Element: Why AI-Generated Content Is Killing Authenticity](https://blog.zsec.uk/content/images/size/w300/2025/05/L1060154.jpg)](https://blog.zsec.uk/creativity-is-not-dead/)

*图 27：原文相关帖《The Human Element: Why AI-Generated Content Is Killing Authenticity》的封面图。*

- [The Human Element: Why AI-Generated Content Is Killing Authenticity](https://blog.zsec.uk/creativity-is-not-dead/)

---

原文：[Harnessing Harnesses - Climbing the LLM Hills](https://blog.zsec.uk/harnessing-harnesses/)

如果你正在把 LLM 接进漏洞研究、代码审计或红队工具链里，建议读一遍原文，也顺手把上面这些 harness / pipeline 跑起来对比一下。很多时候，差距不在模型，而在模型外面的那层“手脚架”。

> “只选对模型却忽略 harness，就像买了一台赛车引擎，却把它装在购物车上。”——Andy Gill / ZephrFish
