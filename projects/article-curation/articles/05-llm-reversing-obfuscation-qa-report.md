# QA Report — 03-llm-reversing-obfuscation

**日期**: 2026-07-05
**文章**: 03-llm-reversing-obfuscation.md (274行, 16.2KB)
**源文**: https://www.elastic.co/security-labs/llm-reversing-vs-llm-obfuscation

## Gate 1 — 事实核查 ✅ PASS

### URL 验证
| URL | 状态 | 备注 |
|-----|------|------|
| 原文链接 (fixed) | 200 ✅ | 子代理错误使用了 `obfuscating-reversing-llam` (404)，已修正 |
| tigress.wtf | 200 ✅ | |
| github.com/JuliusBrussee/caveman | 200 ✅ | |
| gnupg.org/software/libgcrypt | 200 ✅ | |

### 关键数据交叉验证
- 40% 成功率 (8/20, 2 hang) ✅
- 平均成功 $2.39 / 失败 $4.83 ✅
- Phase 1: 4/7 ≈ 57% ✅
- Phase 2: 3/7 ≈ 43% ✅
- Phase 3: 0% ✅
- p2_flatten+MBA: $1.47→$6.60 (4.5×) ✅
- JIT 是最大克星 ✅
- CFF+MBA 比 VM+MBA 更有效 ✅
- Matryoshka Wall V1: $1.50/10min/30turns ✅
- Matryoshka Wall V2: $10/56min/61turns ✅
- Double Fond V7: 1/5, $5.2, 11.9min ✅
- Dispatch Maze V1: $2.56/12min/68turns ✅
- Dispatch Maze V2: $8.83/46min/119turns ✅

## Gate 2 — 内容审校 ✅ PASS

### 禁用词检查
- 在当今时代/值得注意的是/综上所述/总而言之/etc. → 0 处 ✅
- 教你/学会/掌握/下期预告/回复获取 → 0 处 ✅

### 风格检查
- 分享型语气 ✅
- 无中段"原文说/原文提到" ✅
- 源码出处只在元信息+结尾 ✅
- 代码块有中文注释 ✅
- 中英文混排规范 ✅
- 元信息行间空行 ✅

### 修正清单
- [x] 原文URL: obfuscating-reversing-llam → llm-reversing-vs-llm-obfuscation (2处)
- [x] 封面引用: .png → .jpg
- [x] 插入管道架构图 (image19.png)
- [x] 插入Double Fond版本对比图 (image8.png)

### 轻微备注
- 标题略长但准确，可接受
- Line 146 "我的看法" 是唯一的行内评论，符合聊天风格，可接受
- Phase 3 "全军覆没"措辞略强（1个PARTIAL），但在"恢复密码"目标层面准确

## Gate 3 — 主编终审 ✅ PASS

- 选题定位：AI+安全，在中国技术社区有高关注度
- 原文可访问：200 ✅
- 无敏感内容 ✅
- 对中国读者价值：介绍了3种新颖的LLM针对性混淆技术，启发式强

## 产出文件
- 正文: articles/03-llm-reversing-obfuscation.md (274行)
- 封面: articles/assets/cover-03-llm-reversing.jpg (42KB baseline JPEG)
- 缩略图: articles/assets/cover-03-llm-reversing-thumb.png
- 质检: articles/03-llm-reversing-obfuscation-qa-report.md (本文)
