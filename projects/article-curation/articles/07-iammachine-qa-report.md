# QA Report — 05-iammachine

**日期**: 2026-07-05
**文章**: 05-iammachine.md (215行, 11KB)
**源文**: https://www.abdulmhsblog.com/posts/iammachine/

## Gate 1 — 事实核查 ✅ PASS

### URL 验证
| URL | 状态 |
|-----|------|
| 原文链接 | 200 ✅ |
| exploit.ph (tgtdeleg) | 200 ✅ |

### 图片验证
| 图片 | 状态 |
|------|------|
| certexportpfx.png | 200 image/png ✅ |
| CertipyAuthNTLMV1HashMachineAccount.png | 200 image/png ✅ |
| s4u2selfabuse.png | 200 image/png ✅ |

### 技术细节验证
- Virtual Account 以 DOMAIN\COMPUTER$ 身份认证域资源 ✅
- MachineKeySet=FALSE 允许低权限导出私钥 ✅
- certreq/certutil/certipy 命令语法正确 ✅
- S4U2SELF silver ticket 提权流程正确 ✅
- tgtdeleg 作为无 ADCS 替代方案正确 ✅
- 证书吊销不受密码重置影响 ✅

## Gate 2 — 内容审校 ✅ PASS

### 禁用词检查: 0 处 ✅
### 禁用模式检查: 0 处 ✅

### 风格检查
- 红队兄弟分享型语气 ✅
- "我当时想""你可能要问了" 接地气表达 ✅
- 无中段"原文说" ✅
- 源出处只在元信息+结尾 ✅
- 代码块有中文注释 ✅
- 元信息行间空行 ✅
- 无 SVG 图片 ✅

## Gate 3 — 主编终审 ✅ PASS

- 选题定位：Windows AD 攻防技术，对中国安全从业者实用性强
- 原文可访问：200 ✅
- 无敏感内容 ✅

## 产出文件
- 正文: articles/05-iammachine.md (215行)
- 封面: articles/assets/cover-05-iammachine.jpg (39KB baseline JPEG)
- 缩略图: articles/assets/cover-05-iammachine-thumb.png
- 质检: articles/05-iammachine-qa-report.md
