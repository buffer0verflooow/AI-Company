---
tags: [department, strategy, intel, info-sources]
created: 2026-08-11
updated: 2026-08-11
---

# 公司安全信息源地图 (实测版)

> 实测日期: 2026-08-11,本机 curl 直连(非代理),Chrome UA
> 用途: 文章选题素材 / 蜂群调研输入 / 市场雷达扩展 / 跳槽知识跟踪
> 状态图例: ✅=curl 直连可抓 ｜ ⚠️=受限(需浏览器/API key/特殊参数) ｜ ❌=不可抓(CF/SSL/壳)
> 采集通道: RSS=有标准 feed ｜ API=有公开接口 ｜ HTML=页面正则解析 ｜ Browser=需浏览器自动化

---

## 0. TL;DR

- 实测 60+ 源,**~45 个 curl 直连可抓**,含全部四大顶会论文站、主流 RSS 媒体、中文安全社区
- **论文通道最优**: dblp + arXiv + OpenAlex 三个 API 覆盖全部学术论文;NDSS/USENIX 官网直接出录用论文列表
- **RSS 通道最省事**: FreeBuf/Krebs/Schneier/The Record/SecurityWeek/Threatpost/Trail of Bits/PortSwigger/Unit42/SentinelOne/Cloudflare/Project Zero 全部有 feed
- **反爬重灾区**: BlackHat(CF JS挑战)、BleepingComputer/DarkReading/NCC Group/VX-Underground(CF 403)、Simon Willison(SSL 35)、CNNVD/H1 hacktivity(SPA 壳)
- 建议新建 `automation/security_intel.py` 每日采集,数据同时服务公众号选题 + 蜂群 + 市场雷达

---

## ✅ 已落地 (2026-08-11)

- **采集脚本**: `automation/security_intel.py` — 20 源四通道(RSS/API/HTML/X)实测 161 条/日
  - RSS: FreeBuf/SecurityWeek/Krebs/Schneier/The Record/Threatpost/Trail of Bits/PortSwigger/Unit42/SentinelOne/Cloudflare
  - API: arXiv cs.CR(15)/CISA KEV(20,增量)
  - HTML: 看雪/安全客/先知(正则解析;安全客需 `-k`;先知链接为绝对 URL)
  - **X 账号 (2026-08-11 新增, nitter.net RSS 实测复活)**: @simonw/@wunderwuzzi23/@taviso/@llm_sec
    - `https://nitter.net/<账号>/rss` 标准 RSS 2.0,pubDate 齐全;tiekoetter/xcancel 有反爬不选
    - 链接自动转 x.com;RT by/R to 前缀清洗后分类;dc:creator 存作者
    - 账号清单按赛道对齐(AI 安全红队/0day 研究),404 的账号(stevetroutman 等)已筛掉
- **赛道引擎 (2026-08-11 v2)**: 每条情报自动分类到战略赛道 `TRACK_RULES`
  - A 攻防实战 / B EDR对抗 / C Agent安全治理 / D 变现向(关键词命中: 标题×3/摘要×1, ASCII 词边界匹配)
  - CISA KEV 强制独立类(不参与赛道);渠道招募/招商/招聘广告标题 → ad 类滤除
  - 报告顶部统计: `A 8 / B 4 / C 6 / D 1 / KEV 20 / 泛安全 93 / 广告 3`
  - 3 天新鲜度窗口(HTML 源无日期默认新鲜;naive 日期按 UTC 处理)
- **存储**: `marketing/security_intel.db` (intel_items 表,SHA256 去重,track 列)
- **日报**: `marketing/runtime/security-intel/<date>/report.md` — 🎯选题池就绪区(按赛道) + ⚠️KEV区 + 📋泛安全区
- **cron**: `security-intel-daily` (job 69f1f0a80725,每日 08:00,no_agent,deliver=local)
  - wrapper 在 `~/.hermes/scripts/security_intel_wrapper.py`(cron script 必须相对该目录)
