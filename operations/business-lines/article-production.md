---
tags: [operations, business-line, article]
created: 2026-07-05
---

# 📝 文章创作分享产线

> 将技术内容转化为中文文章，通过多平台分发触达读者。

## 工作流

```
选题 → 子代理撰写 → humanizer 去 AI 味 → 主代理质检 → 封面生成 → 推送草稿箱 → 人工审核发布
```

| 阶段 | 工具/代理 | 产出 |
|------|------|------|
| 选题 | 源文章 URL / 内容策略 | 选题确认 |
| 撰写 | `agency_agents_delegate` → technical-writer | draft.md |
| **去 AI 味** | **humanizer 技能（34 条模式检查）** | **draft-humanized.md** |
| 质检 | Gate 1（事实核查）+ Gate 2（内容审校）+ Gate 3（主编终审） | QA 报告 |
| 封面 | Python PIL | 900×383 cover + 200×200 thumb |
| 排版 | [[../../projects/gzh-design-skill/README\|gzh-design-skill]] → 摸鱼绿主题 | GZH 合规 HTML |
| 预览 | `wrap_preview.py` → 浏览器打开 | 带「复制」按钮的预览页 |
| 推送 | 预览页点「复制」→ 公众号编辑器 Ctrl+V 粘贴 | 草稿箱 |
| 审核 | 人工 | 发布 |

### 去 AI 味（Humanize）阶段

草稿完成后，必须运行 `humanizer` skill（`/skill humanizer`），执行三步流程：

1. **识别 AI 模式**：扫描 34 种 AI 写作特征（填充词、AI 词汇、教科书结构、标语腔、表情符号、过度的强调/宣传/设问等）
2. **重写问题段落**：保持核心信息不变，换用人话表达
3. **大声朗读检查**：读出声来判断是否自然，最后输出「什么让这篇文章看起来像 AI 生成的」自查清单

### 排版（Format）阶段

humanizer 通过后，文章须适配微信公众号格式：

- 使用 `wenyan` CLI 格式化（`-t lapis` 蓝色技术主题，`-h solarized-light` 代码高亮）
- 去掉 emoji 标题、标题大写、填充段落、无意义的 boldface
- 代码块使用 wenyan 语法，确保移动端可读
- 段落节奏有变化：短句穿插长句，避免均匀段落

## 素材包规范（2026-08-12 模板化）

每篇文章的素材包（`marketing/evidence/<slug>-<date>/source.md`）按统一三区块结构组织，模板见 `marketing/evidence/TEMPLATE.md`，样板见 `c11-h1-identity-verification-2026-08-11/source.md`：

1. **事实层** — 核心事实每条带出处（域名/平台 + 日期）；多源交叉验证的标注验证过的源；原创观察类另列"反方/社区素材"（支持派 vs 警觉派）
2. **约束层** — 角度 / 公司独有角度 / 结构建议 / 标题方向 / 风格 / 字数 / 禁词 / 时效
3. **边界层** — 不许虚构 / 必须验证 / 演示 vs 生产标注 / 红线

原则：素材包是"工程化上下文"不是"资料夹"——上下文要可检索、可解释、可控。采集新素材时按模板生成；旧素材包（无三区块头的）按需补头，原文保留在 `# 素材原文(采集转储)` 之后。

## 文档解析能力

文章产线可使用 [[../../projects/mineru/README|MinerU]] 将论文、技术报告和幻灯片转换为结构化 Markdown，再进入现有的编译与 QA 流程。解析输出仍属于中间产物，发布前必须经过事实核验、版权和披露审核。

## 关联代理

### 产线工人（按工位）

| 工位 | 代理 | Division | 用途 |
|------|------|------|------|
| 📝 技术撰稿 | `technical-writer` | engineering | 技术文章撰写、代码注释 |
| ✍️ 内容策划 | `content-creator` | marketing | 选题策略、叙事结构 |
| 🤖 AI 方向 | `ai-engineer` | engineering | AI/ML 技术文章 |
| 🔐 安全方向 | `application-security-engineer` | security | 安全研究文章 |
| 🏗️ 安全架构 | `security-architect` | security | 防御体系分析 |
| 🎯 攻击视角 | `penetration-tester` | security | 红队/攻防文章 |
| 📡 威胁情报 | `threat-intelligence-analyst` | security | 威胁态势分析 |
| 🔍 检测工程 | `threat-detection-engineer` | security | 检测规则/EDR 分析 |
| 🎨 视觉设计 | `visual-storyteller` | design | 信息图、知识卡片 |
| 📋 合规审查 | `legal-compliance-checker` | support | 合规性终审 |
| 📱 公众号 | `wechat-official-account-manager` | marketing | 公众号排版/运营策略 |
| 💡 知乎 | `zhihu-strategist` | marketing | 知乎问答/专栏策略 |
| 📺 B站 | `bilibili-content-strategist` | marketing | B站专栏同步策略 |
| 🔎 趋势研究 | `trend-researcher` | product | 选题热点追踪 |

