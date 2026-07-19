# QA 报告 — 04-workflow-patterns

**文章**：Agent 工作流模式——Chain、Router、Parallel，你的 Agent 该跑哪种？
**质检时间**：2026-07-08
**目标渠道**：微信公众号（手动粘贴）

---

## Gate 1：事实核查 ✅

### 外部链接验证

| URL | 状态 | 备注 |
|-----|------|------|
| https://github.com/rohitg00/ai-engineering-from-scratch | 200 ✅ | 源项目主页 |
| https://github.com/rohitg00/ai-engineering-from-scratch/tree/main/phases/14-agent-engineering | 200 ✅ | Phase 14 子目录 |
| https://www.anthropic.com/engineering/building-effective-agents | 200 ✅ | Anthropic 原文 |
| https://platform.openai.com/docs/guides/prompt-engineering | 403 ⚠️ | Cloudflare 拦截，浏览器可正常访问 |

### 数据声明核查

- 无性能数据/模型对比/星级评分 ✅
- Anthropic "Building Effective Agents" 引用准确 ✅
- 无 CVE/GitHub PR 等技术声明需核实 ✅

---

## Gate 2：内容审校 ✅

### 链接去重
- 无重复链接 ✅
- 无自引用链接 [url](url) ✅
- 元信息与正文无 URL 重叠 ✅

### 禁用词检查
- 无「今日课程」「本篇教学目标」「学完你会」等上课腔 ✅
- 无「我的思考」「我的看法」等个人评论标记 ✅
- 无「回复获取」等虚假 CTA ✅

### 交叉引用
- 上一篇 → 「Phase 14 · 函数调用——Agent 拿到地图后怎么开车」✅ 匹配 03 篇实际标题
- 下一篇 → 「Phase 14 · Agent Memory——让 Agent 记住今天做了什么」✅ 系列规划一致

### 风格
- 「这篇文章聊什么」替代「教学目标」✅
- 「回顾一下」替代「本章总结」✅
- 文末只有点赞在看分享 CTA ✅
- 代码块使用围栏格式 ✅

---

## Gate 3：终审 ✅

- 选题契合项目定位（AI Edu Series — Agent Engineering）✅
- 源文章可访问（Anthropic 原文 200）✅
- 无敏感/争议内容 ✅
- 封面图存在且合理 ✅

---

## HTML 校验

```
✅ 完全合规，可直接粘贴到公众号编辑器
span leaf 包裹: 273 处
```

---

## 结论

**三关全部通过 ✅，可以推送。**