- **调试**: `python3 automation/security_intel.py --limit-sources xianzhi --no-persist`
- **已知解析坑**: 安全客链接 `/post/id/`、先知 `/news/数字`(相对或绝对 URL 都匹配)、看雪 RSS 403 走主页 HTML

---

## A. 学术会议 / 顶会论文 (全部 ✅)

| 源 | URL | 通道 | 状态 | 备注 |
|----|-----|------|:---:|------|
| NDSS | ndss-symposium.org | HTML/RSS | ✅ | accepted-papers 页 271 篇论文标题直提;feed/ 有新闻 |
| IEEE S&P (Oakland) | ieee-security.org | HTML | ✅ | 会议主页,cfp/accepted 页 |
| ACM CCS | sigsac.org | HTML | ✅ | 会议主页 |
| USENIX Security | usenix.org/conference/usenixsecurity26 | HTML | ✅ | 258KB 含论文列表 |
| DEF CON | defcon.org | HTML | ✅ | 会议/议题 |
| HITB | conference.hitb.org | HTML | ✅ | 会议/议题 |
| CanSecWest | cansecwest.com → secwest.net | HTML | ✅ | 301 跳转,自动跟随 |
| OffensiveCon | offensivecon.org | HTML | ✅ | 演讲列表 |
| KCon (知道创宇) | kcon.knownsec.com | HTML | ✅ | 国内会议 |

## B. 论文检索库 / 学术 API

| 源 | URL | 通道 | 状态 | 备注 |
|----|-----|------|:---:|------|
| dblp API | dblp.org/search/publ/api?q=ndss&format=json | API | ✅ | 注意用 `q=` 不用 `stream:`(后者 500);按会议查论文元数据 |
| arXiv cs.CR | export.arxiv.org/api/query?search_query=cat:cs.CR&sortBy=submittedDate&sortOrder=descending | API | ✅ | Atom XML,官方无风控;list/cs.CR/recent 页亦可 |
| OpenAlex | api.openalex.org/works?search=LLM+security | API | ✅ | 免费学术图谱,替代 Semantic Scholar |
| Semantic Scholar | api.semanticscholar.org | API | ⚠️ | 无 key 429 限流,有 key 才稳 |
| Google Scholar | scholar.google.com/scholar?q=LLM+security | HTML | ✅ | 实测无验证码、有结果;勿高频 |

## C. 漏洞库 / 威胁情报

| 源 | URL | 通道 | 状态 | 备注 |
|----|-----|------|:---:|------|
| NVD API v2 | services.nvd.nist.gov/rest/json/cves/2.0 | API | ✅ | 官方 CVE 数据 |
| CVE.org (MITRE) | cveawg.mitre.org/api/cve/{CVE-ID} | API | ✅ | 单 CVE 详情,要真实 ID |
| Exploit-DB | exploit-db.com | HTML/RSS | ✅ | rss.xml 通;PoC 库 |
| GitHub Advisories | github.com/advisories | HTML | ✅ | atom 406,HTML 解析 |
| HackerOne hacktivity | hackerone.com/hacktivity | Browser | ⚠️ | SPA 壳 1.7KB,需浏览器或 GraphQL |
| Seebug (知道创宇) | seebug.org | HTML | ✅ | 需 `-k`(SSL);中文漏洞情报 |
| CNNVD | cnnvd.org.cn | Browser | ⚠️ | SPA 壳 985B,需浏览器 |
| 补天 | butian.net | HTML | ✅ | 中文众测平台 |
| 漏洞盒子 | vulbox.com | HTML | ✅ | 中文众测平台 |
| CISA KEV | cisa.gov/.../known_exploited_vulnerabilities.json | API | ✅ | 1.5MB JSON,已利用漏洞权威清单 |
| MalwareBazaar | bazaar.abuse.ch | API | ✅ | 恶意样本 IOC |
| MITRE ATT&CK | attack.mitre.org | HTML | ✅ | 1.5MB,攻防技战术知识 |
| SANS ISC | isc.sans.edu | HTML | ✅ | 威胁事件日更 |
| Bugcrowd | bugcrowd.com | HTML | ✅ | 众测平台 |
| CNVD | cnvd.org.cn | ❌ | 521 反爬 |
| VX-Underground | vx-underground.org | ❌ | CF 403 |
| Cisco Talos | blog.talosintelligence.com | HTML | ✅ | 需 `-k` |

