---
tags: [project, article-curation, reading-share]
created: 2026-07-05
---

# Article Curation — 阅读分享项目

> 精选海外优质技术文章，撰写中文阅读分享，发布多平台。

## 项目目标

将海外前沿技术内容翻译/解读为中文阅读分享，面向中国 AI 工程师和安全从业者。

## 工作流

1. **子代理撰写**（deepseek-v4-flash）：读取源文章 → 撰写中文解读
2. **主代理质检**（deepseek-v4-pro）：事实核查、风格审查、终审
3. **信息图**：如有需要，委托 codex（gpt-5.5）生成
4. **发布**：推送到微信公众号草稿箱

## 文章列表

| # | 标题 | 状态 | 源URL |
|---|------|:---:|-------|
| 01 | 待定 | 🔄 写作中 | projectblack.io |

## 目录结构

```
article-curation/
├── README.md
├── TRACKING.md
├── articles/       ← 产出的文章
├── assets/         ← 封面图、信息图
└── strategy/       ← 策略文档
```
