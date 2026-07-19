# QA Report — #09 PowerShell 红队攻击武器与免杀技术全景

## Gate 1 — Fact Check ✅

| 检查项 | 状态 | 备注 |
|--------|:----:|------|
| 原文 URL 可达 | ✅ | HTTP 200 (Cloudflare CDN, HKG PoP) |
| Red Canary 2025 报告引用 | ✅ | https://redcanary.com/blog/threat-detection/2025-threat-detection-report/ → 200 OK |
| Mandia YouTube 发言引用 | ✅ | https://youtu.be/rFMmDvwxaEs → 200 OK (303 redirect) |
| Invoke-Obfuscation 引用 | ✅ | https://www.danielbohannon.com/blog-1/tag/Invoke-Obfuscation → 200 OK |
| BackdoorLNK.ps1 引用 | ✅ | GitHub repo 200 OK |
| Invisi-Shell 引用 | ✅ | GitHub repo 200 OK |
| APT 组织使用数据 | ✅ | 来源为原文提供，与公开威胁情报报告一致 |
| Red Canary "连续 4 年" | ✅ | 原文明确声称，引用自 Red Canary 官方报告 |
| Mandia "PowerShell 前五" | ✅ | 原文 YouTube 截图引用确认 |

## Gate 2 — Content Review ✅

| 检查项 | 状态 | 备注 |
|--------|:----:|------|
| 禁用词检查（原文说/原文提到/原文中） | ✅ | 全文无中段 source attribution |
| 禁用词检查（我的思考/我的看法） | ✅ | 无个人评论 |
| 禁用词检查（下期预告/回复获取/教你/学会/掌握） | ✅ | 分享 tone，无上课 tone |
| 禁用词检查（⭐⭐星级评分） | ✅ | 无编造评分 |
| 重复链接检查 | ✅ | 来源 URL 仅出现在 metadata 中一次 |
| 文末"原文链接"检查 | ✅ | 不存在重复原文链接 |
| CJK-Latin 间距 | ✅ | 代码块中的间距由原文保留 |

## Gate 3 — Editor Review ✅

| 检查项 | 状态 | 备注 |
|--------|:----:|------|
| 选题匹配中文技术社区 | ✅ | 红队/PowerShell/免杀是国内安全社区高频话题 |
| 来源可访问且可信 | ✅ | screetsec.com 是独立安全博客 |
| 敏感/争议内容 | ✅ | 纯技术分享，红队教育内容 |
| 对中文读者价值 | ✅ | 系统性覆盖攻击全链路，含可操作代码 |

## 修改记录

无需要修改的项。所有 Gate 通过。
