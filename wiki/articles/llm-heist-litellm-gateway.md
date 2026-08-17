---
title: LLM Heist — 劫持 LiteLLM AI 网关
swarm: capture
swarm_tags: [ai-gateway, litellm, traffic-interception, red-team, api-security]
swarm_agent: obsidian
swarm_source: article
swarm_intent: analyze
tags: [articles, track-a, published]
created: 2026-08-11
published: 2026-08-09
---

# LLM Heist — 劫持 LiteLLM AI 网关

来源: embracethered.com (@wunderwuzzi23), 2026-08-03。翻译整理, 已发布公众号 (2026-08-09)。

## 技术结论

- LiteLLM 是 AI 网关, 统一接口 + 持有后端 LLM 提供商密钥 → **高价值目标**
- 四种对抗目标: ① IP/数据窃取(截获上下文/PII/机密) ② 未授权推理(盗用凭证烧对方账户) ③ 响应伪造与工具调用注入(向 AI 客户端注入文本/工具调用) ④ 模型蒸馏与行为克隆(收集实时对话训练数据)
- 攻击面: 网关即中间人 — 流量重路由、截获、修改一站式
- 防御视角: 无监控/无安全控制的网关, 多种攻击可同时发生且不被察觉
- 通用性: 研究基于 LiteLLM, 原理适用于其他 AI 网关产品

## 可复用知识条目

1. **AI 网关 = 密钥集中点 + 流量中继点**, 是 AI 基础设施里攻击收益最高的目标之一
2. 四种对抗目标的完整分类法(TTP 框架可直接用于授权红队操作)
3. 响应伪造/工具调用注入 = 对 AI 客户端的供应链级污染, 客户端无感知
4. 防御缺口: 网关侧缺乏流量完整性监控是普遍现状

## 产品信号

- **潜在产品**: AI 网关安全检测(流量异常检测/密钥泄露监控/响应完整性校验)
- 需求证据: 网关被劫持后客户端完全无感, 企业自建网关大量存在(LiteLLM 是主流开源方案)
- 实验品形态: 网关流量检测规则集 / 巡检脚本 / 检测报告模板
- 变现路径: 工具实验 → 评测服务 → (企业化后)安全检测服务
