---
tags: [finance, model-routing, token-cost, dsh, opencode]
created: 2026-08-22
updated: 2026-08-22
---

# Token 成本规划:通道分层与任务迁移

背景:Codex(anyrouter gpt-5.6-sol)模型暂不可用,且价格最高($2/$12 每 1M)。
决策:Codex 任务迁移到 dsh(DeepSeek 官方),简单任务走 OpenCode Zen 免费模型。

## 通道价格盘点($ / 1M tokens,输入/输出)

| 通道 | 模型 | 输入 | 输出 | 用途 |
|---|---:|---:|---|---|
| DeepSeek 官方 | v4-flash | ~0.14 | ~0.28 | Hermes 主/委派、dsh,最便宜智能通道 |
| DeepSeek 官方 | v4-pro | ~0.44 | ~0.87 | 复杂任务 |
| AnyRouter | gpt-5.6-sol | 2 | 12 | Codex 原主力(不可用) |
| AnyRouter | gpt-5-codex | 1.25 | 10 | 备选(暂停使用) |
| ZenMux | v4-flash | 0.14 | 0.28 | 中转备选 |
| OhMyGPT | 270 模型 | 50% off 促销 | — | key 已更新(2026-08-22);免费模型仅 Web 促销层,API 不可用 |
| OpenCode Zen | 6+ 免费模型 | 0 | 0 | 简单任务,每日限额最高 100 万 token/模型 |
| ZenMux | 4 免费模型(含视觉) | 0 | 0 | 简单任务 + 图像理解,端点 zenmux.ai/api/v1 |
| 图像 | flux 系列 | 按张 | — | 封面/信息图,不烧 token |

## 任务路由决策表

| 任务类型 | 通道 | 说明 |
|---|---|---|
| 自动化代码修复(daily auto-fix) | dsh headless | ✅ 已迁移,2026-08-22 |
| 代码编写/排查/项目开发 | dsh headless | Codex 不可用期间的替代 |
| 封面/信息图 | generate_cover.py / flux | 本地 PIL 零成本优先;flux 按张 |
| 文章初稿/摘要/翻译/简单脚本/批量文本 | OpenCode Zen 免费模型 | 待配置 key |
| 高复杂度推理(Hermes 主会话) | deepseek-v4-pro/flash | 保持 |

## 已执行

1. ✅ cron `company-daily-auto-fix`(6d68b56f8bf6):codex exec → `dsh --profile headless`,
   skills 移除 codex,保留 Hermes fallback。下次运行 2026-08-23 04:00。
2. ✅ dsh 部署验证:文件编辑/命令执行/git commit 均可用(2026-08-22 实测)。
3. ✅ 确认封面生成不消耗 token:content worker 走 image_gen(flux),本地有 generate_cover.py(PIL)。
4. ✅ 蜂群模型对照表 (swarm migration 020, 2026-08-22):
   - model_profiles 加 tier 列 + model_usage_daily 记账表
   - 免费池白名单角色: scanner/data-analyst/reporter/report-writer/content-writer/custom
     → opencode run 调免费模型 (zenmux glm-5.3-free / opencode nemotron-3-ultra-free)
   - 轮询: 当日用量均衡; 超限 (默认 100 万 token/300 calls/模型/日,
     SWARM_FREE_DAILY_TOKENS / SWARM_FREE_DAILY_CALLS 可覆盖) 自动降级付费
   - 非白名单角色 (analyst/exploiter 等) 永远付费通道
   - executor: swarm_hermes_executor.py 按 tier 分流 (opencode run / hermes chat)
   - 测试: tests/test_model_free_pool.py 13 例, 全量 245 passed

## 待办

- [x] OpenCode Zen:已配置(2026-08-22),key ~/.config/opencode/zen.key
- [x] ZenMux:已配置,key ~/.config/opencode/zenmux.key(复用 Hermes config.yaml)
- [ ] 简单任务清单接入:文章初稿/摘要/翻译/简单脚本/批量文本 → opencode run(免费)
- [ ] 观测两周:对比 daily-auto-fix 迁移前后质量与成本,必要时调整模型分层
- [ ] 封面通道定案:Codex 图像能力不可用期间,默认 generate_cover.py(PIL)或 flux

## 免费通道实测清单(2026-08-22)

OpenCode Zen(6/7 可用,key: ~/.config/opencode/zen.key):
  ✅ opencode/nemotron-3-ultra-free   ✅ opencode/nemotron-3.5-lightning-free
  ✅ opencode/hy3-free                ✅ opencode/mimo-v2.5-free
  ✅ opencode/x-preview-f-free(Ox Alpha) ✅ opencode/big-pickle(stealth)
  ❌ opencode/muse-spark-1.2-contributor-free(地域限制,国内不可用)

ZenMux(3/9 可用,key: ~/.config/opencode/zenmux.key):
  ✅ zenmux/z-ai/glm-5.3-free                        ✅ 强推理
  ✅ zenmux/deepseek/deepseek-v4-flash-vision-exp-free  ✅ 视觉
  ✅ zenmux/z-ai/glm-4.6v-flash-free                 ✅ 视觉快速
  ❌ 其余 5 个 free 模型 404/无效,勿路由

OhMyGPT(key: ~/.config/opencode/ohmygpt.key,已配 opencode):
  ❌ 免费模型:官网称 GLM-4.7/GLM-4.6V 有 free 变体、ohmygpt-internal-c35haiku-free
     每日免费次数,但 API 实测均 400 不可用(仅 Web 促销层)
  ✅ 付费可用(全站 50% off):ohmygpt/glm-4.7、deepseek-chat、deepseek-reasoner 等
     — 作为 dsh 之外的付费备选通道(打折后仍比 anyrouter 便宜)

用法:
  opencode run --model opencode/nemotron-3-ultra-free "任务"
  opencode run --model zenmux/z-ai/glm-5.3-free "任务"
  opencode run --model zenmux/deepseek/deepseek-v4-flash-vision-exp-free "看图"

## 风险

- dsh 为 developer preview,兼容性会变,auto-fix 有 Hermes fallback 兜底。
- Zen 免费模型限时且每日限额,只用于简单任务,不做质量关键路径。
- 迁移后若 auto-fix 质量下降(DeepSeek 修复能力弱于 gpt-5.6-sol),评估恢复
  gpt-5-codex($1.25/$10,成本仍低于原 gpt-5.6-sol)。