## D. 中文论坛 / 社区 (全部 ✅)

| 源 | URL | 通道 | 状态 | 备注 |
|----|-----|------|:---:|------|
| 看雪论坛 | bbs.kanxue.com | HTML | ✅ | RSS 403 被禁;主页 260KB 216 链接,帖子标题正则可提 |
| FreeBuf | freebuf.com | **RSS** | ✅ | **/feed 一次 20 条热文**,标题+时间,采集首选 |
| 安全客 | anquanke.com | HTML | ✅ | RSS 404;页面解析;一手攻击链文章 |
| 嘶吼 | 4hou.com | HTML | ✅ | 老牌安全媒体 |
| 吾爱破解 | 52pojie.cn | HTML | ✅ | 逆向/破解社区 |
| 先知社区 | xz.aliyun.com | HTML | ✅ | 阿里云安全社区,技术浓度高;RSS 404 |
| 火线安全 | huoxian.cn | HTML | ✅ | 众测+社区 |
| doonsec 公众号库 | wechat.doonsec.com | HTML | ✅ | 2.2MB,3962 个安全公众号,img-name 属性批量提取 |
| T00ls | t00ls.com | HTML | ✅ | 老牌低调论坛 |

## E. 中文媒体 / 厂商博客

| 源 | URL | 通道 | 状态 | 备注 |
|----|-----|------|:---:|------|
| 安全内参 | secrss.com | HTML | ✅ | RSS 404;决策者视角 |
| 腾讯玄武实验室 | xlab.tencent.com | HTML | ✅ | 根路径可访问(/zh 404);AI 安全密度最高 |
| 长亭 | chaitin.cn | HTML | ✅ | 厂商博客 |
| 奇安信 | qianxin.com | HTML | ✅ | 厂商门户 |
| 360 | 360.cn | HTML | ✅ | 厂商门户 |
| 蚂蚁安全 | security.alipay.com | HTML | ✅ | 蚂蚁安全实验室 |
| 知道创宇 | knownsec.com | HTML | ✅ | 2.5KB 偏壳,内容看 Seebug/KCon |
| 腾讯科恩 | keenlab.tencent.com | ⚠️ | 91B 壳,几乎无内容 |
| 微步在线 | x.threatbook.com | ⚠️ | 1.8KB 壳,需浏览器 |
| 绿盟博客 | blog.nsfocus.net | ❌ | CURLERR(52) 空响应 |

## F. 英文媒体 (RSS 大户)

| 源 | URL | 通道 | 状态 | 备注 |
|----|-----|------|:---:|------|
| The Hacker News | thehackernews.com | HTML | ✅ | feedburner RSS SSL 坏;页面解析 |
| SecurityWeek | securityweek.com/feed/ | **RSS** | ✅ | |
| KrebsOnSecurity | krebsonsecurity.com/feed/ | **RSS** | ✅ | 独立调查记者 |
| Schneier on Security | schneier.com/feed/atom/ | **RSS** | ✅ | 安全通识/密码学 |
| The Record | therecord.media/feed | **RSS** | ✅ | Recorded Future 旗下 |
| Ars Technica Security | arstechnica.com/security/ | HTML | ✅ | RSS 404;深度技术 |
| The Register Security | theregister.com/security/headlines.atom | **RSS** | ✅ | |
| Threatpost | threatpost.com/feed/ | **RSS** | ✅ | |
| BleepingComputer | bleepingcomputer.com | ❌ | CF 403 |
| DarkReading | darkreading.com | ❌ | CF 403 |

