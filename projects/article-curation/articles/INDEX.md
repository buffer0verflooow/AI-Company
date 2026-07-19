---
tags: [project, article-curation, index]
created: 2026-07-05
updated: 2026-07-05
---

# 文章索引

## 按时间线

| # | 日期 | 标题 | 来源 | 状态 |
|---|------|------|------|:---:|
| 01 | 2026-07-05 | 一个人+5台虚拟机+Claude Code：他是如何让LLM自己挖0day的 | zsec.uk | ✅ |
| 02 | 2026-07-05 | 花$30让AI挖漏洞结果毛都没找到——本地模型的正确打开方式 | projectblack.io | ✅ |
| 03 | 2026-07-05 | 防御栈正在裸奔——用LLM逆向工程商业安全产品，五款EDR无一幸免 | trustedsec.com | ✅ |
| 04 | 2026-07-05 | 我们拆了Cortex XDR——9,350条规则、7个ML模型、6,358条YARA签名全部恢复 | specterops.io | ✅ |
| 05 | 2026-07-05 | LLM 逆向 vs LLM 混淆——Elastic 的代码保护实验 | elastic.co | ✅ |
| 06 | 2026-07-05 | Zero Day 编排——用任意模型找漏洞的框架 | provos.org | ✅ |
| 07 | 2026-07-05 | IAMMachine——当机器账户成为域控的万能钥匙 | abdulmhsblog.com | ✅ |
|| 08 | 2026-07-05 | Patch Diffing Pipeline——$300 自动化挖 Windows 0day | originhq.com | ✅ |
|| 09 | 2026-07-09 | PowerShell 还没死——红队实战攻击武器与免杀技术全景 | screetsec.com | ✅ |
|| 10 | 2026-07-09 | BPF 映射投毒：从内部击穿 EDR 的监控体系 | matheuzsecurity.github.io | ✅ |
|| 11 | 2026-07-09 | 内核 Rootkit 如何让 eBPF 工具集体失明 | matheuzsecurity.github.io | ✅ |
||| 12 | 2026-07-09 | 0xMatheuZ 全站技术纵览：13篇深度研究 | matheuzsecurity.github.io | ✅ |
||| 13 | 2026-07-09 | P³：用进程启动参数投毒，四款 EDR 无一告警 | sensepost.com | ✅ |
||| 14 | 2026-07-10 | EDR 内部架构与绕过技术全景：从内核回调到调用栈欺骗 | 0xdbgman.github.io | ✅ |

## 按主题

### 安全研究

- [[projects/article-curation/articles/01-bullying-llms-into-finding-0days\|#01 让 LLM 自己挖 0day]] — Andy Gill 的自主漏洞挖掘系统全拆解
- [[projects/article-curation/articles/01-local-ai-cybersecurity\|#02 本地 AI 做网络安全的正确打开方式]] — 四种方案对照实验，$30 vs 6000万 token
- [[projects/article-curation/articles/03-defensive-stack-exposed\|#03 防御栈正在裸奔]] — TrustedSec 用 LLM 逆向五款商业 EDR
- [[projects/article-curation/articles/04-llm-powered-edr-analysis\|#04 我们拆了 Cortex XDR]] — SpecterOps 对 Cortex 的完整拆解
- [[projects/article-curation/articles/05-llm-reversing-obfuscation\|#05 LLM 逆向 vs LLM 混淆]] — Elastic 的代码保护对抗实验
- [[projects/article-curation/articles/06-zero-day-orchestration\|#06 Zero Day 编排]] — IronCurtain 开源框架
- [[projects/article-curation/articles/07-iammachine\|#07 IAMMachine]] — Windows AD 机器账户提权
- [[projects/article-curation/articles/08-patch-diffing-pipeline\|#08 Patch Diffing Pipeline]] — 自动化漏洞挖掘管线
- [[projects/article-curation/articles/09-powershell-red-team-evasion\|#09 PowerShell 红队攻击全景]] — AMSI 绕过、ClickFix、内存执行、日志清除
- [[projects/article-curation/articles/13-process-parameter-poisoning\|#13 P³：进程参数投毒]] — 用 CreateProcessW 启动参数传递 shellcode，四款 EDR 零告警

### Linux 内核安全

- [[projects/article-curation/articles/10-bpf-map-poisoning\|#10 BPF 映射投毒]] — 用 bpf(2) API 直接修改 EDR 的 BPF map 使其失明
- [[projects/article-curation/articles/11-breaking-ebpf-security\|#11 内核 Rootkit 致盲 eBPF]] — ftrace hooking 系统性破坏 ringbuf/iterator/perf/map
- [[projects/article-curation/articles/12-matheuz-tech-overview\|#12 全站技术纵览]] — 13篇文章完整技术栈：LD_PRELOAD → io_uring → eBPF → EDR

### AI / ML

_待添加_

### 系统架构

_待添加_
