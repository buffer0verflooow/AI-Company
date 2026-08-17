---
title: LiteLLM 供应链攻击 — 2500 家企业 43.4 万条 CI/CD 流水线暴露
swarm: capture
swarm_tags: [supply-chain, litellm, ci-cd, ai-gateway, exposure]
swarm_agent: obsidian
swarm_source: article
swarm_intent: analyze
tags: [articles, track-a, published]
created: 2026-08-11
published: 2026-08-11
---

# LiteLLM 供应链攻击 — 2500 家企业 43.4 万条 CI/CD 流水线暴露

来源: FreeBuf (2026-08-11), 情报采集 security-intel 捕获。原创盘点, 已发布公众号 (2026-08-11)。

## 技术结论

- LiteLLM 供应链攻击波及 **2,500 家企业、434,000 条 CI/CD 流水线** 暴露
- LiteLLM 作为 AI 网关基础设施, 供应链攻击面 = 依赖树污染 → 下游全部 AI 应用受影响
- CI/CD 流水线暴露 = 密钥/凭证/部署链路的连锁风险

## 可复用知识条目

1. AI 网关的供应链攻击 = 单点污染、多点爆炸: 一个依赖项被投毒, 下游 2500 企业受影响
2. CI/CD 流水线是供应链攻击的放大器: 流水线里的密钥即攻击者的提款机
3. AI 基础设施(网关/代理)的供应链风险是 2026 年真实发生的事件, 不是理论

## 产品信号

- **潜在产品**: 供应链风险扫描(AI 依赖树 / 流水线暴露面)
- 需求证据: 事件真实发生且规模巨大, 暴露面数据可量化(2500 企业/43.4 万流水线)
- 实验品形态: AI 依赖树扫描脚本 / 流水线密钥泄露检测 / 供应链暴露报告
- 与 LLM Heist 文章的产品信号合并: **AI 网关安全 = 检测 + 供应链两个面**
