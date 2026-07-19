---
title: "$300 搭一条 Patch Tuesday → 0day 自动化管线"
cover: /home/media/workspace/company/projects/article-curation/assets/cover-08-patch-diffing.jpg
---

# $300 搭一条 Patch Tuesday → 0day 自动化管线

> 原文: https://www.originhq.com/research/patch-diffing-pipeline

> 作者: Tyler Holmwood, Origin HQ

> 阅读时间: 约 18 分钟

## 一句话总结

一个 Windows 安全研究员用 Rust + Claude Agent SDK + 四个 MCP，搭了一条从 Patch Tuesday 公告自动拉补丁、做 binary diff、生成 PoC exploit 的完整管线，总花费约 $300，跑通了 CVE-2026-27914 的端到端提权。

## 他们实际做了什么

Anthropic 的 Mythos（Project Glasswing）演示让安全圈看到了 AI 辅助漏洞研究的方向：把前沿模型对准软件，让它发现并利用漏洞。Tyler Holmwood（Origin HQ）想验证一个更落地的问题——一个普通研究员用现成工具能搭出多少？

他选了 patch diffing 作为切入点。这是一种经典的漏洞研究技术：对比补丁前后的二进制，找到厂商修了什么，用于教育、变种分析或 N-day 武器化。工作流大部分是重复的、可自动化的，而且已经有成熟的开源工具。

最终产出是两阶段管线：

- **PatchWatch**（Rust 写的 ingestion 引擎）— 从 Patch Tuesday 拉更新、做 binary diff、生成 LLM 可读的 diff 报告
- **Pocsmith**（基于 Claude Agent SDK）— 拿 diff 报告驱动一个带内核调试器的 Hyper-V 虚拟机，自动生成并验证 PoC exploit

配套环境包括四个 MCP server：hyperv-mcp（VM 生命周期）、kd-mcp（内核调试器，22 个 tool）、pocsmith-mcp（编译/执行/状态控制）、pyghidra-mcp（静态分析，@clearbluejar 的作品，唯一的第三方 MCP）。

所有工具全部开源在 GitHub 上。

## 管线长什么样

Patch Tuesday 每月第二个周二，微软发一个累积安全更新（如 KB5083768）加上 CVE 列表。KB5083768 包含超过 28,000 个文件变更——手动 diff 不可能，全扔给 LLM 会在 token 费用上破产。

### PatchWatch：Rust ingestion 引擎

`patchwatch poll` 命令查询 MSRC Security Update Guide API，拉取本次发布的所有 CVE（当前限定 x64 桌面 SKU 26H1）。PatchWatch 找到对应的 KB，从 Microsoft support feed 拉 CSV 格式的文件变更列表（CSV 缺失时回退到下载并解压 MSU）。

**分层省钱**：

- CVSS ≥ 9.0 或标记为 actively exploited 的 CVE → Tier 1 做 LLM triage
- 其他 CVE → 存本地 SQLite DB，后面再说

Triage 阶段把 CVE 描述 + 受影响文件列表发给 LLM，让它对每个文件打分（这个 CVE 的修复最可能在哪），附带简短理由。评分持久化，re-run 是幂等的（除非 MSRC revision number 变了不会重复花钱）。

手动选中某个 CVE 后（CLI 或 Web UI），orchestrator 从 Winbindex（@m417z 的 Windows 二进制索引）拉取补丁前后二进制，用 Ghidriff（@clearbluejar 的作品）做 binary diff，然后喂给 LLM 做两轮分析：

1. **Synthesis pass** — 从紧凑的变更函数列表中筛选最有趣的改动
2. **Deep analysis pass** — 遍历每个被标记函数的 decompiled C 代码，产出具体发现

Ghidriff 的输出被解析成两份 artifact：

- **DiffSummary** — 紧凑的变更函数列表、相似度、字符串变更（便宜，喂给 synthesis pass）
- **DiffIndex** — 每个修改函数的完整 pre/post decompiled C（喂给 deep analysis）

最终产出 `report.md`，包含：补丁做了什么的叙述、相关函数及其 pre/post 代码、confidence 排名的修复位置列表。

**这个 report.md 就是两阶段管线的握手协议**——PatchWatch 产它，Pocsmith 吃它。

### Pocsmith：Claude Agent SDK 驱动的 exploit 生成

Pocsmith 不用裸 API + 手写 loop。作者选 Claude Agent SDK 的原因是"大部分工作在 toolset 上，不在 loop 上"。

