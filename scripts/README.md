---
tags: [scripts, tools, automation]
created: 2026-07-09
updated: 2026-07-19
---

# 🔧 共享脚本

跨项目、跨部门的实用脚本。不属于任何单一项目或部门。

## 脚本清单

| 脚本 | 用途 | 关联项目 |
|------|------|----------|
| `generate_cover.py` | AI 文章封面图生成（PIL + Playwright） | ai-edu-series、article-curation |
| `wechat_css_inline.py` | 微信公众号 CSS 内联（premailer + pygments） | 所有公众号发布 |
| `wechat_push.py` | 微信公众号草稿箱推送 | 所有公众号发布 |

## 使用方式

```bash
cd /home/pwn/workspace/company
python3 scripts/generate_cover.py --help
python3 scripts/wechat_css_inline.py --help
python3 scripts/wechat_push.py --help
```
