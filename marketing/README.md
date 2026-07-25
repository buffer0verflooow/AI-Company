---
tags: [department, marketing]
created: 2026-07-04
updated: 2026-07-21
---

# 📢 市场部

## 当前状态

微信公众号内容分发和外部市场雷达已稳定运行。2026-07-15 已导入公众号后台统计：10 篇有逐篇明细，趋势总表识别到 2026 年 7 月发布内容 17 篇。市场雷达每天 08:30 从公开网络、社区、公众号和需求代理信号中生成经多来源佐证的市场脉冲；截至 2026-07-20 已完成 8 轮真实采集、累计 170 条信号。

**关键集成进展（2026-07-21）**：市场雷达数据已通过 [[../marketing/market-to-content-bridge|市场雷达→内容项目桥接文档]] 关联到 ai-edu-series 和 article-curation 的内容排期。最高分脉冲"企业 AI 智能体安全治理需求"(81.23, 7 来源)已被映射到 ai-edu-series #15/#16/#19 的提前排期建议。当前仍无推广预算，收入只按实际凭证确认。

## 已有资产

- [[projects/ai-edu-series/strategy/content-strategy|AI工程系列 · 内容策略]]
- [[projects/ai-edu-series/strategy/distribution-plan|AI工程系列 · 分发计划]]（6 平台：公众号/知乎/B站/CSDN/掘金/小红书）
- [[projects/article-curation/strategy/content-strategy|文章精选 · 内容策略]]
- [[article-performance-2026-07-15|文章发布表现（2026-07-15）]]
- `article_performance.db` — 逐篇表现、渠道阅读和原始趋势数据
- `market_signals.db` — 外部市场信号、去重记录、评分和多来源市场脉冲
- `runtime/market-radar/` — 每次雷达运行的原始响应、报告和结构化脉冲
- `market-to-content-bridge.md` — 市场雷达 → 内容项目桥接（选题映射、排期建议）

## 关联项目

- [[projects/ai-edu-series/TRACKING|AI工程从零开始]]
- [[projects/article-curation/TRACKING|文章精选阅读]]

## 近期事项

- [x] 开始公众号发布并导入首批真实表现数据
- [ ] 将阅读、关注、分享和收藏数据接入每日 TVCR
- [x] 建立公开市场信号雷达并接入公司自治机会队列
- [x] 建立同主题 168 小时冷却和评分显著变化重开规则，避免重复追逐噪声
- [x] 市场雷达数据桥接到内容项目排期决策（桥接文档 v1）
- [ ] 建立各平台账号（知乎/B站/CSDN/掘金/小红书）
- [ ] 制定盈利模型（目前零收入，纯投入）
