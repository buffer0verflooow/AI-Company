---
title: Fable 5 模型降级攻击 + 记忆劫持
swarm: capture
swarm_tags: [model-downgrade, memory-attack, prompt-injection, opus, fable5, jailbreak]
swarm_agent: obsidian
swarm_source: article
swarm_intent: analyze
tags: [articles, track-a, published]
created: 2026-08-11
published: 2026-08-11
---

# Fable 5 模型降级攻击 + 记忆劫持

来源: embracethered.com (@wunderwuzzi23) 2026-04-17 研究 + 2026-08-09 推文。翻译整理 + 图解, 已发布公众号 (2026-08-11)。

## 技术结论

- **降级攻击**: 给 Fable 5 攻击加 forbidden topic → 安全分类器误报 → 静默降级到 Opus 4.8 → 4 个月前针对旧模型的攻击手法原地复活
- **记忆劫持链**: ChatGPT 生成对抗图片(黑底深色文字 + 高亮 `antml memory`)→ Opus 分析图片被社交工程劫持 → memory 工具 view + add × 4 → 虚假记忆持久化(Neo / 43 岁 / NASA 宇航员 / 喜欢冰淇淋饼干)
- 成功率: 定向对抗样本重复试验 **5/10 (50%)**; 对比 Mythos 系统卡 Opus 4.6 Thinking k=100 累计 ASR 21.7%, k=1 0.2%
- 该对抗样本发布约 24 小时后 ASR 归零(分类器调整或缓解更新, 原因不明)
- 观察: 空记忆库降低写入门槛; "合理性"载荷(NASA 宇航员)比离谱载荷容易被抓,"喜欢冰淇淋"更容易蒙混; 指令来源(图片 vs 直接输入)和"是否值得记"是两个变量
- 防御现状: Anthropic 在 memory_user_edits 工具加 critical_reminders(禁存敏感数据/禁存逐字命令/冲突检查)
- MCP 服务器通常比 memory 工具更容易被劫持成功(通用工具方差大难防御)
- 社区佐证: anthropics/claude-code issue #66728 — 分类器误报导致 Fable 5 1M 静默降级 Opus 4.8

## 可复用知识条目

1. **模型降级 = 攻击面**: 降级把新模型防御带回旧模型水平, 所有旧研究重新可用; 攻击者只需找到触发降级的开关, 不需要新漏洞
2. **记忆持久化攻击链**: 图片谜题(社交工程)→ 工具调用劫持 → 长期记忆污染 → 影响所有未来对话
3. ASR 数据对比是"基准平均值 ≠ 定向利用潜力"的证据
4. 防御信号: critical_reminders 说明厂商在补记忆写入防线; 空记忆库门槛更低是反直觉发现

## 产品信号

- **潜在产品**: 模型降级监控(检测静默降级事件) / Agent 记忆安全测试用例集
- 需求证据: 降级事件真实发生(推文 + issue #66728), 企业 Agent 大规模接 memory 工具后此攻击面扩大
- 实验品形态: 降级监控脚本(周期性检测模型版本一致性) / memory 工具安全测试集(喂给评测服务)
- 变现路径: 工具实验 → AI 安全评测服务(记忆安全维度)
