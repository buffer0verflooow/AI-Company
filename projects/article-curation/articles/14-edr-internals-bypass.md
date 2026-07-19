---
title: EDR 内部架构与绕过技术全景：从内核回调到调用栈欺骗
cover: /home/media/workspace/company/projects/article-curation/assets/cover-14-edr-internals.jpg
---

> 原文: https://0xdbgman.github.io/posts/edr-internals-research-and-bypass/
> 作者: DebuggerMan（Red Teamer）
> 阅读时间: 约 15 分钟

## 一句话总结

DebuggerMan 发布了一篇 EDR 逆向工程与绕过技术的系统性研究，覆盖了从 Windows 内核回调、ETW 威胁情报、文件系统迷你过滤器到用户态 Hook 绕过、Sleep 混淆、调用栈欺骗和 BYOVD 的完整技术栈，并将每项技术与对应的绕过方法做了精确映射。

## 他们实际做了什么

这篇文章本质上是一份「EDR 攻防双向手册」——既拆解了现代 EDR 产品的内部组件架构，又为每个检测层提供了对应的规避手段。作者按照检测引擎管线的处理顺序组织内容：

**Part 1 — EDR 内部架构**：逐一分解了用户态服务、注入 DLL、内核驱动、文件系统迷你过滤器、ETW 消费者、WFP 网络过滤器等组件的具体功能和交互方式。

**Part 2 — 检测技术**：把内核回调（进程/线程/镜像加载/对象句柄/注册表）、用户态 API Hook、内存扫描器、ETW 遥测源映射到具体的检测规则模式。

**Part 3 — 绕过与规避**：按检测层分层给出绕过方案——静态分析规避 → IAT 隐藏 → 行为签名规避 → 用户态 Hook 绕过 → 内存扫描器规避 → Sleep 混淆 → 调用栈欺骗 → 残余内核遥测。

**Part 4 — 研究方法论**：提供了一个可复现的 8 阶段 EDR 逆向研究方法，从假设制定、实验环境搭建到能力深度逆向的完整流程。

## 具体发现了什么

### EDR 组件清单（跨厂商通用）

作者指出，尽管各家 EDR 产品命名不同（MDE 的 `MsSense.exe`、CrowdStrike 的 `CSFalconService.exe`、SentinelOne 的 `SentinelAgent.exe`），但架构角色是一致的：

| 组件 | 位置 | 功能 |
|---|---|---|
| 传感器服务 | `Program Files\<Vendor>\...` | 遥测关联、云端传输、策略执行 |
| 文件系统迷你过滤器 | `System32\drivers\...sys` | 检查文件 IRP，可选拦截 |
| 内核回调驱动 | 通常同一 .sys | 注册 Ps/Ob/Cm/镜像加载回调 |

此外还有三个非独立文件形态的子系统：ntdll 内联 Hook（注入 DLL 在进程初始化时安装）、ETW 提供程序订阅（通常 10-20 个提供程序，包括 `Microsoft-Windows-Threat-Intelligence`）、WFP 呼出驱动。

### Windows API 调用链与 Hook 位置

关键洞察：EDR 的 Hook 精确地位于 `ntdll.dll` 的 `Nt*` 存根函数处。完整调用链如下：

```
应用代码 → kernel32.dll → kernelbase.dll → ntdll.dll (← EDR Hook 在这里)
→ syscall 指令 → ntoskrnl.exe → FltMgr.sys → 迷你过滤器链 → NTFS.sys
```

### 内核回调：主要遥测获取机制

文章列出了五种内核回调及其典型检测规则：

- **进程创建回调**（`PsSetCreateProcessNotifyRoutineEx`）：Office 进程派生 cmd/powershell、命令行熵分析、base64 检测。系统范围硬限制 64 个回调槽位。
- **线程创建回调**（`PsSetCreateThreadNotifyRoutine`）：`CreateRemoteThread` 无论用户态 Hook 是否被绕过都会触发此回调。
- **镜像加载回调**（`PsSetLoadImageNotifyRoutine`）：无签名 DLL 加载到签名进程、从 `%TEMP%` 加载、DLL 侧加载检测。
- **对象句柄预操作回调**（`ObRegisterCallbacks`）：LSASS 句柄访问掩码剥离——`OpenProcess(PROCESS_ALL_ACCESS, lsass_pid)` 返回成功，但实际授予的权限被削减为 `PROCESS_QUERY_LIMITED_INFORMATION`。
- **注册表回调**（`CmRegisterCallbackEx`）：自动启动项写入、Windows Defender 配置篡改。

### ETW 威胁情报提供程序的关键特性

`Microsoft-Windows-Threat-Intelligence` 具有三种区分于标准 ETW 提供程序的架构特性：

1. 事件从内核镜像（`ntoskrnl.exe`）发出，用户态 `ntdll!EtwEventWrite` 补丁无效
2. 事件在内核操作完成后触发，用户态绕过无法阻止事件记录
3. 消费者进程必须标记为 PPL-Antimalware（`PsProtectedSignerAntimalware`）

### 绕过技术矩阵

文章将绕过技术按检测引擎管线遍历顺序组织：

**静态分析规避**：
- 符号和节重命名（每次构建生成全新指纹，消除静态签名）
- 编译时字符串/代码加密（AES-256-CTR，运行时解密到栈缓冲区，用后清零）

**IAT 隐藏**：
- 运行时 API 解析（仅导入 `LoadLibraryA` + `GetProcAddress`）
- API 哈希（FNV-1a 哈希比较，二进制中不含函数名字符串）

