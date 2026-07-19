---
title: Agent 的灵魂只有 120 行代码——从零手写 AI Agent 工程入门
cover: ../assets/cover-phase-14-agent-intro.png
---

# Phase 14｜Agent 的灵魂只有一个不到 200 行的循环——从零手写 AI Agent 工程入门

> 2026 年，每一个你能叫出名字的 AI 产品——Claude Code、Cursor、Devin、元宝——底层跑的都是同一件事：一个不到 200 行的循环。学会这个循环，你就拿到了 Agent 世界的万能钥匙。

---

📖 本系列基于开源项目 [AI Engineering from Scratch](https://github.com/rohitg00/ai-engineering-from-scratch)（503 节课 · 20 阶段 · MIT 协议），用中文重新梳理 AI 全栈知识体系，从数学基础一路写到多智能体集群。

---

**📌 这篇文章聊什么**：理解 AI Agent 的核心运行机制，掌握 Agent Loop、工具调用、Reflexion 反思等关键概念

**⏱️ 预计阅读**：15 分钟

**🛠️ 涉及技术**：ReAct 循环 · Function Calling · Tool Schema · Reflexion · ReWOO · Tree of Thoughts

**🎯 内容地图**：Agent 工程全景认知 + Agent Loop 核心代码骨架


## 🔥 为什么你该关心 Agent

先说一个尴尬的事实。

你花 199 刀买了 ChatGPT Plus，问它「帮我查一下明天北京到上海的机票」。它回答得头头是道——"建议您通过携程或飞猪查询实时票价，目前参考价格约为……"

它不知道。它只是根据训练数据猜了一个听起来合理的答案。**LLM 本质上是一个超级自动补全引擎**——给定上文，它猜下文。它不能真的去查机票、不能读你的邮箱、不能帮你提交代码 PR。

Agent 改变了这一切。

Agent（智能体）在 LLM 外面包了一层循环，让模型可以：停下来 → 调用工具 → 读到结果 → 继续思考 → 再调用工具 → 直到完成任务。**这个循环，就是整个 Agent 工程大厦的地基。**

用一句话理解 Agent：**LLM 是大脑，Agent 是给了大脑手和眼睛的完整身体。**

> 这篇文章会带你搞清楚：
> - 彻底理解 Agent 和普通 LLM Chat 的本质区别
> - 掌握 ReAct 循环的五个核心要素
> - 理解工具定义（Tool Schema）和函数调用（Function Calling）的原理
> - 建立 Agent 工作流模式的全局地图（ReWOO / Reflexion / ToT / Self-Refine）
> - 获得一段可以直接理解（甚至手写出来）的 Agent Loop 核心代码

---

## 🧠 核心概念：Agent 工程的四大基石

### 基石一：Agent Loop——Agent 的心跳

如果你拆开 Claude Code、Cursor Agent、OpenAI Codex 的引擎盖，你会看到同一个东西：**ReAct 循环**。

这个名字来自 Yao 等人 2022 年的论文《ReAct: Synergizing Reasoning and Acting in Language Models》。ReAct = **Reason（推理）+ Act（行动）**。每一次循环只有三步：

```
思考（Thought）→ 行动（Action）→ 观察（Observation）→ 思考 → 行动 → 观察 → …… → 完成
```

举个具体的例子。假设你让 Agent 查「法国首都是什么，这个城市的人口是多少」：

```
Thought: 我需要先查出法国的首都。
Action: search("capital of France")
Observation: Paris is the capital of France.

Thought: 现在我知道首都是巴黎，需要查巴黎的人口。
Action: search("population of Paris")
Observation: Paris has a population of approximately 2.1 million.

Thought: 我已经有了完整答案。
Action: finish("法国首都是巴黎，人口约 210 万。")
```

这就是一切 Agent 的底层。**它简单到令人发指，但强到足以驱动 2026 年最复杂的 AI 产品。**

每个 Agent Loop 只需要五个要素，缺了任意一个你就不是 Agent，只是个聊天机器人：

| 要素 | 说明 | 缺了会怎样 |
|------|------|-----------|
| **消息缓冲区** | 保存完整的「用户→助手→工具→助手→……」对话历史 | 模型记不住自己刚才做了什么 |
| **工具注册表** | 模型可以按名字调用的工具清单，每个工具有名字、描述、参数定义 | 模型不知道该用什么、怎么用 |
| **停止条件** | 判断什么时候该结束：模型说 `finish`、本轮没调用工具、达到最大轮次、触发安全门 | Agent 会死循环 |
| **轮次预算** | 硬性限制最多跑多少轮（2026 年典型场景 40-400 步） | 一个任务烧掉你几百次 API 调用 |
| **观察格式化器** | 把工具返回的结果转成模型能理解的文本 | 工具报错了，模型却以为成功了 |

> ⚠️ **2026 年的新变化**：早期的 ReAct 用 `Thought:` 这样的文本标记来承载推理过程。2025-2026 年，OpenAI 的 Responses API 和 Letta V1 把「推理」放到了独立通道上——思考不再是提示词里的文字，而是模型原生的推理 token，在推理结束后传递给下一轮。**但循环本身没变。** 变得只是推理内容放在哪里，不变的是「观察→思考→行动→观察」这个控制流。

### 基石二：工具定义与 Schema——让模型知道「你有什么武器」

Agent 能调工具，但模型怎么知道有哪些工具、每个工具怎么用？

答案是 **Tool Schema（工具定义）**。每个工具需要三样东西：

```
工具名（name）: "search_web"
工具描述（description）: "搜索互联网获取实时信息。当需要最新数据或核实事实时使用。"
参数定义（input_schema）: {query: string}
```

**工具描述是生死攸关的。** 模型就是靠读你的描述来决定「这个场景该调用哪个工具」。描述写得差——比如写了 `"search(query)"` 但没说什么时候用它——模型就只能瞎猜。实际生产中，**选错工具的第一大根因就是糟糕的工具描述。**

工具注册后还有一道防线：**参数校验**。永远不要信任模型传过来的参数。模型可能把数字传成字符串（`"5"` 而不是 `5`），可能传一个 schema 里不存在的枚举值，可能漏掉必填字段。每一条校验失败都应该作为**结构化的错误观察**返回给模型，让它可以重试——而不是直接崩溃。

> 📊 **学术前沿：BFCL V4（伯克利函数调用排行榜）**  
> 2025-2026 年的事实标准评测。V4 包含五类场景：40% Agent 轨迹（多轮+记忆+动态决策）、30% 多轮对话、10% 真实用户问题、10% 合成用例、10% 幻觉检测（模型应该拒绝调用不存在的工具）。核心发现：**单轮函数调用已接近解决，真正的难题集中在长链工具调用（超过 20 步后模型开始漂移）、记忆传递和动态决策上。**

### 基石三：工作流模式——Agent 不止一种跑法

一旦你理解了 Agent Loop，下一步就是知道**什么时候用简单的，什么时候上复杂的**。Anthropic 在 2024 年底发布了一篇被引爆的文章《Building Effective Agents》，核心观点是：

> **大部分问题不需要 Agent。先用直接 API 调用，只有当步骤无法预判时才上 Agent。**

他们把常见的 LLM 编排方式归纳为五种工作流模式：

| 模式 | 核心思想 | 适用场景 |
|------|---------|---------|
| **Prompt 链式** | 上一个输出是下一个输入，线性串联 | 翻译→润色→排版，步骤可枚举 |
| **路由分发** | 先分类，再分发给不同的处理器 | 用户问客服/退款/技术/Bug？先分类 |
| **并行化** | 同时跑 N 个 LLM 调用，聚合结果 | 多角度打分取平均，多段落分别总结 |
| **编排者-工作者** | 一个编排 LLM 动态决定派哪些专家 | 复杂任务，子任务间有依赖 |
| **评估者-优化者** | 生成→评估→修改，循环到通过为止 | 代码生成（跑测试验证）、文案润色 |

但 Agent 真正的威力在于下面这些更高级的模式。我们挨个看：

#### ReWOO：先把计划写好，再一口气执行

ReAct 把思考和行动交织在一起，每一步都要把之前所有的思考带在上下文里——token 消耗随步骤数快速增长。到第 10 步时，提示词里塞满了前 9 步的思考。

**ReWOO（Reasoning WithOut Observations）** 提出了一个大胆的想法：把计划和执行拆开。

```
Planner（规划器）:  用户问题 → [计划 DAG]
Worker（执行器）:   [计划 DAG] → [证据收集]
Solver（求解器）:   用户问题 + 计划 + 证据 → 最终答案
```

规划器一次生成完整计划（比如「步骤1：查首都→步骤2：查人口」），执行器并行获取证据（两步互不依赖就可以同时跑），求解器把所有信息串起来。

**据 ReWOO 论文实验数据：token 消耗降至 ReAct 的约 1/5，HotpotQA 准确率反而高了 4 个百分点。** 更厉害的是，因为规划器不接触工具返回结果，你可以用一个 7B 的小模型做规划（从 175B 大模型蒸馏出来），推理时根本不需大模型。

#### Reflexion：让 Agent 从失败中学习，不需要梯度下降

传统的强化学习要修复一个 bug，需要跑几千次试验、算梯度、更新权重。贵、慢、而且大部分生产环境没有训练预算。

**Reflexion（口头强化学习）** 问了另一个问题：Agent 失败了，能不能让它自己想想为什么失败，然后把反思写下来，下次跑之前先读一遍？

```
Actor（执行器）:     跑一次任务
Evaluator（评估器）:  打分——通过还是失败？
Self-Reflector（反思器）: 如果失败，写一句反思
Episodic Memory（情景记忆）: 把反思存起来，下次跑之前塞到提示词里
```

效果：在 ALFWorld 上超过 ReAct 和其他非微调基线；在 HumanEval 代码生成上达到当时的 SOTA。**全程没有一次梯度更新。** 就是自然语言写下来、存起来、下次读。

这个模式 2026 年已经无处不在：
- **Claude Code** 的 CLAUDE.md 和「保存到记忆」功能，就是 Reflexion 的工程化版本
- **Letta（原 MemGPT）** 的 sleep-time compute——在 Agent 不忙的时候跑反思器，把学到的经验写入记忆块
- **OpenAI Agents SDK** 通过自定义 Guardrail 实现类似的「不合格就回退重试」逻辑

> ⚠️ **Reflexion 的陷阱：记忆腐烂。** 反思会不断积累，有些过时了、有些是错的、有些只是那次运行刚好抽风。不去清理的话，情景记忆会越跑越慢、越来越毒。解决方案：定期压缩、给反思加「保质期」（TTL）、或者像 Letta 那样开一个独立的清理 Agent。

#### Tree of Thoughts：当「一条道走到黑」不够用

Chain-of-Thought 是一条直线。第一步错了，后门每一步都在错误的地基上盖楼。在「4 个数字用加减乘除凑 24」这种游戏上，GPT-4 用 CoT 只有 4% 的正确率。

**Tree of Thoughts（思维树）** 把推理变成搜索：每个节点是一个「中间想法」，可以分叉出多个候选，LLM 自己给每个候选打分，只保留高分分支继续探索。

效果（据 ToT 论文实验数据）：Game of 24 从 4% 飙到了 74%。代价是 token 消耗是 CoT 的 100-1000 倍。

**LATS（Language Agent Tree Search）** 更进一步，把 ToT + ReAct + Reflexion 统一到蒙特卡洛树搜索（MCTS）框架下。据 LATS 论文，HumanEval pass@1 达到 92.7%。

现实是：大部分生产 Agent 不用 ToT 或 LATS。太贵了。只有在正确性远重要于延迟的场景（代码生成跑测试、深度研究探索多路径）才会启用。**实际做法是在 Agent Loop 里加一个判断：任务复杂度超过阈值才开搜索。**

#### Self-Refine + CRITIC：生成→挑刺→修改→再挑刺

Self-Refine 用一个模型扮演三个角色：生成器、评审者、修改者。生成一份输出→评审者挑毛病→修改者参考历史修正→再评审→直到通过。

一个关键发现：**修改器必须看到完整历史（之前所有输出和评审意见），否则质量暴跌。**

但 Self-Refine 有个致命弱点——LLM 自己评自己不可靠。一个幻觉往往在产生它的模型看来「挺有道理」。

**CRITIC** 修复了这一点：把「评审」步骤交给外部工具——搜索引擎核实事实、代码解释器验证代码、计算器验证算术、单元测试验证逻辑。**CRITIC = Self-Refine + 外部验证**。

---

## ✍️ 手写实现：Agent Loop 的核心骨架

说了这么多概念，代码到底长什么样？

下面这段代码是整个 Agent 工程的基础——一个完整的 ReAct Agent Loop。不依赖任何框架，纯 Python 标准库。**理解了这段代码，你就理解了 Claude Code、Cursor、Devin 的底层引擎。**

```python
class AgentLoop:
    """
    ReAct Agent Loop 的核心实现。
    
    五个要素：
    1. messages（消息缓冲区）
    2. tool_registry（工具注册表）
    3. stop 条件（finish / 无工具调用 / 达到预算）
    4. max_turns（轮次预算）
    5. _format_observation（观察格式化器）
    """
    
    def __init__(self, llm, tool_registry, max_turns=50):
        self.llm = llm                    # 大语言模型接口
        self.tool_registry = tool_registry # 工具名 → 可执行函数
        self.max_turns = max_turns         # 防止死循环的硬上限
        self.trace = []                    # 完整轨迹记录
    
    def run(self, user_query: str) -> str:
        """主循环：观察 → 思考 → 行动 → 观察 → …… → 完成"""
        messages = [{"role": "user", "content": user_query}]
        
        for turn in range(self.max_turns):
            # ---- 步骤 1：模型思考 ----
            response = self.llm.chat(messages, tools=self.tool_registry.schemas)
            messages.append(response)  # 追加助手消息到缓冲区
            
            # ---- 步骤 2：检查是否要调工具 ----
            tool_calls = response.get("tool_calls", [])
            
            # 停止条件 1：模型没调任何工具 → 任务完成
            if not tool_calls:
                return response["content"]
            
            # ---- 步骤 3：执行工具调用 ----
            for tc in tool_calls:
                # 工具名在注册表里吗？
                tool = self.tool_registry.get(tc["name"])
                if not tool:
                    obs = f"错误：工具 '{tc['name']}' 不存在。可用工具：{self.tool_registry.names}"
                else:
                    try:
                        # 参数校验 + 执行
                        result = tool.execute(tc["arguments"])
                        obs = str(result)
                    except Exception as e:
                        # 停止条件 2：工具执行出错 → 格式化成可读观察
                        obs = f"错误：调用 {tc['name']} 时发生异常：{e}"
                
                # ---- 步骤 4：观察回传 ----
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": obs
                })
                self.trace.append({
                    "turn": turn, "action": tc, "observation": obs
                })
        
        # 停止条件 3：达到最大轮次 → 强制结束
        return "达到最大执行轮次，任务未完成。"
```

```python
class ToolRegistry:
    """工具注册表：管理 Agent 可调用的所有工具"""
    
    def __init__(self):
        self._tools = {}  # name → Tool
    
    def register(self, tool: Tool):
        """注册一个工具。每个工具包含 name, description, input_schema, execute"""
        self._tools[tool.name] = tool
    
    @property
    def schemas(self) -> list[dict]:
        """生成供 LLM 使用的工具定义列表（OpenAI Function Calling 格式）"""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,  # ← 最关键！模型靠这个选工具
                    "parameters": t.input_schema
                }
            }
            for t in self._tools.values()
        ]
    
    def get(self, name: str):
        """按名查找工具，不存在返回 None"""
        return self._tools.get(name)
    
    @property
    def names(self) -> list[str]:
        """返回所有工具名（用于错误提示）"""
        return list(self._tools.keys())
```

**这段代码的核心设计决策**:

1. **为什么工具执行出错不直接抛异常？** → 因为 Agent 需要从错误中恢复。把错误格式化成结构化的观察文本传回模型，模型可以读错误信息并调整下一次调用。如果直接崩溃，Agent 就死了。
2. **为什么用字典而不是强类型？** → 这是演示版。生产环境会用 Pydantic/Zod 做参数校验和类型转换，但这个核心结构没变。
3. **为什么要记录 trace？** → 调试 Agent 是地狱难度的——40 步里哪一步错了，没有完整轨迹根本定位不了。

**运行效果**（用脚本化的 ToyLLM 模拟）:

```
[Turn 1] Thought: 需要查法国首都
[Turn 1] Action: search("capital of France")
[Turn 1] Observation: Paris
[Turn 2] Thought: 知道首都是巴黎，查人口
[Turn 2] Action: search("population of Paris")
[Turn 2] Observation: ~2.1 million
[Turn 3] Thought: 答案已完整
[Turn 3] Response: 法国首都是巴黎，人口约 210 万。
```

---

## 🚀 工程调优与框架选型

### 你已经会造轮子了，下一步选什么框架？

一旦你掌握了 Agent Loop 的本质，选框架就变成了**工程体验和运维需求**的权衡，而不是「哪种控制流更好」——控制流全是 ReAct。

| 框架 | 核心差异 | 适合场景 |
|------|---------|---------|
| **Claude Agent SDK** | 开箱即用的子 Agent、生命周期钩子、工具生态 | 想快速构建 Claude 驱动的 Agent 产品 |
| **OpenAI Agents SDK** | Handoff（Agent 间交接）、Guardrails（安全门）、Session 管理 | 需要多 Agent 交接和可观测性 |
| **LangGraph** | 有状态的图结构，每一步自动 checkpoint | 需要断点续跑、人机协同审批 |
| **AutoGen v0.4** | 异步消息传递的 Actor 模型 | 多 Agent 并发协作 |
| **CrewAI** | 角色模板（role + goal + backstory） | 快速搭建「团队协作」型 Agent |
| **Dify / FastGPT** | 低代码/无代码 Agent 搭建 | 不需要写代码的产品或运营团队 |

> 💡 **一句话选型指南**：如果你需要状态持久化，选 LangGraph。如果你需要多 Agent 异步通信，选 AutoGen。如果只是快速上手，直接用 Claude/OpenAI SDK 就好。**但不管你选哪个，你本质上跑的都是本文开头那个不到 200 行的 Agent Loop。**

---

## 📦 回顾一下

这篇文章里，我们建立了一个完整的 Agent 工程认知地图：

```
Agent Loop（ReAct）
  ├── 工具定义（Tool Schema + JSON Schema 校验）
  ├── 函数调用（Function Calling + BFCL 评测）
  ├── 计划-执行（ReWOO → Plan-and-Execute → Plan-and-Act）
  ├── 口头反思（Reflexion → Episodic Memory → Sleep-time Compute）
  ├── 树搜索（Tree of Thoughts → LATS → MCTS）
  ├── 迭代改进（Self-Refine → CRITIC → Evaluator-Optimizer）
  └── 工作流模式（Anthropic 五模式：链式/路由/并行/编排/评估）
```

如果你想现在就动手，用上面的 Agent Loop 代码骨架，把你的第一个工具（比如一个查天气的函数）注册进去，换成真实的 LLM API，五分钟就能跑起来。

---

## 🔮 下一篇

下一篇文章深入 Phase 14 的第二站：**「工具定义与 Schema——让 Agent 学会使用工具」**。

会聊工具 Schema 的三要素：
- 工具名（name）—— snake_case 命名规则和为什么不用 camelCase
- 工具描述（description）——「Use when... Do not use for...」模式，描述质量直接决定模型选错工具的概率
- 参数定义（input_schema）—— JSON Schema 校验、枚举约束、类型强制转换的工程实现

> 🔧 工具 Schema 是 Agent 的地图。下一篇文章教你怎么画这张地图，让模型准确率从 62% 飙到 89%。

---

## 📚 延伸阅读

- 源项目 Phase 14 完整目录：[AI Engineering from Scratch — Agent Engineering](https://github.com/rohitg00/ai-engineering-from-scratch/tree/main/phases/14-agent-engineering)
- [Yao et al., ReAct (arXiv:2210.03629)](https://arxiv.org/abs/2210.03629) —— Agent Loop 的开山论文
- [Anthropic, Building Effective Agents](https://www.anthropic.com/engineering/building-effective-agents) —— 什么时候用 Agent，什么时候用 Workflow
- [Schick et al., Toolformer (arXiv:2302.04761)](https://arxiv.org/abs/2302.04761) —— 自监督工具学习
- [Shinn et al., Reflexion (arXiv:2303.11366)](https://arxiv.org/abs/2303.11366) —— 口头强化学习
- [Berkeley Function Calling Leaderboard V4](https://gorilla.cs.berkeley.edu/leaderboard.html) —— 函数调用能力评测

---

🧭 Phase 14 / 20 · Agent Engineering 入门篇
📋 第一篇 / 20 · Agent Engineering 入门
📋 下一篇：Phase 14 · 工具定义与 Schema——让 Agent 学会使用工具（已发布）

💡 如果这篇文章对你有帮助，欢迎 **点赞 · 在看 · 分享**

> *「不要调 API，从零手写。不是学会用 AI，而是学会造 AI。」*
