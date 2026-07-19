# QA 报告 — #01 Agent 的灵魂只有 120 行代码

> 审核日期: 2026-07-05
> 审核员: Hermes Agent (Gate 1+2+3)

---

## Gate 1: 事实核查

### 链接可达性

| 链接 | 状态 | 备注 |
|------|:---:|------|
| github.com/rohitg00/ai-engineering-from-scratch | ✅ 200 | — |
| github.com/.../phases/14-agent-engineering | ✅ 200 | — |
| arxiv.org/abs/2210.03629 (ReAct) | ✅ 200 | 作者 Yao et al. 2022 ✓ |
| arxiv.org/abs/2302.04761 (Toolformer) | ✅ 200 | 作者 Schick et al. 2023 ✓ |
| arxiv.org/abs/2303.11366 (Reflexion) | ✅ 200 | 作者 Shinn et al. 2023 ✓ |
| gorilla.cs.berkeley.edu/leaderboard.html | ✅ 200 | — |
| anthropic.com/research/building-effective-agents | ❌ 307→/engineering/... | **URL 已变更，需修正** |
| platform.openai.com/docs/guides/function-calling | ⚠️ 403 | Cloudflare 拦截 curl，URL 本身可能正确 |
| docs.anthropic.com/.../tool-use/overview | ⚠️ geo-block | 重定向到 claude.com/app-unavailable-in-region |

### 无法独立验证的声明（建议标注出处）

| 声明 | 位置 | 建议 |
|------|------|------|
| ReWOO token 消耗是 ReAct 的 1/5，HotpotQA 准确率 +4% | L140 | 标注"据 ReWOO 论文实验数据" |
| Tree of Thoughts: Game of 24 从 4% → 74% | L172 | 标注"据 ToT 论文实验数据" |
| LATS: HumanEval pass@1 达 92.7% | L174 | 标注来源 |
| Reflexion: HumanEval 达到当时 SOTA | L157 | 标注来源和具体数字 |
| DeepSeek-V3 每次推理只激活 37B | L141 | 标注"据 DeepSeek 官方技术报告" |
| BFCL V4 五类场景占比 (40/30/10/10/10) | L106 | 需对照 BFCL 官网核实 |

### 已验证的事实

- ✅ ReAct 论文: Shunyu Yao 等人 2022 年发表，标题正确
- ✅ Toolformer: Timo Schick 等人 2023 年发表
- ✅ Reflexion: Noah Shinn 等人 2023 年发表
- ✅ Anthropic "Building Effective Agents" 发布于 2024 年底
- ✅ BFCL V4 由伯克利维护，URL 正确

---

## Gate 2: 内容审校

### 🔴 严重错误

**1. 「下一篇」预告内容错误 (L398)**

> 当前: `📋 下一篇：Phase 14 · Agent Memory 深度解析（下周发布）`

实际下一篇是 **「工具定义与 Schema——让 Agent 学会使用工具」**（已写完）。Agent Memory 不在当前发布计划中。
**必须修正**，否则读者会期待错误的内容。

**2. 「上一篇」信息错误 (L397)**

> 当前: `📋 上一篇：Phase 13 — AI 与世界接轨：Tools & Protocols（即将发布）`

这是系列第一篇，Phase 13 尚未撰写。"（即将发布）"会误导读者以为内容已存在。
**建议改为**: `📋 第一篇 / 20 · Agent Engineering 入门篇`

### 🟡 需要修正

**3. 标题数字不一致**
- 标题: "120 行代码"
- 引言 (L8): "不到 200 行的循环"
- 实际代码: ~160 行 (L199-L296)

统一为一个数字，建议用"不到 200 行"或精确值。

**4. 违反风格指南: "📦 读完你会得到" (L22)**

违反了 wechat-publisher skill 中的禁止列表:
> ❌ "读完本文你将获得 / 学完你会"
> ✅ "这篇文章会带你搞清楚"

L39 已经用了正确的"这篇文章会带你搞清楚"——L22 的 meta block 也应统一。
**建议改为**: `📦 内容地图` 或 `🎯 这篇文章里你会搞懂`

**5. Anthropic 文章 URL 变更**
L388: `/research/building-effective-agents` → 应改为 `/engineering/building-effective-agents`

### 🟢 良好实践

- 整体结构遵循项目模板 ✓
- "我的思考/编者解读"与原文明确区分 ✓
- 中文比喻（"大脑和手和眼睛"）恰当 ✓
- 国产模型对照（DeepSeek-V3 / Qwen）充分 ✓
- 代码示例完整可理解 ✓

---

## Gate 3: 主编终审

| 检查项 | 状态 | 备注 |
|--------|:---:|------|
| 选题与 ai-edu-series 定位一致 | ✅ | Phase 14 Agent Engineering 入门 |
| 源项目仍可访问 | ✅ | GitHub 200 OK |
| 无敏感/争议内容 | ✅ | — |
| 封面图存在 | ✅ | cover-phase-14-agent-intro.png + thumb |
| 文末含源项目链接 | ✅ | — |
| MIT License 声明 | ✅ | L12, L395 |

---

## 修正清单（发布前必须完成）

- [ ] 🔴 L398: 下一篇预告改为「工具定义与 Schema」
- [ ] 🔴 L397: 上一篇信息修正
- [ ] 🟡 L388: Anthropic URL 改为 /engineering/building-effective-agents
- [ ] 🟡 L22: "读完你会得到" 改为 "内容地图"
- [ ] 🟡 标题/引言数字统一（120 vs 200）
- [ ] ⚠️ 无法验证的性能数据添加出处标注