**系统调用绕过**（按演进顺序）：
- Hell's Gate → Halo's Gate → Tartarus' Gate → FreshyCalls → RecycledGate → SysWhispers4 → Sysplant → Acheron

直接系统调用和间接系统调用的关键区别：间接调用将 `syscall` 指令的执行点保留在 `ntdll.dll` 内，返回地址指向合法模块，满足栈遍历验证。

**Sleep 混淆（Sleep Mask）**：

| 技术 | 原语 | 仓库 |
|---|---|---|
| Ekko | 定时器队列 + `NtContinue` ROP 链 | Cracked5pider/Ekko |
| FOLIAGE | 单线程 APC 链 | 多个 PoC |
| Zilean | `WaitForSingleObject` + 线程上下文 | 多个 PoC |
| Cronos | Ekko + `PAGE_NOACCESS` 切换 | 多个 PoC |
| AceLdr | Ekko + 自定义 UDRL + 堆加密 | kyleavery/AceLdr |
| DreamWalkers | 栈欺骗 + Sleep Mask + 模块幽灵加载 | MaxDcb/DreamWalkers |

核心机制：Sleep 前加密内存区域并重置为只读/不可访问 → 等待 → 解密恢复。EDR 在睡眠窗口扫描时观察到的是加密、不可执行的字节。`VirtualProtect` 切换可执行状态会产生 ETW-TI 内存保护变更事件——这是 Sleep Mask 的主要检测信号。

**调用栈欺骗**：
- SilentMoonwalk（完全动态，不需要为每个 API 生成存根）
- VulcanRaven / LoudSunRun（构造伪造栈帧，返回地址指向合法文件支持代码区）
- DreamWalkers（Ekko + 动态栈欺骗 + 幽灵映射模块加载的结合体）

检测反制：验证栈底第三帧是否为线程注册起始函数的返回地址；检测指向 `MEM_PRIVATE` 区域的 RBP 链；验证 unwind-info 一致性。

**ETW-TI 和 AMSI 绕过**：
- 通过 `NtContinue` 安装硬件断点（不经过 `SetThreadContext`，避免产生 TI 事件）
- VEH 处理器在断点触发时合成干净扫描结果，不修改 `amsi.dll` 任何字节
- BYOVD → 修改 `EPROCESS->Protection` → PPL 提升 → ETW-TI 订阅

**BYOVD 2026 年现状**：
- Microsoft 漏洞驱动程序阻止列表（Win11 22H2+ 默认启用）
- HVCI（虚拟机监控程序强制代码完整性）使内核写入原语对受保护结构无效
- 可行路径：2015 年 7 月前签名的未列入阻止列表的驱动、低分发厂商驱动中新发现的漏洞

### 残余内核遥测

即使用户态绕过完全应用，以下内核遥测源仍然有效：

- 栈帧分析（返回地址在 `syscall` 入口处被检查）
- ETW-TI 事件（从内核上下文发出）
- 对象预操作回调（句柄访问掩码在内核层被削减，无法从用户态恢复）
- 镜像加载通知（每次节映射操作都产生 `IMAGE_INFO` 回调）
- Intel TDT / AMD 等效硬件遥测（CPU PMU 计数器和 LBR）

作者给出的 2026 年交战行动建议：「用户态绕过对任何资源充足的操作者而言已经是成熟的战术技能，但内核遥测并非轻易可消声。应假设残余内核遥测持续存在，优先考虑短驻留时间，在受监控系统上最小化持久化，并避免在关键基础设施上产生高置信度内核信号。」

## 他们公开了什么

- 完整的架构图：EDR 组件交互图、内核回调映射图、IRP 流程图、迷你过滤器栈图、ETW 架构图、WFP 架构图、系统调用流程图、研究阶段图——共 8 张技术插图
- 内核回调参考实现代码：进程生命周期回调、LSASS 句柄剥离、Sysmon LSASS 访问规则
- 迷你过滤器骨架注册代码
- WFP 过滤器安装代码（阻止特定可执行文件的出站流量）
- 控制流混淆、编译时加密、API 哈希的完整 C 代码示例
- 硬件断点 AMSI 绕过的完整 VEH 实现
- 所有提及工具和技术的 GitHub 仓库链接（Hell's Gate、FreshyCalls、SysWhispers4、SilentMoonwalk、DreamWalkers 等 20+ 个仓库）
- Sysmon 检测规则（LSASS 句柄、WMI 事件订阅）
- 8 阶段 EDR 研究方法论（假设制定 → 实验环境构建 → 线索收集 → 观测仪器化 → 能力深挖 → 假设验证 → 记录 → 迭代）
- 工具基线清单：Procmon、WinDbg、Frida、Ghidra、Volatility 3、mitmproxy 等 20+ 工具
- MDE 组件映射表（`MsSense.exe` / `MsSecFlt.sys` / `MsMpEng.exe` / `WdFilter.sys`）

## 结论

DebuggerMan 的这项研究是对现代 EDR 攻防状态的一次全面快照。文章的核心观点是：**EDR 的检测能力取决于它订阅了哪些信号以及如何关联这些信号，而非取决于架构本身。** 绕过不是在单个层面完成的——它是对整个检测管线（静态 → ML → 动态 → 启发式 → 行为 ML）的分层攻击。即使最完整的用户态绕过栈被应用，内核遥测表面仍然存在，交战行动应据此设计。

对于防守方，这篇文章提供了理解自家 EDR 内部运作的精确参考；对于进攻方，它是按检测层索引的绕过技术目录。两者之间的差距正在缩小，因为双方都在研究同一套内核 API 和遥测源。
