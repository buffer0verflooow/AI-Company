---
title: 函数调用——Agent 拿到地图后怎么开车
cover: /home/media/workspace/company/projects/ai-edu-series/articles/assets/cover-03-function-calling.png
---

# Phase 14｜函数调用——Agent 拿到地图后怎么开车

> 工具 Schema 写好了，注册表搭好了。但模型**怎么决定**调哪个工具？**什么时候调**？**并行调还是串行调**？函数调用不是 JSON 格式化——它是 Agent 和 LLM 之间最精密的握手协议。

---

📖 本系列基于开源项目 [AI Engineering from Scratch](https://github.com/rohitg00/ai-engineering-from-scratch)（503 节课 · 20 阶段 · MIT 协议），用中文重新梳理 AI 全栈知识体系，从数学基础一路写到多智能体集群。

---

**📌 这篇文章聊什么**：深入函数调用的完整链路——从 LLM 如何输出 `tool_calls`，到 `tool_choice` 的三种模式如何改变 Agent 行为，再到流式调用中参数分片到达的缓冲和解析策略

**⏱️ 预计阅读**：18 分钟

**🛠️ 涉及技术**：Function Calling · tool_choice · 流式工具调用 · 并行调用 · tool_use_id · strict mode · naive vs smart 派发

**🎯 内容地图**：理解 Function Calling 的完整运行机制 + 一段能直接用的工具派发器代码

---

## 🔥 工具 Schema 只是地图，Function Calling 才是开车

上篇文章我们写好了工具 Schema——名字、描述、参数定义。就像你拿到了一张标好加油站和出口的地图。

但地图不会自己开车。

模型拿到工具列表后，真正需要回答的问题只有三个：

1. **要不要调工具？** —— 还是直接用文本回答就够了？
2. **调哪个？** —— 三个工具都 match 了关键词，选谁？
3. **怎么调？** —— 参数填什么？串行还是并行？

这三个问题看起来简单，但每一个选错，Agent 的行为就崩塌了。**Function Calling 不是「模型输出一段 JSON」，而是 LLM 推理能力在工具空间里的具象化。**

先看一个完整的调用链路：

```
用户: "北京和上海今天天气怎么样？"

模型: → 输出 tool_calls
  [
    {id: "call_1", function: {name: "get_weather", arguments: '{"city":"北京"}'}},
    {id: "call_2", function: {name: "get_weather", arguments: '{"city":"上海"}'}}
  ]

Agent Runtime:
  → 解析 tool_calls 列表
  → 查注册表验证工具存在
  → 校验参数格式
  → 并行执行 call_1 + call_2
  → 把结果用 tool_use_id 对号入座回传

模型: → 收到两个结果，组织自然语言回复
  "北京今天晴，22°C。上海阴有小雨，18°C。"
```

下面我们把这个链路拆开看每一步在做什么。

---

## 🧠 核心概念

### 1. 模型怎么输出 Function Call——不只是 JSON

很多人以为 Function Calling 就是「模型输出一个特别格式的 JSON」。这个理解对了一半——**现代 LLM 的 Function Calling 是模型架构内的原生能力，不是提示词工程**。

关键区别：

| 方式 | 原理 | 可靠性 |
|------|------|:---:|
| 提示词诱导（旧） | 在 system prompt 里写「请用 JSON 格式回复」，让模型「猜测」工具调用格式 | 低——JSON 格式经常错、幻觉高 |
| 原生支持（新） | 模型训练时就学会了 `tool_calls` 这个输出通道，分词器里有 `<tool_call>` 等特殊 token | 高——输出走的是训练好的通道 |

2025-2026 年的前沿模型（GPT-4、Claude 3.5 系列）在训练阶段就把 tool use 作为一等公民来优化。它们不需要你提示「如果你想调工具，请输出这个格式……」——**你传 tools 参数进去，模型自动决定要不要用、用哪个、怎么用。**

这引出了一个反直觉的事实：**Function Calling 的质量，主要取决于工具描述的质量，而不是调用格式的工程实现。** 上篇文章讲的 Schema 设计决定了 80% 的准确率。

### 2. tool_choice——控制模型的「调用意愿」

`tool_choice` 是 Function Calling 里最被低估的参数。它控制模型「倾向于调工具还是用文本回答」，有三种模式：

```
tool_choice: "auto"      → 模型自己决定（默认）
tool_choice: "required"  → 必须调工具，不许直接用文本回答
tool_choice: "none"      → 禁止调工具，只能用文本回答
```

**每种模式意味着完全不同的 Agent 行为：**

#### auto（默认）——让模型自己判断

```
用户: "你好"
模型: → 不调工具，直接回复 "你好！有什么可以帮你的？"

用户: "北京天气"
模型: → 调用 get_weather(city="北京")
```

`auto` 模式下模型根据上下文判断这个请求是否需要工具。用的好，省 token；用得不好——模型可能在该调工具的时候选择蒙一个答案。**LLM 本质上是个自动补全引擎，它永远倾向于「说点什么」而不是「承认不知道」。** 当它不确定该不该调工具时，默认行为是猜一个文本回答。

这就是幻觉产生的温床。

#### required——强制调工具

```
用户: "你好"
模型: → 必须调工具，但不知道调哪个
  → 可能调用一个不相关的工具，可能输出格式错误的 tool_call
```

`required` 模式不给模型退路——它不能用文本「搪塞」，必须找到一个工具来调用。这在某些场景是必要的（比如你明确知道任务需要工具才能完成），但**滥用 required 会让模型在没有合适工具时生成无意义的调用**。

#### none——禁止调工具

```
用户: "帮我查天气"
模型: → 不能调工具，只能文本回复
  → "很抱歉，我无法查询实时天气信息。"
```

`none` 模式关闭所有工具。用于多轮对话中的纯文本轮次，或者当你需要隔离「思考阶段」和「执行阶段」时。

#### 实际工程中的组合用法

```
# 第一轮：用 required 强制模型分析需求
messages = [{"role": "system", "content": "分析用户需求并选择合适的工具"}]
response = llm.chat(messages, tools=tools, tool_choice="required")

# 第二轮：用 auto 让模型自己决定
messages.append({"role": "tool", ...})
response = llm.chat(messages, tools=tools, tool_choice="auto")
```

**生产经验**：大多数 Agent 用 `auto` 就够了。`required` 适合 ReWOO 模式中的「规划器」阶段——你已经明确知道这轮需要输出计划，不需要模型犹豫。

### 3. ⭐ 流式工具调用——参数是「碎片」到达的

这是 Function Calling 在生产环境最容易被忽略的坑。当开启流式输出（streaming）时，一个工具调用的 JSON 参数不是一次性到达的，而是**分成多个 delta chunk 陆续到达**。

```
Chunk 1: {tool_calls: [{index: 0, id: "call_1", function: {name: "get_weather"}}]}
Chunk 2: {tool_calls: [{index: 0, function: {arguments: '{"city"'}}]}
Chunk 3: {tool_calls: [{index: 0, function: {arguments: ':"北京"'}}]}
Chunk 4: {tool_calls: [{index: 0, function: {arguments: '}'}}]}

# 拼起来才是完整参数: {"city":"北京"}
```

更复杂的情况：**模型可能同时输出多个工具调用**，每个的 `arguments` 在不同 chunk 里交叠出现。你的派发器必须能处理：

1. 多个 tool_call 同时流式到达（`index` 区分）
2. 每个 tool_call 的参数分片到达，需要**按 index 分组累积**
3. 参数可能跨 chunk 分段（一个 JSON key 跨两个 chunk）
4. 可能在某个 chunk 里突然冒出新的 tool_call（模型中途决定多调一个）

**核心代码模式**：

```python
# 流式工具调用缓冲器
pending_calls = {}  # {index: {id, name, arguments_buffer}}

for chunk in stream:
    for tc in chunk.get("tool_calls", []):
        idx = tc["index"]
        if idx not in pending_calls:
            pending_calls[idx] = {"id": None, "name": None, "args": ""}

        call = pending_calls[idx]
        if "id" in tc:
            call["id"] = tc["id"]
        if tc.get("function", {}).get("name"):
            call["name"] = tc["function"]["name"]
        if tc.get("function", {}).get("arguments"):
            call["args"] += tc["function"]["arguments"]

# 流结束时：所有参数已拼完
for idx, call in sorted(pending_calls.items()):
    args = json.loads(call["args"])
    result = execute(call["name"], args)
```

### 4. 并行 vs 串行——什么时候一起调，什么时候排队

模型返回多个 `tool_calls` 时，你有一个关键选择：并行执行还是串行执行。

```
# 并行——两个调用互不依赖
get_weather("北京")  ──┐
                       ├── 同时跑
get_weather("上海")  ──┘

# 串行——第二个依赖第一个的结果
search("法国首都") → 得到 "巴黎"
  → get_weather("巴黎")  ← 依赖上一步的结果
```

**判断逻辑**：看 tool_use_id。如果三个调用的 tool_use_id 在同一个 assistant 消息里，说明模型认为它们互不依赖，可以并行。如果第二个调用是在第一个的结果返回后才生成的，那自然是串行的。

然而现实中很多 Agent 框架做了**乐观并行**——同一轮的调用不管有没有隐式依赖全部并行派发。这在大语言模型表现越来越好（幻觉越来越少）的今天，通常是对的。

---

## ✍️ 手写实现：一个生产级的工具派发器

前面把原理都讲清楚了，来看代码。下面这个 `ToolDispatcher` 把工具校验、参数验证、流式缓冲、并行派发全部打包在一起。**理解了这段代码，你就理解了 Agent Runtime 最核心的执行引擎。**

```python
import json
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ToolCall:
    """一次工具调用的完整描述"""
    id: str
    name: str
    arguments: dict


@dataclass
class ToolResult:
    """一次工具调用的执行结果"""
    tool_call_id: str
    success: bool
    content: str


class ToolDispatcher:
    """生产级工具派发器——校验、执行、错误恢复的完整管线"""

    def __init__(self, registry):
        self.registry = registry  # ToolRegistry 实例

    def dispatch(self, call: ToolCall) -> ToolResult:
        """派发单个工具调用"""

        # ── Gate 1: 工具存在性检查 ──
        tool = self.registry.get(call.name)
        if not tool:
            return ToolResult(
                call.id, False,
                f"工具 '{call.name}' 未注册。"
                f"可用工具: {self.registry.names}"
            )

        # ── Gate 2: 参数校验 ──
        try:
            validated = tool.validate(call.arguments)
        except ValueError as e:
            return ToolResult(
                call.id, False,
                f"参数校验失败: {e}\n"
                f"正确格式示例: {tool.schema_example()}"
            )

        # ── Gate 3: 执行 ──
        try:
            result = tool.execute(validated)
            return ToolResult(call.id, True, tool.format_result(result))
        except Exception as e:
            return ToolResult(
                call.id, False,
                f"执行错误 ({type(e).__name__}): {e}\n"
                f"提示: {tool.troubleshooting_hint()}"
            )

    def dispatch_all(self, calls: list[ToolCall]) -> list[ToolResult]:
        """并行派发——所有调用同时执行"""
        # 如果 calls 里没有相互依赖（同一个 assistant 消息里的调用），
        # 可以全部并行。实际部署用 asyncio.gather 或用线程池。
        return [self.dispatch(c) for c in calls]


class StreamingToolBuffer:
    """流式工具调用缓冲器——把分片参数拼成完整调用"""

    def __init__(self):
        self._calls: dict[int, dict] = {}

    def ingest(self, tool_call_delta: dict):
        """接收一个 chunk 的工具调用 delta"""
        idx = tool_call_delta.get("index", 0)
        if idx not in self._calls:
            self._calls[idx] = {"id": None, "name": None, "arguments_chunks": []}

        call = self._calls[idx]
        if "id" in tool_call_delta:
            call["id"] = tool_call_delta["id"]
        func = tool_call_delta.get("function", {})
        if "name" in func:
            call["name"] = func["name"]
        if "arguments" in func:
            call["arguments_chunks"].append(func["arguments"])

    def finalize(self) -> list[ToolCall]:
        """流结束后，把所有缓冲拼成完整 ToolCall 列表"""
        result = []
        for idx in sorted(self._calls.keys()):
            c = self._calls[idx]
            args_str = "".join(c["arguments_chunks"])
            result.append(ToolCall(
                id=c["id"],
                name=c["name"],
                arguments=json.loads(args_str) if args_str else {}
            ))
        return result


# ── 完整调用示例 ──

# 1. 流式接收
buffer = StreamingToolBuffer()
for chunk in llm.stream(messages, tools=tool_schemas):
    for delta in chunk.get("tool_calls", []):
        buffer.ingest(delta)

# 2. 流结束，拿到完整调用列表
calls = buffer.finalize()

# 3. 派发执行
dispatcher = ToolDispatcher(tool_registry)
results = dispatcher.dispatch_all(calls)

# 4. 结果回传（注意 tool_call_id 对号入座）
for r in results:
    messages.append({
        "role": "tool",
        "tool_call_id": r.tool_call_id,
        "content": r.content
    })

# 5. 下一轮
response = llm.chat(messages, tools=tool_schemas)
```

### 三段式设计决策

这套代码有几个刻意为之的设计：

**1. 错误的「形状」比错误本身重要**

注意到返回的 `ToolResult` 不是简单的 `"失败了: xxx"`，而是包含了三层信息：
- 哪一步失败了（工具不存在 / 参数校验 / 执行错误）
- 具体的错误原因
- **修复提示**（可用工具列表、正确格式示例、排查建议）

模型靠这些信息自我修正。第一层的 `"工具不存在"` 和第三层的 `"参数校验失败"` 需要完全不同的修正策略——模型必须知道自己在哪一关挂了。

**2. buffer 用 index 不是 name 做 key**

为什么 `StreamingToolBuffer` 用 index 而不是工具名做 key？因为模型可能**并行调用同一个工具两次**：

```
get_weather(city="北京")  ← index=0
get_weather(city="上海")  ← index=1
```

如果用 name 做 key，第二个北京就会覆盖第一个上海。

**3. 先全部缓冲，再全部派发**

流式阶段只做拼接，不执行。等到流完全结束了，一次性拿到完整的调用列表，再并行派发。这避免了「参数还没到齐就开始执行」的竞态问题。

---

## 🚀 生产实践：避开 Function Calling 的三个大坑

### 坑一：strict 模式选了但工具描述没对齐

OpenAI 在 2024 年末推出了 `strict` 模式——要求模型输出的 JSON 严格遵守你定义的 JSON Schema。开启方式：

```python
{
    "type": "function",
    "function": {
        "name": "get_weather",
        "strict": True,  # ← 开启
        "parameters": {...}
    }
}
```

但 `strict` 模式有个陷阱：**你的 JSON Schema 必须是 strict 兼容的。** 它不支持 `default` 字段、`anyOf`、某些嵌套 `$ref`。如果你的 Schema 里用了这些，开启 strict 后模型会直接拒绝调用。

**生产规则**：
- 参数简单（3-5 个字段、没有复杂嵌套）→ 开 strict
- 参数复杂、有枚举值、有可选字段 → 用 auto 模式，在派发器里做二次校验

### 坑二：并行调用中一个挂了，全挂还是部分挂？

现实问题是：模型调了三个工具，第二个超时了。Agent 怎么办？

两种策略：

| 策略 | 做法 | 适用场景 |
|------|------|---------|
| **全部取消** | 一个挂了，停止其余，把所有结果（成功+失败）一起回传 | 工具之间有逻辑依赖 |
| **部分返回** | 成功的正常回传，失败的标注错误 | 工具之间完全独立（如并行查多个城市的天气） |

**默认选「部分返回」**。工具调用之间如果有依赖，模型会在上一轮结果回来后、下一轮才发起——所以我们同时拿到的调用列表，本身就是模型认为可以并行的。

### 坑三：token 预算被 Function Calling 吃光

一个容易被忽略的事实：**tools 参数本身占 token。** 把你注册的所有工具的 JSON Schema 全部塞进请求里，每次调用都消耗 prompts token。

10 个工具、每个 200 字符 → 2000 token/turn。一个 40 轮的 Agent 运行，光是工具 Schema 就占 80k token。

**优化方案**：
- **按需注册**：根据上下文只传 2-3 个相关工具，而不是全量
- **分层注册**：先注册「分类工具」（帮模型判断任务类型），确定领域后再注册该领域的专用工具
- **工具描述精简**：描述只写「Use when...」，不写内部实现细节

---

## 📦 回顾一下

这篇文章把 Function Calling 的完整链路拆到底了：

```
工具 Schema（#02 讲的地图）
  ├── tool_choice（auto / required / none）          ← 控制调用意愿
  ├── 流式调用（StreamingToolBuffer）                 ← 碎片拼接
  ├── 并行 vs 串行（同一 assistant 消息 = 可并行）     ← 执行策略
  ├── 派发器（ToolDispatcher）                        ← 三级校验管线
  └── 错误恢复（失败回传 + 模型自修正）                ← 不崩溃
```

如果上篇文章给的是地图，这篇文章教你怎么握方向盘、看路标、避开坑。

---

## 🔮 下一篇

下一篇文章：**「Agent 工作流模式——Chain / Parallel / Router」**。当你的 Agent 不再只有一根筋地跑 ReAct 循环，而是可以用链式、路由、并行三种模式编排更复杂的任务时，Agent 才真正从「工具人」变成「可以跑管线的工程师」。

---

## 📚 延伸阅读

- 源项目 Phase 14 完整目录：[AI Engineering from Scratch — Agent Engineering](https://github.com/rohitg00/ai-engineering-from-scratch/tree/main/phases/14-agent-engineering)
- [OpenAI — Function calling guide](https://platform.openai.com/docs/guides/function-calling) — strict mode、并行调用、流式处理
- [Anthropic — Tool use overview](https://docs.anthropic.com/en/docs/agents-and-tools/tool-use/overview) — tool_use 和 tool_result 语义
- [Berkeley Function Calling Leaderboard V4](https://gorilla.cs.berkeley.edu/leaderboard.html) — 函数调用能力评测
- [OpenAI — Streaming](https://platform.openai.com/docs/api-reference/streaming) — delta chunk 的完整结构定义

---

🧭 Phase 14 / 20 · Agent Engineering · Function Calling 篇
📋 上一篇：Phase 14 · 工具定义与 Schema——让 Agent 学会使用工具
📋 下一篇：Phase 14 · Agent 工作流模式——Chain / Parallel / Router

💡 如果这篇文章对你有帮助，欢迎 **点赞 · 在看 · 分享**

> *「Function Calling 不是 JSON 格式化——它是 LLM 把推理能力投射到工具空间里的那一瞬间。」*
