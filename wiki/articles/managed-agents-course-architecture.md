---
title: 托管式 AI Agent 架构课件 — 服务端循环、会话与工具调用
swarm: capture
swarm_tags: [managed-agent, agent-loop, session, event-stream, state-machine, harness, tool-calling, claude]
swarm_agent: obsidian
swarm_source: article
swarm_intent: analyze
tags: [knowledge, agent-engineering, track-c, feishu-course]
created: 2026-08-11
source: https://my.feishu.cn/docx/Jy8XdmON5oFXnHxTUqQcv5dBnQb
---

# 托管式 AI Agent 架构课件 — 服务端循环、会话与工具调用

来源: 飞书文档 (Claude Managed Agents 视频中文课件, 原视频 36:49, 8 单元)。2026-08-11 Firecrawl 抓取, 存 evidence/managed-agents-course-2026-08-11/。

## 技术结论

- **演进三层**: Messages API(原始模型访问)→ Agent SDK(Claude Code 能力进可编程 harness)→ Managed Agents(运行时基础设施托管: 上下文管理/compaction/工具执行/会话恢复/鉴权/可观测性)
- **开发者 vs 平台分工**: 开发者提供 task + agent config + 自定义工具逻辑(MCP/Skills); 托管 harness 负责把 Agent 跑起来的运行时
- **三件套**: Agent(定义)/Environment(运行环境)/Session(会话)组合成可持续任务; "脑—手"解耦 = 推理循环与工具执行分离
- **事件协议**(Session 说的事件, 非请求/响应):
  - 发送: user.message / user.custom_tool_result / user.tool_confirmation / user.interrupt
  - 接收: agent.message / agent.tool_use / agent.custom_tool_use / agent.mcp_tool_use / session.status_idle / session.error
  - 协议示意: POST /v1/sessions/{id}/events, GET /v1/sessions/{id}/stream(课件注明不承诺可直接调用)
- **工具执行与循环分离**: Agent loop 在服务端, 客户端脚本保持事件 stream, 收到 agent.custom_tool_use → 调 handle_tool(name, args) → 发回 user.custom_tool_result; 同一工具协议可换数据源(json.load → Datadog client)
- **会话状态机**: idle(等待输入)↔ running(执行循环)→ rescheduling(瞬态错误自动重试)→ terminated(不可恢复); 外部事件/webhook 可唤醒或恢复 Session
- **关键边界**: "可恢复"≠"结果正确"——恢复的是会话与事件上下文, 业务动作仍需幂等性、权限和人工校验

## 可复用知识条目

1. Agent 产品演进的三层责任边界(Messages→SDK→Managed)是理解 Agent 安全攻击面的基础——每层托管了什么, 就多了什么可攻击的信任边界
2. 事件流协议(send/receive 类型表)是 Agent 通信的标准词汇表, 安全分析/监控 Agent 行为时按此分类
3. 状态机 idle/running/rescheduling/terminated = 监控 Agent 健康状况的状态模型, 可复用于 Agent 异常检测
4. "可恢复 ≠ 结果正确"——恢复会话不等于业务正确, 安全上同理: 会话重放/恢复点是攻击面
5. 工具执行与 loop 分离的设计 = MCP/自定义工具是独立信任域, 与记忆攻击(Fable 5)的结论呼应

## 产品信号

- **潜在产品**: Agent 运行时监控/审计(基于事件流协议与状态机); Agent 安全评估(三层责任边界视角)
- 与 C10 选题(多 Agent 为何要 Graph: Harness/Loop/Graph 辨析)直接相关——Harness 内部机制一手素材
- 与 Fable 5 记忆攻击文章形成互补: 攻击面(记忆/工具)vs 架构面(事件/状态/可观测)两端都覆盖
- 实验品形态: 事件流监控规则集 / 会话状态异常检测 / Agent 审计清单
