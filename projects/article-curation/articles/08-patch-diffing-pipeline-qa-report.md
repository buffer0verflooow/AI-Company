# QA Report — 08-patch-diffing-pipeline (重写版)

**日期**: 2026-07-08
**文章**: 08-patch-diffing-pipeline.md
**源文**: https://www.originhq.com/research/patch-diffing-pipeline

## Gate 1 — 事实核查 ✅ PASS

### URL 验证
| URL | 状态 |
|-----|------|
| https://www.originhq.com/research/patch-diffing-pipeline | 200 ✅ |
| https://github.com/originsec/patchwatch | 200 ✅ |
| https://github.com/originsec/pocsmith | 200 ✅ |
| https://github.com/originsec/hyperv-mcp | 200 ✅ |
| https://github.com/originsec/kd-mcp | 200 ✅ |

### 关键数据交叉验证
- CVE-2026-27914 (MMC EoP, CVSS 7.8, KB5083768) ✅
- CVE-2026-41096 (ws2_32.dll RCE, CVSS 9.8, KB5089548) ✅
- ~$300 API 费用, Opus-4.7 ✅
- PatchWatch (Rust) + Pocsmith (Claude Agent SDK) ✅
- Ghidriff binary diff 引擎, Winbindex ✅
- 28,000 文件变更, 209 added/17 deleted/3,544 modified ✅
- _IsFileSourceUntrustworthy, 0x80030070, 0x3494 ✅
- StringLengthWorkerW, 0x104 wchar buffer ✅
- HPACK 0x48→0x50, STATUS_INTEGER_OVERFLOW (0xC0000095) ✅
- kd-mcp 22 tools, mmc.exe PID 8140 ✅
- MDASH 100+ agents ✅

### 重写 vs 旧版差异
- [x] 删除了"我的思考"整个章节（3 个子节）
- [x] 删除了"本文不是翻译，是读完研究后自己的梳理和讨论"
- [x] 新增 CVE-2026-41096 bonus 案例完整描述（旧版只有简略提及）
- [x] 新增 Token 费用独立章节
- [x] 新增工具箱表格（8 个组件，含作者）
- [x] 新增对防守方意义章节（NCSC/CSA/MDASH 引用）
- [x] 新增 POC 文件 manifest 概要
- [x] 标题从"一个研究员搭的"改为"$300 搭一条"

## Gate 2 — 内容审校 ✅ PASS

### 禁用词/模式: 0 处 ✅
- 我的思考/我的看法/我认为/我觉得: 0 ✅
- 原文说/原文提到/原文中: 0 ✅
- 本文不是翻译: 0 ✅
- 教你/学会/掌握: 0 ✅
- 下期预告/回复获取/收藏本系列: 0 ✅

### CJK-Latin 间距: 0 issues ✅

### 源出处
- 只在元信息 header 出现 1 次 ✅
- 文末 GitHub 链接是交付物，不是原文链接 ✅

### 风格
- 分享型技术描述 ✅
- 无中段"原文说" ✅
- 代码块有注释 ✅

## Gate 3 — 主编终审 ✅ PASS

- 选题：自动化漏洞研究，对中国安全从业者价值高 ✅
- 原文可访问 ✅
- 无敏感内容 ✅
- 重写后完全去除个人评论，纯事实分享 ✅

## 产出
- 正文: articles/08-patch-diffing-pipeline.md (15.2KB)
- 封面: assets/cover-08-patch-diffing.jpg (28.6KB baseline JPEG)
- 缩略图: assets/cover-08-patch-diffing-thumb.jpg (4.8KB)