### 实际使用情况

| 项目 | 撰写代理 | 质检方式 |
|------|------|------|
| ai-edu-series (#01-#03) | `technical-writer` | Hermes 主代理 Gate 1-3 |
| article-curation (#01-#04) | 子代理（deepseek-v4-flash） | Hermes 主代理 Gate 1-3 |
| article-curation (#05-#08) | 子代理 + QA 报告 | `application-security-engineer` 等安全代理 |

| 项目 | 类型 | 进度 |
|------|------|:---:|
| [[../../projects/ai-edu-series/TRACKING\|AI工程从零开始]] | 原创教育 | 5/20 |
| [[../../projects/article-curation/TRACKING\|文章精选阅读]] | 海外编译 | 14 篇 |

## 分发平台

- 微信公众号（首发）
- 知乎专栏
- CSDN / 掘金
- B站（文字版同步）
- 小红书（知识卡片）

## 质量门

每篇文章发布前须通过三道 Gate：
1. **事实核查** — 链接可达性、数据交叉验证
2. **内容审校** — 禁用词检查、风格一致性、术语准确性
3. **主编终审** — 选题定位、敏感内容、合规性

## 草稿箱管理与发布注意事项（2026-08-10 事故教训）

草稿箱是共享外部资源，操作时遵守以下规则，防止误判与竞态：

1. **草稿箱数量变化 ≠ 草稿丢失**：文章发布后会自动从草稿箱移除。看到
   草稿箱条目减少，先查「已发表」列表确认（`draft/batchget` vs
   `freepublish/get` / 公众号后台），**不要臆断为丢失**。
   - 事故案例：2026-08-10 用户发布 SakDriver/LLM Heist 后草稿箱 9→7 条，
     主代理误判为"worker 误删"，重新推送了两篇已发布文章，产生重复草稿，
     需要用户提醒后手动删除。
2. **发布是人工动作**：只有用户能发布。产线推送的是草稿箱（draft/add），
   发布后不要重新推送同标题草稿（重复草稿污染草稿箱）。
3. **主代理与 Router 分发的 worker 不得并行操作草稿箱**：Router 把任务
   分发给 company worker 时，主代理不应同时执行同一外部动作（竞态风险）。
   草稿箱增删属于外部动作，只能由一条执行路径操作。
4. **推送前先查重**：wechat_push.py 已有同标题删除逻辑，但主代理手动
   推送前仍应先 `draft/batchget` 确认目标文章当前状态（已发表？草稿箱已有？
   已删除？），再决定推送/更新/跳过。
5. **author 字段**：公众号名称为 nooooop（不是 pwn），wechat_push.py
   硬编码已修正；历史草稿 author=pwn 的重新推送时自动变为 nooooop。
6. **推送后用 API 验证**：推送后立即 `draft/batchget` 核对 media_id、
   update_time、author，确认推送对象正确。

## 经营计量

质量门通过只表示文章具备交付条件，不代表产生了业务价值。每个文章 Run 完成后自动记录模型、Token、工具调用、耗时和产物；采用、发布、触达和人工返工需在真实结果出现后补录到经营账本。

文章产线的 TVCR 评估至少同时检查：

- Value：是否被采用、发布、触达目标读者或形成可复用内容资产；
- Cost：Token、已确认模型费用、人工时间和机会成本；
- Time：从 Brief 到交付、返工和等待审批的周期；
- Risk：事实错误、合规、品牌和低质量规模化风险。

高 Token 只能触发调查。优化顺序为选题价值、文章分级、Brief 质量、流程与资源配置，最后才是 Prompt、配置或代码。

详见 [[../tvcr-governance|TVCR 经营治理闭环]]。

## 已接入业务结果

- 2026-07-15 已导入微信公众号后台真实统计：10 篇逐篇明细、96 条文章渠道阅读记录。
- 结构化数据：`marketing/article_performance.db`。
- 汇总：[[../../marketing/article-performance-2026-07-15|文章发布表现（2026-07-15）]]。
- article-curation 当前状态：11 篇已发布、2 篇由用户确认不发布、1 篇待发布。
- 2026-08-09 产线体检：[[../../operations/article-pipeline-review-2026-08-09|文章产线整理报告]]（Router 误分发根因修复 + 20 个误分发 job 识别）。
