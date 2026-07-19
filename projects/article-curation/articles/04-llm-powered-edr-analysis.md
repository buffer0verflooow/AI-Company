---
title: 我们拆了 Cortex XDR——9,350 条检测规则、7 个 ML 模型、6,358 条 YARA 签名，全部恢复
cover: /home/media/workspace/company/projects/article-curation/assets/cover-04-llm-powered-edr-analysis.png
---

> 原文: [LLM-Powered EDR Analysis](https://specterops.io/blog/2026/06/29/llm-powered-edr-analysis/)
> 作者: Adam Chester（SpecterOps，高级攻防安全工程师）
> 阅读时间: 约 12 分钟

## 一句话总结

SpecterOps 用 GPT-5.5-Cyber + Binary Ninja + 一个 while 循环，跑了几轮就把 Cortex XDR 的所有本地检测——YARA 规则、行为规则、ML 模型、CLIPS 决策逻辑——全部恢复，而且每条都能用真实操作触发验证。作者还说：这不是只针对 Cortex 一家，其他四大家 EDR 的结果现在已经躺在内部服务器上了。

## 怎么做到的

这可能是全篇最值得看的部分——整个「测试平台」简单得离谱。

### 硬件和模型

- 专用主机「Bishop」，7×24 跑 LLM
- OpenAI **GPT-5.4-Cyber** → 后来升级到 **GPT-5.5-Cyber**
- 模型跑在 Codex-CLI 里，Codex-CLI 跑在 Docker 容器里
- Binary Ninja 通过 MCP 暴露给模型

### 「Day Shift」循环

就一个 while 循环，代码全文：

```bash
#!/usr/bin/env zsh
source ./codex-docker.sh

while true; do
    [ -f "./STOP" ] && break
    codex-dind exec --yolo "你的任务是理解 Cortex 实现了哪些检测、Hook、
    缓解措施、告警、规则和模型。重点关注加载方式、使用方式、混淆/加密/压缩，
    最终提供提取原始内容供红队审查的方法。如果加载了 ML 模型，记录模型加载
    方式、工作原理、评估的特性和风险评分，以及隔离测试的代码。Cortex 产品在
    ProgramFiles 目录，ProgramData 包含运行中主机的副本数据。输出必须加入
    REPORT.md，STATE.md 用于记录状态。限制访问外部服务器，仅使用本地文件分析。"
    sleep 5
done
```

四个 Markdown 文件维持循环：

| 文件 | 作用 |
|------|------|
| `REPORT.md` | 将关键发现输出给人类审查 |
| `STATE.md` | 每个 Agent 循环跟踪关键事件 |
| `CODEMAP.md` | 存储反汇编中感兴趣/关键的地址引用，加速后续循环 |
| `AGENTS.md` | 告诉模型如何使用上述文件 |

**没有多 Agent 工作流，没有分工的子 Agent**。就 Binary Ninja、Codex 和一个 while 循环。循环的作用是绕过当前 LLM 的一个限制：给开放式目标时容易提前退出。通过每次清空上下文窗口并重新审查之前迭代的 Markdown，Agent 经常能发现新的线索继续推进。

## 具体拆出了什么

以下全部来自 LLM 的 `REPORT.md` 输出，每一条 Adam 都做了人工验证。

### 1. 用户态 DLL 注入分析

模型第一件事就指出了 Cortex 注入用户进程的 DLL：

```
cyinjct.dll 是共享的用户态 Hook 写入器和进程启动门控

位置:
- ProgramFiles/Traps/cyinjct.dll
- CyInject_InitializeRuntime 0x180006140
- CyInject_InstallInlineHook 0x18000b520
- CyInject_NtContinueShim 0x180005ed0
- CyInject_NtTestAlertShim 0x1800032c0

发现: Cortex 在 LdrInitializeThunk、NtContinue、NtTestAlert 和 KiUserApcDispatcher
上安装了早期内联 Hook，用于门控启动和后续特性。
```

直接告诉攻击者——绕过内联 Hook 的最高价值方法：**重映射纯净 ntdll、直接系统调用、手动映射、恢复被修改的函数序言**。

### 2. YARA 规则：6,358 条，全部恢复

模型发现了本地的加密 YARA 规则：

```
YARA 规则完全可以从本地文件离线恢复

位置:
- yara_plugin_config.lua
- yara_plugin.dll
- yara_data.json → 6,358 条 {id, action} 清单
- YaraRulesetsCache/yara_rulesets_cache.bin

发现: YaraSignatures_*.yara 文件本地存储，使用 ENCY 封装 → AES-128-ECB 解密
→ 解压。固定密钥前缀为 <已隐去>
```

模型不光识别了加密方式，还**自动写了 Python 解密工具**，在后续循环中把每一套规则解出来，整理成整齐的文件目录。

验证方法很直接：在良性 PE 文件末尾追加字符串 `github.com/MythicAgents`（SpecterOps 自家 C2 框架 Poseidon 的特征），Cortex 准确告警——解密出来的规则真实有效。

### 3. 行为检测规则：9,350 条 DSE + 4,209 条 BIOC

从明文的 LUA 配置文件中恢复：

```
DSE/BIOC 行为检测大部分可从明文元数据和主机覆盖项离线重建

位置:
- dse_rules_config.lua
- dse_modules.json
- dse_internals.json

发现: 主机附带 9,350 条 DSE 规则，含 4,209 条 BIOC 规则。本地动态覆盖禁用了
494 条 → 有效 DSE 8,856 条，有效 BIOC 3,989 条。

ChildProcessPattern 由 C01 元组本地构建。OpenProcess 是流入更高级 passwordStealing
规则的原语。Credential Gathering 直接映射到模块 ID 2（passwordStealing）。
```

验证：从提取的 LUA 文件中找了一条子进程检测规则，用匹配正则的命令行参数启动 `cmd.exe`，Cortex 告警命中。

### 4. 本地 ML 模型：7 个全部提取，还配了执行沙箱

```
本地分析 ML 是基于工程化特征的树集成评分器，不是神经网络运行时

位置:
- ml_plugin.dll
- tlaplugin.dll / tlapluginv2.dll
- LocalAnalysisModel_*.dat

模型参数:
- PE 7.1.1: parser family 1, 22,977 特征, 阈值 0.88
- PowerShell 8.4.0: type 4, 26,142 特征, 阈值 0.65
- VBS 8.6.0: type 5, 707 特征, 阈值 0.27
- JS 8.8.0: type 6, 9,355 特征, 阈值 0.75
```

模型提取出来还不够——**直接建了一个 Windows 执行沙箱**：加载 `tlaplugin.dll`（Cortex 自己的特征提取 DLL），喂入待测 PE 文件，输出模型的评分和分类。两个样本对比，恶意/良性分数一目了然。

Adam 的原话：*「看 LLM 不仅能提取模型，还能给我们建好 Windows 执行沙箱，说实话让人既惊叹又谦卑。」*

### 5. CLP 规则：用 LISP 写的检测引擎

这才是全篇最离谱的发现。

模型发现 Cortex 用了一种叫 **CLIPS** 的语言写决策规则——CLIPS 基于 LISP，Adam 说他第一次见到有商业产品用这个语言。看傻了之后直接跑出去买了本 LISP 教材。

解密过程：

```
完整的 CLP 离线解密路径:

1. 从 cysvc.dll 读取 64 字节嵌入密钥 → <已隐去>
2. 从 dse_common.lua 读取 k2u 和 v_s:
   - k2u = <已隐去>
   - v_s = 9
3. 派生 AES 材料:
   - key = full_secret[v_s:v_s+20] + k2u[:12]
   - iv = k2u[-16:]
4. AES-256-CBC 解密 .clp 文件
5. 去除 PKCS#7 填充
6. 解压 gzip 流

产出: 明文 CLIPS 源代码
```

解密出来的 CLIPS 规则长这样——判断 `reg save` 导出 SAM 蜂巢是否允许：

- 直接运行 `reg save HKLM\SAM out.bin` → **被检测**
- 用 CLIPS 规则里的白名单路径运行 `reg save HKLM\SAM C:\rcoc\sam.hive` → **顺利导出，无检测**

攻击者不需要猜——规则文本直接告诉你什么路径是被允许的。

### 6. 模拟规避测试（实验阶段）

Adam 还搭了一个实验性环境用于提前测试绕过效果。两个子 Agent：

- `EMULATE-WINDOWS.yaml` — 模拟 Windows 主机响应 API 调用
- `EMULATE-EDR.yaml` — 读取提取的 EDR 数据，判断操作是否会被检测

驱动层是 `AGENTS.md` 定义的 "Upside Down" C2 框架。完整代码在 [Gist](https://gist.github.com/xpn/085c87a05a752a55ed9cb9356b063e93)。

在这个模拟环境中：
- `ps` 命令能列出逼真的进程列表
- 尝试 `reg save` 导出 SAM → 模拟 EDR 正确识别并告警
- 用 CLIPS 规则中的白名单路径重试 → 模拟 EDR 静默通过

## 作者总结

> 我们跑的这个流程，每一家主流 EDR 都用完全相同的流程跑过。现在它们的提取规则、签名和模型就躺在我们的内网服务器上。

> 这篇文章不是针对某一家 EDR 的批评。这是对我们整个行业当前状态的一次现实检验。

> LLM 辅助的 EDR 规避再也不是理论。终端安全厂商必须重新考虑他们的策略了。

> 但这不意味着 EDR 没用了。本地规则和行为检测在短期内效果会打折扣，但要记住——EDR 只有一部分价值来自本地检测，大量的遥测数据持续从主机上送到云端分析。
