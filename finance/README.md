---
tags: [department, finance]
created: 2026-07-04
updated: 2026-07-15
---

# 💰 财务部

## 当前状态

部门已建立证据化 SQLite 台账：`finance_ledger.db`。当前没有任何带付款/收款凭证的实际交易，因此**实际赏金收入为 $0**；HackerOne 报告中的 `$10,850–$30,150` 仅作为 forecast 展示，不计入收入或利润。

## 台账规则

- 实际收入/支出必须附本机证据文件，并保存 SHA-256。
- `expected_payout`、报告估值和项目奖金区间只能进入 forecast。
- 不同币种不自动合并净利润。
- Hermes 只有 `cost_status` 为 actual/confirmed/provider_reported/billed 时才计入实际模型成本；未知价格保持 unpriced。

首次同步：

| 指标 | 当前值 |
|------|------:|
| 实际收入 | $0 |
| 实际支出 | $0 |
| 赏金 forecast | $10,850–$30,150 |
| 已确认模型成本 | $0 |
| 未定价 Hermes 会话 | 61（价格目录已建立，但尚未回写历史会话） |
| 已完成 Router Run | Research 2、文章 1、视频 0 |

## 模型价格目录

已在 `finance_ledger.db` 的 `model_prices` 表中写入 25 条公开报价，采集时间为 2026-07-15。报价只用于经营成本估算，不等同于已付款账单。

| Provider | 模型 | 输入 / 1M | 输出 / 1M | 缓存读取 / 1M | 币种 |
|---|---|---:|---:|---:|---|
| ZenMux | DeepSeek V4 Pro | 0.435 | 0.87 | 0.003625 | USD |
| ZenMux | DeepSeek V4 Flash | 0.14 | 0.28 | 0.0028 | USD |
| ZenMux | DeepSeek V3.2 | 0.293 | 0.4395 | 0.0293 | USD |
| ZenMux | DeepSeek V3.1 | 0.28 | 1.11 | 0.056 | USD |
| ZenMux | DeepSeek R1 0528 | 0.56 | 2.23 | 0.112 | USD |
| OhMyGPT | DeepSeek V4 Pro | 3 | 6 | 未公开 | CNY |
| OhMyGPT | DeepSeek V4 Flash | 1 | 2 | 未公开 | CNY |
| OhMyGPT | DeepSeek Chat | 1 | 2 | 未公开 | CNY |
| OhMyGPT | DeepSeek Reasoner | 1 | 2 | 未公开 | CNY |

AnyRouter 已从用户保存的定价页中提取 16 条 USD 报价，完整清单见 [[anyrouter-pricing-2026-07-15|AnyRouter 模型价格摘要]]。其输入价格为 $1–$15 / 1M tokens，输出价格为 $5–$75 / 1M tokens；页面未公开缓存价格。

来源证据保存在 `finance/sources/`，数据库记录来源 URL、SHA-256、币种和采集日期。

```bash
cd /home/pwn/workspace/company
python3 automation/finance_ledger.py --sync
python3 automation/finance_ledger.py --report
```

## 成本结构

| 类型 | 项目 | 估计 |
|------|------|:---:|
| 算力 | LLM API 调用（写作/质检） | 低 |
| 工具 | 域名、服务器 | 暂无 |
| 认证 | 微信公众号认证 | 300元/年（待办） |

## 关联分析

- [[../strategy/okx-ai-profitability-analysis|OKX AI 盈利模式分析]]
- [[../strategy/upwork-profitability-analysis|Upwork 盈利能力分析]]

## 近期事项

- [x] 建立基础收支、预测与模型用量分账
- [ ] 为实际 HackerOne accepted/paid 记录补录平台凭证
- [x] 建立 ZenMux / OhMyGPT 可核验价格目录
- [ ] 将公司价格目录接入 Hermes 历史会话重算；在接入前不修改原 `unknown/unpriced` 状态
- [ ] 完成微信公众号认证（数据分析 API 前置条件）
- [ ] 制定 2026 下半年预算
