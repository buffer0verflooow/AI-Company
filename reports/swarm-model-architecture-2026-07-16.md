---
tags: [report, swarm, models, architecture]
created: 2026-07-16
author: reporter
evidence:
  - /home/pwn/.hermes/config.yaml (lines 1-5, 413-424, 432-445, 603-606, 666-679)
  - /home/pwn/workspace/scripts/swarm-orchestrate.py (lines 13-18, 224-228)
  - /home/pwn/workspace/company/finance/README.md (lines 35-45)
  - /home/pwn/workspace/company/automation/import_company_data.py (lines 333-385)
---

# 蜂群模型架构

## 概述

蜂群使用的模型体系为 **三层结构**：

| 层级 | 职责 | 模型数 |
|------|------|:---:|
| L1: Hermes 运行时 | Controller / 子 Agent / X Search | 3 |
| L2: 蜂群编排器 | 6-phase 多模型 Bug Bounty | 4 |
| L3: MoA 参考模型 | 多模型聚合（非蜂群主路径） | 3+1 |

---

## L1: Hermes 运行时（config.yaml，已验证）

| 用途 | 模型 | Provider | Base URL |
|------|------|----------|----------|
| Controller / 主 Agent | `deepseek/deepseek-v4-pro` | custom (Zenmux.ai) | `https://zenmux.ai/api/v1` |
| delegate_task 子Agent | `deepseek-v4-flash` | `custom:zenmux.ai` | `https://zenmux.ai/api/v1` |
| X Search (xurl) | `grok-4.20-reasoning` | — | 配置于 `x_search.model` |

证据源：`~/.hermes/config.yaml` lines 1-5, 413-416, 603-606。

### ⚠️ 证据纠正：delegate_task 的 Provider

分析师（analyst-01）声称 delegate_task 配置于 OhMyGPT 且有 87% 失败率，但 **当前 config.yaml 显示 provider 为 `custom:zenmux.ai`**，与主 Agent 共用同一 Zenmux 端点。

**历史叙事（来自 Memory，非文件证据）**：
- 2026-07-09 前，delegate_task 配置于 OhMyGPT，因间歇故障导致 87% 失败率
- 此后配置迁移至 Zenmux.ai（"Delegate 修复后 deepseek-v4-flash 正常"）

**当前状态**：delegate_task 的 model + provider 均指向 Zenmux.ai，与主 Agent 一致。OhMyGPT 不再用于 delegation。

---

## L2: 蜂群编排器（swarm-orchestrate.py，已验证）

`~/workspace/scripts/swarm-orchestrate.py` 定义了 6-phase 多模型 Bug Bounty 编排：

| Phase | 模型 | Provider 推测 | Agent 数 | 成本 | 用途 |
|-------|------|:---:|:---:|:---:|------|
| 1 Recon | `deepseek-chat` | OhMyGPT | ×4 | 极低 | 广域扫描 |
| 1b Audit | `gpt-4.1-nano` | OpenAI | ×2 | 低 | GPT 审计 Phase 1 盲区 |
| 2 Depth | `deepseek-v4-flash` | OhMyGPT | ×3 | 中 | 快速推理深入分析 |
| 3 Synthesis | `deepseek-v4-pro` | Zenmux.ai | ×2 | 高 | 旗舰质量出报告 |
| 3b CrossCheck | `glm-4.7-flash` | OhMyGPT | ×1 | 中 | GLM 交叉验证 |
| 4 Verify | `deepseek-v4-flash` | OhMyGPT | ×2 | 中 | 快速回归验证 |

证据源：`swarm-orchestrate.py` lines 13-18, 224-228, 25-228 (PHASE_CONFIG)。

**Provider 推测依据**：
- `deepseek-chat` 仅存在于 OhMyGPT 价格目录，ZenMux 不提供此模型
- `deepseek-v4-pro` → 主 Agent 使用 Zenmux.ai，推测编排器同样走 Zenmux
- `deepseek-v4-flash` → OhMyGPT 价格（CNY 1/2）远低于 ZenMux（$0.14/0.28），经济性指向 OhMyGPT
- `glm-4.7-flash` → 仅在 OhMyGPT custom_providers 中定义，MoA 也使用此路径
- `gpt-4.1-nano` → OpenAI 原生模型

