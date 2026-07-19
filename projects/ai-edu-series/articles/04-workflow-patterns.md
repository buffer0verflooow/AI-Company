---
title: Agent 工作流模式——Chain、Router、Parallel，你的 Agent 该跑哪种？
cover: /home/media/workspace/company/projects/ai-edu-series/articles/assets/cover-04-workflow-patterns.png
---

# Phase 14｜Agent 工作流模式——Chain、Router、Parallel，你的 Agent 该跑哪种？

> 前三篇文章教你造车（Agent Loop）、给地图（Tool Schema）、学会开车（Function Calling）。现在车有了，问题是：**路不止一条。** 有些任务适合直线跑到底，有些需要路口分流，有些可以多条车道并行——Anthropic 管这叫 Workflow Pattern，搞懂了比多装几个工具更有用。

---

📖 本系列基于开源项目 [AI Engineering from Scratch](https://github.com/rohitg00/ai-engineering-from-scratch)（503 节课 · 20 阶段 · MIT 协议），用中文重新梳理 AI 全栈知识体系，从数学基础一路写到多智能体集群。

---

**📌 这篇文章聊什么**：掌握五种 LLM 工作流模式——Prompt 链式、路由分发、并行化、编排者-工作者、评估者-优化者——以及每种模式适合什么场景、不适合什么场景

**⏱️ 预计阅读**：15 分钟

**🛠️ 涉及技术**：Workflow Patterns · Chain · Router · Parallel · Orchestrator-Worker · Evaluator-Optimizer · Anthropic Building Effective Agents

**🎯 内容地图**：能判断任何任务该用什么工作流 + 每种模式的代码骨架

---

## 🔥 为什么「选什么路」比「车多快」更重要

先讲一个翻车现场。

你给 Agent 注册了 20 个工具，写了完美的工具描述，配了并行投递器。然后你问：「帮我分析上周的销售数据，看看哪些品类该补货，给供应商发邮件」。

Agent 开始跑——第一轮它自己琢磨要调什么工具，第二轮收到一堆数据开始分析，第三轮发现有歧义又回头再查，第四轮、第五轮、第六轮……40 轮后 token 烧光，邮件没发出去。

**问题不在 Agent，在编排方式。** 你用一根筋的 ReAct 循环去跑一个需要多步骤协作的任务，就像让一个人同时做数据采集、分析、决策、写邮件——他能做，但效率极差。

2024 年底 Anthropic 发了篇《Building Effective Agents》，里面有一句话直接点破了这件事：

> 「大部分问题不需要 Agent。先用直接 API 调用，只有当步骤无法预判时才上 Agent。」

他们把常见的 LLM 编排方式归纳为**五种工作流模式**：

```
┌──────────────────────────────────────────────────────┐
│                    LLM 编排光谱                        │
├────────────┬────────────┬────────────┬───────────────┤
│  Prompt链式  │  路由分发   │  并行化    │ 编排者-工作者  │
│  直线串联    │  条件分流   │  多路并发  │ 动态派发子任务 │
│             │            │           │               │
│  评估者-优化者│            │           │               │
│  生成→评估→改│            │           │               │
└────────────┴────────────┴────────────┴───────────────┘
```

下面我们逐个拆开看。

---

## 🧠 五种工作流模式

### 模式一：Prompt 链式（Chain）——最朴素也最可靠

**什么时候用**：步骤可以事先枚举，上一个输出是下一个输入。

最简单的例子：翻译 → 润色 → 排版。

你不会让 LLM 翻译时顺便做排版。你把翻译的英文稿扔给润色 Prompt，把润色稿扔给排版 Prompt。每一步的输出严格等于下一步的输入。

```python
# Prompt 链式：三步骤串行
def translate_and_format(text: str) -> str:
    raw = llm.call("把以下英文翻译成中文：" + text)          # Step 1
    polished = llm.call("润色这段中文，使其更流畅：" + raw)    # Step 2
    formatted = llm.call("用公众号排版规范格式化：" + polished)  # Step 3
    return formatted

# 每一步的输入精准、输出可预测——这是最简单的模式，也是最不会出错的模式。
```

**核心原则**：每一步只做一件事。LLM 最擅长的是单体任务——给它一个指令、一个输入、让它做一个操作。你把 10 个操作揉成一个 Prompt，它每个都做得一般；你拆成 10 个 Prompt，每个都做得好。

**不适合的场景**：步骤无法事先枚举。比如「帮我在网上找最便宜的往返机票」——你不知道要搜几次、每次搜什么关键词。这种情况需要 Agent Loop 而不是 Chain。

### 模式二：路由分发（Router）——先分类，再分发给专家

**什么时候用**：输入有明确的类型区分，不同类型需要不同的处理方式。

经典例子：客服系统。用户可能问退款、技术问题、投诉——处理方式完全不同。

```python
def route_and_handle(query: str) -> str:
    # Step 1: 路由——先分类
    route_prompt = """
    将用户咨询分类为以下之一：
    - refund: 退款相关
    - tech: 技术问题
    - complaint: 投诉
    只回复分类标签。
    """
    category = llm.call(route_prompt + f"\n咨询内容：{query}").strip()

    # Step 2: 按类别分发
    handlers = {
        "refund": "用户想退款。查询订单、计算退款金额、告知流程。",
        "tech": "用户遇到技术问题。按排查清单引导用户提供更多信息。",
        "complaint": "用户投诉。先道歉、理解问题、提供解决方案或升级。",
    }
    return llm.call(f"你是客服。{handlers.get(category, handlers['tech'])}\n咨询：{query}")
```

**为什么比一个 Prompt 好**：如果你写「你是客服，请处理用户咨询」，LLM 会给每个问题差不多字数的回答——退款和投诉在它看来权重一样。路由分发的本质是**给不同类型的问题不同的 Prompt 预算**。

**常见错误**：把路由搞得太细。15 个分类类别让 LLM 去判断，它自己也会分错。控制在 3-5 个类别——超出这个数，考虑嵌套路由或上 Agent。

### 模式三：并行化（Parallel）——同时跑，回来再合

**什么时候用**：任务可以拆成互不依赖的子任务。

两种形式：

**1. 分段并行**：同一类操作，不同输入。

```python
# 同时翻译三个段落
sections = split_article(article)
translations = parallel_call([
    ("翻译为英文：", s) for s in sections
])  # 三段同时跑，互不干扰
result = "\n".join(translations)
```

**2. 多角度并行**：同一输入，不同视角。

```python
# 三个评委同时打分，取平均
reviews = parallel_call([
    ("从代码质量角度评分：", code),
    ("从安全性角度评分：", code),
    ("从可维护性角度评分：", code),
])
final_score = average(extract_scores(reviews))
```

**关键判断**：两个子任务能不能并行？看第二个的输入是否依赖第一个的输出。如果依赖——串行；不依赖——并行。就这么简单。

**并行化最容易被滥用**：为了炫技开 10 个并行调用来总结一篇文章的 10 个段落。但 10 个并行的 token 消耗是 1 个串行的 10 倍——问问自己这篇东西值不值。

### 模式四：编排者-工作者（Orchestrator-Worker）

**什么时候用**：复杂任务，子任务之间有依赖，无法事先列出所有步骤。

这是五种模式里最接近 Agent 的一种。一个编排 LLM 动态决定「下一步该做什么」，然后把子任务派发出去，收到结果后再决定「再下一步」。

```python
def orchestrator_worker(task: str) -> str:
    findings = []
    for _ in range(max_rounds):
        # 编排者：读取已有发现，决定下一步
        decision = llm.call(f"""
        任务：{task}
        已有发现：{findings}
        
        决定下一步：
        - search("关键词")  搜索互联网
        - analyze("数据")  分析已收集的数据
        - synthesize()     综合所有发现，生成最终答案
        
        只返回函数调用格式。
        """)

        action, arg = parse_decision(decision)
        if action == "synthesize":
            return llm.call(f"综合以下发现回答：{task}\n{findings}")

        result = work(action, arg)  # 工作者：执行具体任务
        findings.append(result)
```

**编排者和 Agent Loop 的区别**：Agent Loop 里 LLM 同时做「思考」和「执行」——它的上下文里塞满了工具返回结果和自问自答。编排者只做「调度」——它看一份干净的发现列表，决定下一步，自己不干活。这避免了 Agent Loop 常见的「上下文腐烂」——100 轮后提示词里塞了前 99 轮的碎碎念。

**什么时候升级到编排者**：当一个 ReAct Agent 开始在第 15 轮之后明显「迷失」——重复调用同一个工具、忘记之前找到了什么、开始从头再来——这就是编排者模式的信号。

### 模式五：评估者-优化者（Evaluator-Optimizer）

**什么时候用**：输出质量可以客观评估，有明确的「对/错」或「好/更好」标准。

经典例子：代码生成 + 单元测试验证。

```python
def evaluator_optimizer(task: str, max_iterations=5) -> str:
    draft = llm.call(f"生成 {task} 的初版")
    
    for i in range(max_iterations):
        # 评估者：给出反馈
        feedback = llm.call(f"""
        评估以下输出是否满足需求。指出具体问题。
        需求：{task}
        输出：{draft}
        """)

        if "满足" in feedback or "没有明显问题" in feedback:
            return draft

        # 优化者：根据反馈修改
        draft = llm.call(f"""
        你的上一版被评为：{feedback}
        请修改输出以解决这些问题。
        原输出：{draft}
        """)

    return draft  # 达到最大迭代，返回最后一版
```

**这个模式的关键不在 LLM，在评估方式**：

- **弱评估**：LLM 自己评自己（不可靠，一个幻觉在自己看来「挺有道理」）
- **强评估**：外部工具验证——单元测试跑代码、搜索引擎核实事实、linter 检查格式
- **最强评估**：人类判断（慢但准）

**一个结论**：没有外部验证的评估者-优化者只是一厢情愿。如果评估者不能接触到 LLM 自己没见过的事实或工具反馈，它给出的「修改意见」通常只是让输出看起来更花哨，而不是更准确。

---

## ✍️ 手写实现：一个把五种模式统一在一起的编排器

如果你理解了上面五种模式，它们的共同点其实很简单：**输入→处理→输出→决定下一步**。下面这个 `WorkflowEngine` 把五种模式统一到一个接口里——它不是让你直接用，而是让你看到每种模式的本质差异只有几行代码。

```python
from dataclasses import dataclass
from typing import Callable

@dataclass
class Step:
    name: str
    fn: Callable  # (input: str) -> str

class WorkflowEngine:
    """五种工作流模式的统一引擎"""

    def chain(self, steps: list[Step], input_text: str) -> str:
        """模式一：Prompt 链式——上一个输出→下一个输入"""
        result = input_text
        for step in steps:
            result = step.fn(result)
        return result

    def router(self, classifier: Callable, routes: dict[str, Callable], query: str) -> str:
        """模式二：路由分发——分类→分发到对应处理器"""
        category = classifier(query)
        handler = routes.get(category, routes.get("default"))
        return handler(query) if handler else f"未知分类: {category}"

    def parallel(self, fn: Callable, items: list[str]) -> list[str]:
        """模式三：并行化——同一操作对不同输入同时执行"""
        return [fn(item) for item in items]

    def orchestrator(self, planner: Callable, workers: dict[str, Callable],
                     task: str, max_rounds: int = 10) -> str:
        """模式四：编排者-工作者——动态决定下一步，派发执行"""
        context = []
        for _ in range(max_rounds):
            action, arg = planner(task, context)
            if action == "done":
                return arg  # 最终答案
            worker = workers.get(action)
            if worker:
                context.append(worker(arg))
        return "达到最大轮次"

    def evaluator(self, generator: Callable, evaluator: Callable,
                  task: str, max_iter: int = 5) -> str:
        """模式五：评估者-优化者——生成→评估→修改→循环"""
        draft = generator(task)
        for _ in range(max_iter):
            verdict = evaluator(task, draft)
            if verdict.startswith("PASS"):
                return draft
            draft = generator(f"上一版被反馈为: {verdict}\n改进并重新生成: {task}")
        return draft
```

**这段代码的核心设计决策**：

1. **chain、router、parallel 可以用 orchestrator 模拟**——为什么要单独拆出来？因为够简单。用 orchestrator 跑一个固定三步的链式任务，token 开销比 chain 大 5 倍。**知道什么时候不用复杂方案，比知道什么时候用更重要。**

2. **parallel 方法里的 `[fn(item) for item in items]` 是串行的**——这是演示版。生产环境用 `asyncio.gather` 或线程池。核心结构没变。

3. **evaluator 不改代码，改 Prompt**——注意到评估者-优化者模式的迭代不修改模型参数，只是把上一轮的反馈塞进下一轮的 Prompt。这和 Reflexion（口头强化学习）的思路一致——不需要梯度下降，只需要「写下来、塞回去、重来」。

---

## 🚀 怎么选模式：一个决策树

```
任务可以事先枚举所有步骤？
  ├── 是 → 用 Prompt 链式（Chain）
  │       └── 步骤间互不依赖？
  │             └── 是 → 用 Parallel 加速
  │
  └── 否 → 输入有明确的类型分类？
          ├── 是 → 用 Router 分发
          │
          └── 否 → 输出质量可以客观评估？
                  ├── 是 → 用 Evaluator-Optimizer（配外部验证）
                  │
                  └── 否 → 任务复杂、子任务有依赖？
                          ├── 是 → 用 Orchestrator-Worker
                          │
                          └── 否 → 你是被炒作洗脑了
                                  → 用最简单的方法，别上 Agent
```

这张决策树值一篇文章。直接存下来，下次开新任务的时候先走一遍。

---

## 📦 回顾一下

五种模式对应五种不同的「任务形状」：

```
Prompt 链式      → 直线跑道（翻译→润色→排版）
路由分发          → 十字路口（退款走左、技术走右）
并行化            → 多车道高速（三段同时翻译）
编排者-工作者     → 动态地图（边走边决定下一步去哪）
评估者-优化者     → 循环赛道（跑到过关为止）
```

前三篇文章帮你造了车、装了导航、学会了踩油门。这篇文章帮你**会看路**——不是每条路都要用最复杂的方式去开，有时候一条直路就够了。

---

## 🔮 下一篇

下一篇文章回到 Phase 14 的第一站——**「Agent Memory——让 Agent 记住今天做了什么」**。Agent 最大的痛不是不会干活，是干完就忘。我们下篇聊三种记忆范式：虚拟上下文、记忆块、混合记忆。

---

## 📚 延伸阅读

- 源项目 Phase 14 完整目录：[AI Engineering from Scratch — Agent Engineering](https://github.com/rohitg00/ai-engineering-from-scratch/tree/main/phases/14-agent-engineering)
- [Anthropic, Building Effective Agents](https://www.anthropic.com/engineering/building-effective-agents) — 五种工作流模式的原始出处
- [OpenAI — Prompt Chaining Guide](https://platform.openai.com/docs/guides/prompt-engineering) — 链式调用的最佳实践

---

🧭 Phase 14 / 20 · Agent Engineering · Workflow Patterns 篇
📋 上一篇：Phase 14 · 函数调用——Agent 拿到地图后怎么开车
📋 下一篇：Phase 14 · Agent Memory——让 Agent 记住今天做了什么

💡 如果这篇文章对你有帮助，欢迎 **点赞 · 在看 · 分享**

> *「最好的编排，是让你根本感觉不到编排的存在。」*