## G. 英文研究博客 / AI 安全

| 源 | URL | 通道 | 状态 | 备注 |
|----|-----|------|:---:|------|
| Trail of Bits | blog.trailofbits.com/feed/ | **RSS** | ✅ | AI/ML 安全 + 审计方法论 |
| PortSwigger Research | portswigger.net/research/rss | **RSS** | ✅ | Web 漏洞研究权威 |
| CrowdStrike | crowdstrike.com/en-us/blog/feed/ | **RSS** | ✅ | |
| Unit 42 | unit42.paloaltonetworks.com/feed/ | **RSS** | ✅ | |
| SentinelOne | sentinelone.com/blog/feed/ | **RSS** | ✅ | |
| Cloudflare Blog | blog.cloudflare.com/rss/ | **RSS** | ✅ | 网络/安全边缘研究 |
| Project Zero | projectzero.google | HTML/RSS | ✅ | atom 13MB 全量;新域名 googleprojectzero.blogspot.com 亦通 |
| GitHub Security Lab | github.blog/security/ | HTML | ✅ | |
| MSRC Blog | msrc.microsoft.com/blog/ | HTML | ✅ | RSS SSL 坏;微软漏洞响应 |
| Mandiant | cloud.google.com/security/mandiant | HTML | ✅ | 301 跳 Google Cloud |
| OWASP GenAI | genai.owasp.org | HTML | ✅ | 需 `-k`;LLM 安全基准 |
| Orange Tsai | blog.orange.tw | HTML | ✅ | 需 `-k`;顶级 Web 漏洞研究员 |
| NCC Group | research.nccgroup.com | ❌ | CF 403 |
| Simon Willison | simonwillison.net | ❌ | CURLERR(35) SSL 老问题;LLM 安全天花板,需浏览器通道 |

## H. 用户已知源的状态确认

| 用户已知 | 实测结论 |
|---------|---------|
| NDSS 网络安全论文 | ✅ 官网 accepted-papers + feed + dblp API 全覆盖 |
| BlackHat 会议 | ❌ 官方站 CF JS 挑战,curl 必 403;替代:浏览器自动化或第三方议程转载 |
| FreeBuf | ✅ 全公司最好抓的源之一(/feed RSS 直出) |
| 看雪论坛 | ✅ RSS 被封但主页 HTML 可解析 |

---

## 接入建议

1. **新建 `automation/security_intel.py`**(每日 cron):三类采集
   - RSS 类(零摩擦): FreeBuf / SecurityWeek / Krebs / Schneier / The Record / Threatpost / Trail of Bits / PortSwigger / Unit42 / SentinelOne / Cloudflare / Project Zero → XML 解析
   - API 类: dblp(按会议拉新论文) + arXiv cs.CR(每日新增) + CISA KEV(增量) + NVD
   - HTML 类(正则): NDSS accepted papers / 看雪主页 / 安全客 / 先知 / doonsec
2. **产物**: 每日简报 markdown(标题+链接+摘要)落 `marketing/runtime/security-intel/`;高价值条目标签化喂 Swarm KB
3. **三个消费方**: 公众号硬核选题(article-production) / 蜂群调研上下文 / 市场雷达主题扩展
4. **受限源处理**: BlackHat/Simon Willison/DarkReading 等留 Browser 通道(低频手动或 cua-driver),不做高频采集
5. 本清单与 `source-catalog.md`(通用工程向)互补,此文档为安全情报专用

## 维护

- 新增源: 先 curl 探测(参考本文档格式),通才入表
- 失效源: 每季度重测一次全表(curl 批量脚本可复用)
- 更新日期: 2026-08-11 首版实测