**注意**：编排器通过 `hermes config set delegation.model <model>` 动态切换 delegation.model，但不切换 delegation.provider。这意味着当编排器设置 `delegation.model = deepseek-chat` 时，provider 仍为 `custom:zenmux.ai`，可能导致调用失败。此行为需确认。

---

## L3: MoA（Mixture of Agents，已验证）

`~/.hermes/config.yaml` lines 432-445，**非蜂群主路径**：

| 角色 | 模型 | Provider |
|------|------|----------|
| 参考模型 1 | `deepseek-v4-flash` | `custom:ohmygpt` |
| 参考模型 2 | `glm-4.7-flash` | `custom:ohmygpt` |
| 参考模型 3 | `gpt-5-nano` | `custom:ohmygpt` |
| **聚合器** | `z-ai/glm-5.2` | `custom:zenmux.ai` |

---

## Custom Providers 完整列表（config.yaml lines 666-679）

| Name | Base URL | Model | 备注 |
|------|----------|-------|------|
| `ohmygpt-deepseek-v4-pro` | `https://api.ohmygpt.com/v1` | `deepseek-reasoner` | API mode: anthropic_messages |
| `Zenmux.ai` | `https://zenmux.ai/api/v1` | `deepseek/deepseek-v4-pro` | 主 provider |
| `ohmygpt` | `https://api.ohmygpt.com/v1` | — | MoA 参考模型用 |

---

## 成本背景（已验证）

| Provider | 模型 | 输入/1M | 输出/1M | 币种 |
|----------|------|:---:|:---:|:---:|
| ZenMux | DeepSeek V4 Pro | $0.435 | $0.87 | USD |
| ZenMux | DeepSeek V4 Flash | $0.14 | $0.28 | USD |
| OhMyGPT | DeepSeek V4 Pro | ¥3 | ¥6 | CNY |
| OhMyGPT | DeepSeek V4 Flash | ¥1 | ¥2 | CNY |
| OhMyGPT | DeepSeek Chat | ¥1 | ¥2 | CNY |
| OhMyGPT | DeepSeek Reasoner | ¥1 | ¥2 | CNY |

证据源：`finance/README.md` lines 35-45，`automation/import_company_data.py` lines 333-385。

---

## 分析师报告纠正

| 分析师声明 | 验证结果 | 说明 |
|-----------|:---:|------|
| delegate_task 用 OhMyGPT | ❌ 不准确 | config.yaml 显示 `custom:zenmux.ai` |
| delegate 87% 失败率 | ⚠️ 未验证 | 仅来自 Memory（2026-07-09），无文件证据 |
| "deepseek-v4-flash @ OhMyGPT 用于 delegation" | ❌ 过时 | 已迁移至 Zenmux.ai |

---

## 不确定性

1. **swarm-orchestrate.py 的 Provider 不匹配**：编排器切换 delegation.model 但不切换 provider，设置 `deepseek-chat`（OhMyGPT only）时可能失败。需实测确认。
2. **OhMyGPT 87% 失败率**：无文件证据（日志/截图/统计数据），仅 Memory 中有记录。建议如需引用此数据，从 `~/.hermes/logs/agent.log` 提取实际失败统计。
3. **grok-4.20-reasoning 的 provider**：config.yaml 仅设置了 `x_search.model`，未设置 `x_search.provider`，实际路由机制不明。
4. **swarm-orchestrate 中的 provider 路由**：脚本注释写 "OhMyGPT" 但未在代码中显式指定 provider，依赖 Hermes 隐式路由。

---

## 总结

蜂群当前使用的核心模型为：

- **Controller**：`deepseek/deepseek-v4-pro` @ Zenmux.ai（$0.435/$0.87 per 1M tokens）
- **Delegate 子Agent**：`deepseek-v4-flash` @ Zenmux.ai（$0.14/$0.28 per 1M tokens）
- **X Search**：`grok-4.20-reasoning`
- **编排器**：动态切换 4 个模型（deepseek-chat, gpt-4.1-nano, deepseek-v4-flash, deepseek-v4-pro, glm-4.7-flash），跨越 3 个 provider
- **月成本估算**：$10-20（Memory 记录，需财务数据验证）

---

*报告生成时间：2026-07-16 15:30 UTC*
*证据状态：config.yaml 已验证，swarm-orchestrate.py 已验证，财务数据已验证，Memory 声明未文件验证*
