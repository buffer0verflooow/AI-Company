---
title: Cursor 的 Agent Swarm 实验：当 AI Agent 学会组队写代码
---

<style>
body, p, li, table, blockquote {
  font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
  font-size: 14px;
  line-height: 1.8;
  color: #1f2937;
}
p { margin: 0 0 12px; }
h1 {
  font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
  font-size: 24px; line-height: 1.45;
  color: #0f172a; margin: 20px 0 18px;
}
h2 {
  font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
  font-size: 18px; line-height: 1.5;
  color: #0f172a; border-left: 4px solid #2563eb;
  padding-left: 10px; margin: 28px 0 16px;
}
h3 {
  font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
  font-size: 16px; line-height: 1.6;
  color: #1e3a8a; margin: 20px 0 10px;
}
/* Code blocks — dark background, MUST have white-space:pre */
pre {
  background: #1e293b; color: #e2e8f0;
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
  font-size: 13px; line-height: 1.65;
  border-radius: 4px; padding: 14px 16px;
  margin: 14px 0 18px; overflow-x: auto;
  white-space: pre;
}
pre code {
  background: transparent; color: #e2e8f0;
  font-family: inherit; font-size: 13px;
}
/* Inline code */
code {
  background: #eff6ff; color: #1d4ed8;
  border-radius: 3px; padding: 1px 4px;
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
  font-size: 13px;
}
blockquote {
  background: #eff6ff; border-left: 4px solid #3b82f6;
  margin: 14px 0 18px; padding: 10px 14px;
  color: #1e3a8a;
}
blockquote p { margin: 0; }
table {
  width: 100%; border-collapse: collapse;
  margin: 14px 0 18px;
}
th, td {
  border: 1px solid #dbeafe; padding: 8px 12px;
  text-align: left; font-size: 13px;
}
th { background: #eff6ff; font-weight: 600; }
img { max-width: 100%; height: auto; margin: 12px 0; }
a { color: #2563eb; text-decoration: none; }
hr { border: none; border-top: 1px solid #e5e7eb; margin: 20px 0; }
</style>

# Cursor 的 Agent Swarm 实验：当 AI Agent 学会组队写代码

今年七月，Cursor 团队发布了一篇让我反复读了几遍的博客。他们让一群 AI Agent 组队，只凭一份 835 页的文档，用 Rust 从零实现了一个数据库——SQLite 的兼容实现。四小时后，Agent 群通过了全部测试用例。

这件事令人兴奋的地方不是 Agent 写出了代码，而是他们终于弄清楚怎么让多个 Agent 一起干活而不互相踩脚了。

之前 Cursor 做过一个更激进的项目：让 Agent 群从零构建一个网页浏览器。跑了一周，写了一百万行代码，成功渲染出页面。但那个项目更像一次极限测试——能跑，但不知道下一次会出什么乱子。

这次不同。他们把 Agent Swarm 当作工程问题来对待，有测试框架、有明确的对比基线、有量化的成本数据。这是一次有意识的工程化。

## 树与叶子

大型任务天然是树状结构：根是目标，递归拆成子任务，直到不可再分解的工作单元。

Cursor 的 Agent Swarm 只有两种角色，都围绕这棵树来组织：

规划者（Planner）——由最强的模型驱动，负责把目标拆碎、委派出去。设计决策必须自己定，不能往下扔。

执行者（Worker）——通常由更便宜、更快的模型驱动，拿到明确的任务描述后，只专心做这一件。

这种分层的意义不仅是分工。当一个 Agent 什么都要管时，它的上下文会被塞满。它要么盯着细节忘了大局，要么抱着全局视角把局部做糙。而规划者不需要写代码，所以上下文永远不会被细节淹没；执行者不需要决策，所以可以把全部上下文压缩到一件明确的事情上。

Agent Swarm 的扩展能力，主要来自这种上下文效率，而不是并行本身。

有意思的是，经济学家 Ronald Coase 一百年前就描述过同样的结构。他在追问企业为何存在时提出：协调成本的增长比工作本身更快，所以组织自然形成层级分明的单元，而不是让每个人直接和每个人沟通。A 和 B 之间沟通，不如让一个经理协调 A 和 B。

这大概是第一次，AI Agent 的架构设计和人类组织理论走到了同一个结论上。

## 每秒一千次提交

第一版 Swarm 跑浏览器时，用 Git 做版本控制。峰值达到每小时一千次提交，Git 已经吃紧。

第二版跑到每秒一千次提交。

Git 的粗粒度锁在这个量级完全行不通。Cursor 从零造了一套版本控制系统。吞吐量只是原因之一——每一行代码变更都会经过 VCS，冲突最先在这里浮出水面，所以协调机制直接做在 VCS 里。

达到这种提交速度后，人类团队中很少出现的失效模式开始频繁暴露。

## Agent 组队的四个坑

**脑裂设计。** 两个规划者彼此不知道对方存在，在代码库的不同位置各自实现了同一个概念。他们的解决方式是让规划者自己做设计决策，不委派出去，并且确保没有两个委派的子树对同一个问题做决定。

**规划者互搏。** 比脑裂更棘手的情况是规划者彼此知道对方存在，但仍然在同一组文件上来回修改。双方的代码都没错，但对问题域的理解不同。Cursor 让 Agent 把决策记录在共享的设计文件中，依赖这些决策的代码附上一个编译可检查的引用。当矛盾发生时，协调器合并文件，引用链自动向下游传导修复。

**合併冲突。** Agent 经常在同一文件上发生冲突。执行者不擅长解决冲突——它们要么覆盖对方，要么放弃自己的更改。Cursor 引入了一个中立第三方 Agent，在冲突发生时介入，代替双方完成合并。这类似于人类工程团队中的合并队列。

**超大型文件。** 有些文件特别容易被 Agent 集中攻击。每个 Agent 只加少量代码，但没有任何 Agent 负责保持文件精炼。这些"巨无霸文件"拖垮了传输和比较操作，变成冲突高发区。解决方案是让执行者能标记臃肿文件——一旦标记，新提交被阻塞，由外部 Agent 拆分成更小的模块。

这些失效模式其实不是 Agent 独有的。想想一个两三百人的工程团队，没有代码审查、没有架构师、没有合并队列——不出一个月就是同样的局面。只是在人类团队中，这些机制是被反复锤炼出来的常识；在 Agent 的世界里，每一样都要从零开始造。

## 审查视角的堆叠

Agent 跑的越久，错误累积越多。小错误不抓住，迟早变成根本性问题。

Cursor 试了各种审查策略：给审查者看完整对话记录、只看产出、或者除了代码库什么都不给。还让审查者运行在不同模型上，有不同训练数据、不同个性。

结论是，没有单一视角能捕捉一切。

但去相关的视角可以堆叠。审查者 A 漏掉的东西，审查者 B 因为视角不同可能会抓住。没有一个审查者是完美的，但多个不完美的审查者叠在一起，可靠性可以超过人类。

投入审查的算力回报率很高，因为审查的成本远低于被审查的工作本身。Cursor 认为，这套堆叠式审查系统是长期运行保持质量的关键。

## 让 Agent 为同伴写笔记

Cursor 在 Swarm 中做了一个叫 Field Guide 的实验。

这是一个完全由 Agent 维护的文件夹，里面的 index.md 会在每个新 Agent 启动时自动注入。Agent 自己决定往里面写什么内容，唯一的约束是行数预算。

背后的逻辑很简单：模型权重是冻结的，真正值得记录的，是那些出乎意料的发现。这样下一个 Agent 的探索路径就能更短。

听上去像文档。但 Cursor 认为这不是文档——这更像是白蚁筑巢时的 stigmergy。白蚁不直接沟通，它们通过塑造环境来影响其他白蚁的行为。一只白蚁堆了土，环境变了，下一只白蚁就会在土堆上继续。Agent 写笔记、未来的 Agent 读到笔记、轨迹更短，效果是一样的。

## 一个实验证明一切

为了验证新架构到底有没有提升，Cursor 找了一个旧版 Swarm 曾经搞不定的任务：只用文档，用 Rust 实现 SQLite。835 页文档，一个数据库。不给源码、不给测试套件、不给网络。

他们用 sqllogictest（SQLite 项目的测试套件）打分。每次跑完，看看 Agent 写出来的数据库答对了多少查询。系统从不知道这个测试的存在。

旧版 Swarm 和新版 Swarm 跑同一个任务、同一个模型、同一个时间预算。

结果非常清楚：新 Swarm 在每一种模型配置中都碾压旧版。使用 Grok 4.5 时，新版四小时达到 80%，旧版不到两小时就失控，只能被迫暂停。

旧版 Swarm 最终引擎代码 64,305 行，分 54 个 crate，里面三个互不通信的 SQL 实现。新版只用了 9,908 行，9 个 crate。旧版累积了超过七万次合并冲突，新版不到一千。

质量差距在代码里一目了然。

## 最有价值的数据

Cursor 坦白说，各种模型组合跑出来的最终质量差不多。真正惊人的是成本差异。

他们测试了四组配置：

GPT-5.5 全程担任规划和执行。这组的成本是一万零五百六十五美元。

Opus 4.8 担任规划者 + Composer 2.5 担任执行者。成本是一千三百三十九美元。

质量几乎一样，价格差了八倍。

为什么？因为执行者消耗了其中 69% 到 90% 的 token，但规划者的 token 更贵。在大型任务中，真正需要尖端智能的时刻并不多——最初的拆解、关键的设计决策、少数权衡取舍。一旦规划者把模糊收敛成了具体指令，便宜的模型照做就行。

还有一个有趣的细节：Fable 5 的每 token 价格比 Opus 4.8 贵一倍，但用它做规划者时产生的 token 更少——所以规划者账单反而更低。代价是执行者多跑了好多 token，最终总成本更高。模型选择不是简单的"贵的就好"或"便宜的就省"。

Cursor 把这个过程比作编译器：规划者把意图解析成任务树，执行者一步步细化成可执行的代码。区别在于，编译器的每一步保留语义，而 Agent Swarm 的每一步都有概率性——他们做的一切，都是为了缩小这个落差。

文章结尾有一句话值得记住：

> "以后稀缺的东西，是对意图恰如其分的描述。"

当 Agent Swarm 的工作单位从一行代码、一个函数变成一整个规格文档时，写清楚自己要什么成了最核心的能力。这个判断不只适用于写代码的人。

---

这篇文章基于 Cursor 官方博客 [Agent swarms and the new model economics](https://cursor.com/blog/agent-swarm-model-economics) 整理。代码库见 [github.com/cursor/minisqlite](https://github.com/cursor/minisqlite)。
