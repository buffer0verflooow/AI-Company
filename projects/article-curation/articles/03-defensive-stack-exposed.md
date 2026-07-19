---
title: 防御栈正在裸奔——TrustedSec 用 LLM 逆向五款商业 EDR 的内部评估报告
cover: /home/media/workspace/company/projects/article-curation/assets/cover-03-defensive-stack-exposed.png
---

> 原文: [The Defensive Stack is Exposed: LLMs, Reverse Engineering, and the End of Opaque Defense](https://trustedsec.com/blog/the-defensive-stack-is-exposed)
> 作者: Justin Elze（TrustedSec）
> 阅读时间: 约 10 分钟

## 一句话总结

TrustedSec 对五款商业终端安全产品做了内部评估：用 LLM 辅助逆向工程，原本需要资深逆向工程师数周的工作被压缩到几天。同一套分析流程在不同厂商之间只需微调提示词即可迁移。文章末尾附了两份可立即使用的实操材料——一套 EDR 逆向 Skill 和一套完整的分析 Prompt。

## 他们实际做了什么

不是理论推演——TrustedSec 实打实拿了五款商业终端产品做内部评估。结果：

> 以前需要熟练逆向工程师花几周才能完成的工作，现在几天就做完了。模型负责映射、归纳和跨版本对比，人类只做验证和判断。

同样的分析流程在 AV、EDR、安全设备之间只需微调提示词即可迁移。

他们发现，拿到防御产品的难度被高估了——产品会出现在大学下载站、客户试用、VirusTotal 提交、GitHub 仓库、配错的 S3 存储桶里。厂商的「不卖给研究人员」政策挡不住真正的攻击者。一旦拿到手，问题不再是能不能获得，而是**多久能理解它**——这部分被 LLM 彻底改变了。

## 具体能分析出什么

### 规则提取

YARA 规则、行为签名、检测条件——可以从磁盘工件和观察行为中提取、重建或推断。**有些厂商的产品在仅一次解密之后，数百条行为规则就是可读的 Lua 源码。**

### 本地 ML 模型分析

特征提取逻辑、评分阈值、判定边界——可以研究到足够理解「产品看重什么、忽略什么」的程度。

### 策略和排除项挖掘

端点上的有效策略经常是直接可读的。有时甚至在世界可读的注册表项里。可信路径、进程白名单、签名规则、命令行排除项、文件类型掩码、每条规则的静默标志——攻击者可以在做任何高噪动作之前，先挑出监控最少的路径。

### 跨版本对比

更新包直接暴露厂商改了什么、悄悄修了什么、什么突然变得重要到值得改。

### 漏洞发现

分析检测逻辑的同一条流水线也能发现产品自身的安全漏洞。解析例程、IPC 接口、内核回调、更新机制、以 SYSTEM 权限运行的本地服务、防篡改逻辑——全部可审查。**「代码越多，问题越多，而防御产品带的代码非常多。」**

### 一个具体的坑

多款产品存在**合法的运营状态会放松防篡改保护或丢弃遥测数据**，而触发这些状态的条件在 Agent 里可以直接读到。端点不报警，未必是没出事——可能只是活动落在了阈值以下、命中了排除项、触发了缓存干净判定、或者传感器处于维护/云断开状态。

## 他们公开了什么：两份可用的材料

### 1. EDR Reverse Engineering Skill

Gist: https://gist.github.com/HackingLZ/8956b015a55412522d22a88e0dd284fc

一份可安装到 Claude Code 的 Skill，标准化了 EDR 逆向工程的完整流程。包含：

**目录结构**——定义了 `<product>/` 下的完整工作区布局：
```
<product>/
├── extracted/          # 解包后的二进制、配置、安装包
├── ghidra_output/      # Ghidra 反编译输出（每文件一套伪代码、函数列表、字符串、摘要）
├── rules/              # 提取的检测逻辑（YARA/Lua/行为/排除项）
├── models/             # 提取的 ML 模型和脚本引擎
├── analysis/           # 九份分析文档（架构→漏洞→检测盲区→规则→ML→排除→协议→战术→观察）
├── pocs/               # PoC 代码（漏洞利用/检测规避/ML绕过/组合链）
├── probes/             # 实时环境交互脚本（协议/模糊测试/枚举）
├── reports/            # 交付物（技术报告/安全公告/演示文稿）
└── tools/              # 产品专用工具（解密/下载/扫描器）
```

**Phase 0：解包提取**——识别安装包类型（MSI/CAB/WiX Burn/NSIS/InnoSetup），逐层解包，编录每个文件（路径、大小、类型、SHA256、签名），识别加密资产（魔数、熵、异或模式、硬编码密钥）。

**Phase 1：全量反编译**——每个二进制都要产出伪代码。两步走：
- 先用 Ghidra 无头模式批量覆盖（`analyzeHeadless` + `DecompileAll.py`）
- 再用 Ghidra MCP 交互式精准跟进（重命名符号、追踪交叉引用、注释确认项和假设项）

优先级：主 Agent → 内核驱动 → ML/AI 库 → 检测引擎 → 通信组件 → Hook/注入组件 → 辅助服务 → 支持库。.NET 程序集用 `ilspycmd`。

**Phase 2：九份分析文档**——每份都有严格的结构要求：

| 文档 | 内容 |
|------|------|
| `00_ARCHITECTURE.md` | 组件清单、进程架构、IPC机制、内核组件、持久化、自保护、更新通信架构、第三方库、依赖图 |
| `01_VULNERABILITY_ANALYSIS.md` | 攻击面枚举、逐组件漏洞评估、已确认发现表（ID/标题/严重性/CVSS/状态/类型）、每条发现包含根因+反编译代码引用+复现步骤+影响+修复建议、攻击链 |
| `02_DETECTION_GAP_ANALYSIS.md` | MITRE ATT&CK 覆盖矩阵（技术×战术，标注 COVERED/PARTIAL/GAP）、每条盲区声明必须引用反编译代码 |
| `03_RULE_EXTRACTION.md` | 规则清单（YARA/Lua/行为/签名/排除项总计）、提取方法论、规则质量评估 |
| `04_ML_EXTRACTION.md` | 模型清单（传统ML：决策树/神经网络/TFLite/XGBoost/Bonsai；脚本引擎：Lua VM/JS/Python/DSL）、特征提取逻辑、评分阈值、已知盲点、绕过技术 |
| `05_PREFILTER_EXCLUSION_ANALYSIS.md` | 预过滤器和排除项分析 |
| `06_PROTOCOL_COMMS_ANALYSIS.md` | 协议和通信分析 |
| `07_TRADECRAFT.md` | 战术利用 |
| `08_OBSERVATIONS.md` | 观察汇总 |

**Phase 3-4：PoC + 实时探针 + 报告**——漏洞利用代码、规避 PoC、ML 绕过、组合攻击链；网络/管道/RPC/HTTP 探针、模糊测试；技术报告 + 安全公告 + 演示文稿。

### 2. EDR Analysis Large Prompt

Gist: https://gist.github.com/HackingLZ/a9f71c8ea7bd6d867765bda0af2460f6

与 Skill 配套的完整提示词，涵盖同等深度的工作区模板和指令，可直接投入 Claude Code 等 Agent 工具使用。

## 防御建议

TrustedSec 给出的结论不是「修好你的 EDR」，而是架构层面的：

> 不要把全部重量压在一层攻击者能研究透的防御上。

具体建议：

- **主机加固**——应用控制（WDAC/AppLocker 强制执行模式）、LSA Protection、Credential Guard、ASR 规则、PowerShell 脚本块日志、LAPS 本地管理员隔离
- **SIEM 关联**——不依赖 EDR 的告警流，直接摄入原始遥测（进程/文件/注册表/网络/模块加载）和 Windows Security/PowerShell/Sysmon/DNS/代理日志，写关联规则覆盖 EDR 可能漏掉的路径
- **身份检测**——Entra ID 登录风险和用户风险策略 + 条件访问强制执行 MFA；检测不可能旅行、MFA 疲劳、令牌盗窃、服务主体滥用、目录侧动作（Kerberoasting/DCSync/ACL 变更）
- **EDR 只是信号之一**——不是唯一防线。加金丝雀文件/账户/凭证（检测意图而非模式），关注网络出口异常（信标/DNS/新域名）

## 速度不对称

攻击者发现即用（几天），厂商从发现到全量推送修复走的是工单队列→回归测试→客户试点→分批推送（几周到几个月）。这个差距不解决，攻击者侧的曲线就会一直比防御者侧跑得快——和谁有更好的工具无关。