环境就是研究员自己手写 Windows POC 会用的：一个 KDNET 连接的 Hyper-V VM、Ghidra（diff 阶段已经建好 project）、C 编译器、基本 VR 工具（impacket、sysinternals、Python venv）。每个能力包成一个 MCP server。

Agent 启动时拿到三个目标 level 之一：

- **Level A** — crash 复现。触发漏洞能不能崩？
- **Level B** — 控制原语。能把 crash 捋成可靠的读写吗？
- **Level C** — 完整 exploit。能不能真正提权或代码执行？

每个 level 在 `pocsmith.yaml` 里有独立的 time/iteration/dollar 预算，硬限制。

Session 按 **phase** 组织。每个 phase 是一次 bounded 的 Agent SDK run，结束时 agent 把工作状态写到 `notes.md`。Phase 存在的原因是 context window 有限、exploit 研究本质是迭代的。Phase 边界就是 checkpoint——agent 清空上下文重新来过，但通过 notes.md 保持连续性。Pocsmith 不会 summarize 或 interpret 这些 notes，agent 自己的 notes 就是它自己的记忆。

验证逻辑：拿到结果后，restore VM 到干净状态再跑一遍。两次信号一致，才 promote artifacts 到 `artifacts/` 目录并调 reporting LLM 生成最终报告。所有 workspace artifact 保留，研究员可以随时手动介入——在 workspace 里开一个继承相同 MCP 配置的 Claude 实例接着干。Pocsmith 支持 `--resume` 恢复，`--hint` 注入上下文。

## 实战案例：CVE-2026-27914（MMC 提权）

Tyler 从 2026 年 4 月发布中选了这个漏洞来验证管线。CVE-2026-27914 是 Microsoft Management Console（mmc.exe）的提权漏洞，CVSS 7.8，KB5083768。

### 漏洞本身

微软管理控制台（mmc.exe）在加载 `.msc` 控制台文件时，**完全没有检查 Mark-of-the-Web (MOTW) 信任状态**。你从网上下载一个 `.msc` 文件，Windows 会在文件上打一个 Zone.Identifier 标记（MOTW）。但旧版 mmc.exe 不鸟它——直接解析 XML，找到 `<SnapinCache>` 里的 CLSID，然后 `CoCreateInstance` → `LoadLibraryEx`，把 DLL 加载进进程。如果 mmc.exe 以管理员权限跑，DLL 也跟着是管理员权限。

### 补丁修了什么

KB5083768 的修复在 `CAMCDoc::ScOnOpenDocument` 里加了一道 `_IsFileSourceUntrustworthy` 门禁——对原始路径和 MUI 解析后的路径都检查一遍，不信任何一方就直接返回错误码 `ScFromMMC(0x80030070)`。`DisplayFileOpenError` 也扩展了，看到这个新错误码就用新的 resource id `0x3494` 显示"untrusted source"消息。

补丁前后的关键代码对比：

```
// 补丁前：只检查路径非空，直接走到 GetFileMUIPath
if ((in_R8 == 0) || (*in_R8 == 0)) { ... }
else { GetFileMUIPath(0, in_R8, ...); }

// 补丁后：加了两次 _IsFileSourceUntrustworthy 检查
if ((in_R8 == 0) || (*in_R8 == 0)) { ... }
else {
  if (_IsFileSourceUntrustworthy(in_R8)) {
    return ScFromMMC(0x80030070);  // 拦截
  }
  ScGetMuiPath(...);
  if (_IsFileSourceUntrustworthy(mui_path)) {
    return ScFromMMC(0x80030070);  // 再拦一次
  }
  // 两次都通过才走到原来的 load path
}
```

### 管线是怎么打下来的

PatchWatch triage 把 `mmc.exe` 排在第一（confidence 0.60），deep analysis 阶段 confidence 升到 0.70，精准锁定 `ScOnOpenDocument`（relevance 0.95）。Ghidriff diff 结果：209 added、17 deleted、3,544 modified functions。

Pocsmith 接到 diff 报告后，构建的 exploit chain：

