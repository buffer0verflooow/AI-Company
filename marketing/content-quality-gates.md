---
tags: [department, marketing, content-pipeline]
created: 2026-09-21
---

# 内容质量门(去 AI 味 / QA Gate 1-4 / 微信预览与排版)

> **单一来源声明(CV2,2026-09-21)**:本文件是"文字侧质量门"的**共享规范正文**,
> 供两条产线读取 —— ① hermes 隔离执行器(`automation/content_hermes_executor.py`
> 的 article 提示词,按此文本执行);② 蜂群 v2 内容线(`company_router.build_runtime_brief`
> 内联节选 + `_spec/` 全量落盘)。改规则**只改这里**。
>
> 正文来源:由既有 article 提示词第 2/4/5/6 步逐字整理(不新增政策)。

## 1. 去 AI 味(humanizer;34 条模式逐条检查)

对象 = `draft.md`;产出 = `draft-humanized.md`。

- 彻底去掉 AI 词汇("值得注意的是"、"此外"、"总而言之"、"至关重要"、"在当今时代"等)
- 去掉教科书结构("挑战与展望"、"综上所述"等章节模板)
- 去掉 emoji 标题(🚀💡✅ 等)
- 变节奏:短句和长句交替,不要每段一样长
- 加人味:有观点、有态度、有第一人称("我"),不要中立播报腔
- 标题风格参考:用具体数字+反转/反差,不要教科书式标题
- 去掉所有 AI 填充段落("随着…的发展"、"为…奠定了基础"等开头)

**文章结构禁令(对标 #07 已验证风格,必须遵守)**:

- 禁止元数据框:不许出现 "📌这篇文章聊什么"、"⏱️预计阅读"、"🛠️运行环境"、"📖本系列基于"
- 禁止编号章节:不许用 "01｜" "02｜" 等数字前缀
- 禁止 "## 结语"、"## 🔥"、"## 总结"、"📚系列下一篇"
- 禁止教学序词:"下面让我们..."、"首先..."、"接下来..."、"值得注意的是"
- 标题必须有钩子+具体事实,不能是教科书式陈述

## 2. QA 三 Gate(产出 `qa-report.md`)

Gate 1 事实核查、Gate 2 内容审校、Gate 3 主编终审,逐项给证据和结论。
**QA 对象是 `draft-humanized.md`,不是 `draft.md`**。

## 3. 微信预览检查(Gate 4;产出 `wechat-preview.html` + 写入 qa-report 的对应章节)

生成 `draft-formatted.md` 后,转为 HTML 预览文件 `wechat-preview.html`,然后逐项检查:

- CSS 样式整体注入到 `<body>` 元素的 inline `style=` 属性中
- CSS 属性值中没有未转义的双引号(`font-family: "xxx"` → 会截断 HTML style 属性)
- 代码块使用 `<pre>` 标签且 `white-space: pre` 或 `white-space: pre-wrap`
- 所有图片有正确的 `src` 和 `alt` 属性
- 无破损的嵌套标签/未闭合标签

以上检查结果写入 `qa-report.md` 的「Gate 4 微信预览检查」章节,附 HTML 片段证据。

## 4. 排版(任务含「公众号/排版/微信」时;产出 `draft-formatted.md`)

- 必须在正文顶部内联 `<style>` 代码块;CSS 模板见
  `projects/wechat-publisher/assets/wechat-article.css`(随任务落盘为 `_spec/wechat-article.css`)
- 代码框用 ```python 围栏(不能用缩进),`white-space: pre`
- 公式单独占行,前后留空行,不混在正文
- `<style>` 写在 YAML frontmatter 之后、正文之前
- 不使用 emoji 标题、不使用 `word-break: break-all`

## 5. 边界

- 不执行公众号推送、草稿箱写入或公开发布(外部动作必须人工审批)
- 不编造链接、数据、测试或已完成动作;无法核验的内容明确标注
- 只在本任务产物目录内写文件
