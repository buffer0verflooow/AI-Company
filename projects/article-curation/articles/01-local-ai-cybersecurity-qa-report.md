# 质检报告 — 文章 #01: 本地AI做渗透测试

> 原文: Local AI for Penetration Testing & Research (projectblack.io)
> 质检日期: 2026-07-05
> 质检模型: deepseek-v4-pro（主代理）
> 撰写模型: deepseek-v4-pro（子代理产出被质检驳回后主代理重写）

---

## Gate 1: 事实核查

### CVE 编号

| CVE | NVD 状态 | 描述匹配 | 结果 |
|-----|----------|----------|:---:|
| CVE-2026-12194 | ✅ HTTP 200 | PHPIPAM 认证 LFI | ✅ 通过 |
| CVE-2026-12195 | ✅ HTTP 200 | myVesta 认证 RCE | ✅ 通过 |

### 链接可达性

| 链接 | 状态 | 备注 |
|------|:---:|------|
| https://projectblack.io/blog/local-ai-for-cyber-security/ | ⚠️ curl返回000 | 浏览器可访问，curl被拦截（CDN/WAF） |
| https://github.com/phpipam/phpipam/pull/4625 | ✅ 200 | |
| https://github.com/myvesta/vesta/commit/95d7e43... | ✅ 200 | |
| https://github.com/usestrix/strix | ✅ 200 | |

### 数字/统计交叉验证

| 声明 | 源文章匹配 | 结果 |
|------|:---:|:---:|
| 6000万 token (Strix) | ✅ "close to 60 million tokens" | ✅ |
| ~$30 USD (Strix) | ✅ "~$30 USD" | ✅ |
| 12 小时 | ✅ "~12 hours later" | ✅ |
| 800 个源文件 | ✅ "around 800 source code files" | ✅ |
| 1.2 亿 token (harness) | ✅ "roughly 120 million tokens" | ✅ |
| Qwen 3.6 27b | ✅ "Qwen 3.6 27b" | ✅ |
| 170k 上下文 | ✅ "~170k context" | ✅ |
| Strix 25000+ star | ✅ "over 25,000 GitHub stars" | ✅ |

### Gate 1 结论

✅ 通过。所有可验证的事实声明均与源文章一致。CVE 和 GitHub 链接均可达。

---

## Gate 2: 内容审校

- [x] 标题准确不标题党
- [x] 结构遵循 article-curation 模板
- [x] 「我的思考」与原文解读区分清晰
- [x] 无错别字
- [x] 中英文混排规范（CVE号、工具名正确使用英文）
- [x] 分享风格，无「上课」腔（无「教你」「学会」「掌握」）
- [x] 无假 CTA（无「回复获取」）
- [x] 无「下期预告」

### Gate 2 结论

✅ 通过。

---

## Gate 3: 主编终审

- [x] 选题与项目定位一致（安全技术/工程实践）
- [x] 原文可访问（浏览器）
- [x] 无敏感内容
- [x] 对中国读者有实际参考价值：
  - 本地模型（Qwen 3.6）的实战验证对国内开发者有直接参考意义
  - 成本对比数据帮助决策
  - 「方法 > 模型」的结论对 AI 工程化有指导价值

### Gate 3 结论

✅ 通过。

---

## 驳回记录

| 日期 | 轮次 | 模型 | 驳回原因 |
|------|:---:|------|----------|
| 2026-07-05 | 1 | deepseek-v4-flash | 写错文章——写了 zsec.uk 的 "Autonomous Vulnerability Hunting with MCP"，而非用户指定的 projectblack.io 文章 |

两阶段工作流有效拦截了错误产出。

---

## 发布后修正历史

| 日期 | 修正内容 |
|------|----------|
| — | — |