1. **低权限用户 bob（Medium IL, S-1-16-8192）** 在 `C:\Users\Public\` 写一个带 MOTW 标记（ZoneId=3）的 `poc_eop.msc`
2. **管理员 tyler（High IL, S-1-16-12288, BUILTIN\Administrators）** 打开这个文件
3. 旧版 mmc.exe 不检查 MOTW，走到 `ScLoadConsole`，解析 XML，`CoCreateInstance` 一个 CLSID
4. 这个 CLSID 的 HKLM `InprocServer32` 指向 `C:\poc\evil.dll`，被 `LoadLibraryEx` 加载进 mmc.exe（PID 8140）
5. `evil.dll!DllMain` 以 tyler 的 token 执行 `cmd.exe /c whoami /all` 和 `calc.exe`，写入提权证据

调试器捕获的信号：

```
ModLoad: 00007ffc`0a570000 00007ffc`0a621000   C:\poc\evil.dll
BREAK_HIT_CreateProcessInternalW
BREAK_HIT_CreateProcessInternalW
```

`eop_proof.txt` 显示 Token User = `Agent-Test-TH\tyler`，Integrity Level = HIGH（RID 0x3000），Elevated = yes，PID = 8140。`eop_whoami.txt` 确认了 `BUILTIN\Administrators`、`Mandatory Label\High Mandatory Level S-1-16-12288`、`SeDebugPrivilege` 和 `SeImpersonatePrivilege` Enabled。

### 一个诚实的 caveat

微软给这个 CVE 打的 CVSS 分数是 PR:L / UI:N（低权限、无需用户交互）。但 agent 实际打出来的 chain 是 PR:H / UI:R——需要管理员主动打开文件。也就是说 agent 的 exploit 确实成功提了权，走的是真实的 patch bypass 路径，但没有完美命中微软评分的那个入口点。作者推测可能还存在一个 no-click 的调用链（如 MMC20.Application automation、MRU restore on launch、或 shell verb），但本次迭代没有枚举到。补丁的 gate 只加在 `ScOnOpenDocument` 里，这些 sibling caller 可能仍然能绕过。

文章原文也有一段 Level A 的断点证据，证明补丁前的 `ScOnOpenDocument` 确实直接 fall through 到 `ScLoadConsole`，中间没有 trust check：

```
BREAK_HIT 1 at mmc+0x15260 (ScOnOpenDocument)
BREAK_HIT 2 at mmc+0xed34c (ScLoadConsole)
// 两个断点连续命中，证明没有中间检查
```

## 第二个案例：CVE-2026-41096（ws2_32.dll 远程代码执行）

文章末尾附了一个 bonus 报告——2026 年 5 月 Patch Tuesday 的 CVE-2026-41096，Windows DNS Client 远程代码执行，CVSS 9.8，KB5089548。这个案例展示了 **patch diff 如何精确定位漏洞的本质**。

PatchWatch diff 之后发现两个独立的修复模式：

### 修复 1：危险的字符串长度计算（ws2_32.dll）

`WSCGetApplicationCategoryEx` 里有一段手写的 NUL 搜索循环。函数的栈缓冲区只有 0x104 个 wchar，`ExpandEnvironmentStringsW` 可能恰好填满缓冲区、不留 NUL 终止符，那个手工 walk 就找不到 NUL，最终返回长度 0x104。这个最大长度被传给下游的 `ConvertWStrToHash`，后者会读 0x104 个 wchar——如果跨越了页边界，直接 ACCESS_VIOLATION。

补丁前：

```c
lVar9 = 0x104;
pWVar4 = local_248;
do {
  if (*pWVar4 == L'\0') break;
  pWVar4 = pWVar4 + 1;
  lVar9 = lVar9 - 1;
} while (lVar9 != 0);
// 找不到 NUL 时返回 0x104，传入下游
```

补丁后引入 `StringLengthWorkerW` 这个 bounded helper，超过 `cchMax` 就不找了，返回 `E_INVALIDARG`（0x80070057），长度归零：

```c
HRESULT StringLengthWorkerW(STRSAFE_PCNZWCH psz, size_t cchMax, size_t *pcchLength)
{
  // 如果 cchMax 个字符内找不到 NUL，返回 E_INVALIDARG
  // 而不是默默地返回最大长度
}
```

agent 写出了一个确定性的 crash reproducer——本地调用 `WSCGetApplicationCategoryEx`，传入 `"%PATH%"` 重复 8 次的路径，让 `ExpandEnvironmentStringsW` 填满 0x104-wchar 缓冲区。确定性触发 `EXCEPTION_ACCESS_VIOLATION`（0xC0000005）在 `ws2_32!ConvertWStrToHash+0x50`，call stack 确认 caller 是 `WSCGetApplicationCategoryEx+0x207`——正好是补丁改过的 call site。

### 修复 2：HPACK 整数溢出（webio.dll）

另一个修复在 `webio.dll!HkAddPairToTable`——做 HPACK 表分配时缺少整数溢出检查。攻击者可以通过精心构造的 HTTP/2 HEADERS frame 让加法 wrap 32-bit，拿到一个很小的堆分配但写入很大数据（经典 CWE-122 堆溢出）。Windows DNS client 在开启 DNS-over-HTTPS (DoH) 时会用到这个 HTTP 栈，这是最可能的网络可达触发面。

补丁后每个加法都加了 unsigned wrap 检查，检查不过直接返回 `STATUS_INTEGER_OVERFLOW`（0xC0000095）。同时 per-entry 结构大小从 0x48 涨到 0x50 字节。

这个案例展示的是：PatchWatch 通过 diff 精准找到了两个完全不同的脆弱点。虽然 Level A（crash 复现）还不等于远程 RCE，但 patch diff 已经告诉你"问题在哪、为什么是问题、应该怎么修"——剩下的交给 exploit 开发者去补完。

## Token 费用

构建和迭代这个 CVE 花了大约 **$300 USD** 的 API token，加上 Team subscription。大部分花费是 Opus-4.7。作者还没优化 token 成本——明显的 quick win 包括 diff 阶段的 prompt caching、tiered model selection（Haiku 做 triage、Sonnet 做 synthesis、Opus 只在真正需要的地方用），最终目标是解耦 Anthropic、支持任意模型。

## 工具箱一览

| 组件 | 作者 | 作用 |
|------|------|------|
| PatchWatch | Origin HQ | Rust ingestion 引擎，拉 CVE → diff → LLM 报告 |
| Pocsmith | Origin HQ | Claude Agent SDK exploit 生成，phase-based |
| hyperv-mcp | Origin HQ | Hyper-V VM 生命周期：快照、恢复、拷文件、非特权执行 |
| kd-mcp | Origin HQ | 内核调试器封装，22 个 tool，attach/breakpoint/read/step/resume |
| pocsmith-mcp | Origin HQ | compile_c（MSVC）、attacker_py（安全研究 venv）、状态控制 |
| pyghidra-mcp | @clearbluejar | 静态分析：理解函数结构、定位 offset、读 decompiled C |
| Ghidriff | @clearbluejar | 命令行 binary diff 引擎 |
| Winbindex | @m417z | Windows 二进制索引，按 KB/version/hash 查询 |

每个 MCP 通过 per-workspace `.mcp.json` 注入 agent。PatchWatch 还会把 diff report、Ghidra project、缓存的 pre/post 二进制都放进 workspace——agent 不需要自己启动任何东西。

## 对防守方的意义

NCSC 和 Cloud Security Alliance 最近都发了关于"patch wave"的建议：

- **打补丁现在是一场赛跑**。Patch Tuesday 发布和武器化之间的窗口正在急剧缩小。能容忍的地方默认自动更新；不能自动更新的，及时打关键补丁和处理服务中断应该是常态，不是例外。
- **优先保护对外资产**。边缘设备和互联网服务最容易被打。这种管线可以打任何外部能看到的东西。
- **做攻击面缩减**。CSA 的原话："我们跑不过机器速度的威胁"。真正保持防御力的不是参与补丁赛跑，而是缩小互联网暴露面、网络分段和权限隔离、投资检测和响应。
- **别试图复制厂商的工作**。微软的 MDASH 系统编排了 100+ 个 agent 来挖掘 Windows CVE，单个安全团队比不过。在自己代码上做变种分析有回报，但别以为自己能跑赢上游厂商。

## 交付了什么

所有工具开源在 GitHub：

- PatchWatch: https://github.com/originsec/patchwatch
- Pocsmith: https://github.com/originsec/pocsmith
- hyperv-mcp: https://github.com/originsec/hyperv-mcp
- kd-mcp: https://github.com/originsec/kd-mcp

作者声明这些工具仅供研究用途，不对输出有效性做任何保证，一切需独立验证。

原文还提到了几个值得深入的方向：XBOW 和 Aisle 已经持续半年产出类似结果；Google Threat Intelligence Group 正在跟踪 AI 介入 kill chain 各阶段的增长趋势，并报告了首次在野使用 AI 开发的 zero day 尝试；@clearbluejar 的 CVE North Stars 系列和 @wumb0 的 Extracting and Diffing Windows Patches in 2020 是 patch diffing 领域绕不开的经典参考。
