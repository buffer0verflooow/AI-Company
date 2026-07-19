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
