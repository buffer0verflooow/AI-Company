---
title: "P³：用进程启动参数投毒，四款 EDR 无一告警"
cover: /home/media/workspace/company/projects/article-curation/assets/cover-13-process-parameter-poisoning.jpg
---

> 原文: https://sensepost.com/blog/2026/process-parameter-poisoning/
> 作者: Max Hirschberger & Ogulcan Ugur（SensePost / Orange Cyberdefense）
> 阅读时间: 约 15 分钟

## 一句话总结

SensePost 团队发现一种新的进程注入手法——利用 `CreateProcessW` 的三个启动参数（命令行、环境变量、ShellInfo）传递恶意代码，全程不使用 `WriteProcessMemory` 和 `VirtualAllocEx` 这两个 EDR 重点监控的 API，在 4 款头部 EDR 上成功注入 shellcode 且零告警。

## 为什么选这篇

2025-2026 年，进程注入绕过技术的攻防进入了"API 调用模式匹配"的军备竞赛阶段：

- EDR 在 `NtAllocateVirtualMemory`、`NtWriteVirtualMemory`、`NtCreateThreadEx` 等"注入三件套"上挂了密集的 hook 和 ETW 事件
- 攻击者转而使用直接 syscall、D/Invoke、Native API 解析、HWBP 绕过等"底层操作"
- 双方都在围绕同一组监控 API 博弈

P³（Process Parameter Poisoning）打破了这一格局：它**根本不调用**那组被监控的 API。恶意代码通过进程创建的合法路径进入目标进程——参数本身就是载荷。这种思路的转变对于理解 EDR 检测盲区至关重要，也反映了 2026 年进程注入技术的演进方向：从"如何绕过监控"到"如何不在监控雷达上出现"。

## 核心观点

1. **Windows 进程创建本身就是一种内存写入机制**——`CreateProcessW` 在创建新进程时，会自动将 lpCommandLine、lpEnvironment、lpStartupInfo.lpReserved 三个参数从父进程拷贝到子进程的 PEB 中，这个过程不经过任何标记为"可疑"的 API
2. **定位数据不走"写"路线**——通过 `NtQueryInformationProcess` → `NtReadVirtualMemoryEx` 的只读链获取 PEB 指针和 `ProcessParameters` 结构体，全程只读不写
3. **执行跳转绕过 CreateRemoteThread**——使用 `NtSetContextThread` 修改主线程 RIP，新进程创建后 `CreateProcessW` 已自动提供主线程有效句柄
4. **没有挂起进程、没有暂停线程**——绕过 EDR 检测进程挖空（Process Hollowing）和注入攻击的已知指标

## 深度解读

### P³ 的全链路拆解

P³ 的攻击流程分为三个阶段，每个阶段都刻意避开了 EDR 的监控雷达：

**阶段一：数据投毒（通过 CreateProcessW 间接写入）**

传统注入器需要三步：`VirtualAllocEx` 分配远程内存 → `WriteProcessMemory` 写入 shellcode → `VirtualProtectEx` 设置执行权限。P³ 完全绕开这一链路。

`CreateProcessW` 接受三个"可注入"参数：

| CreateProcessW 参数 | PEB 中的对应字段 | 最大容量 | 传递方式 |
|---------------------|-------------------|---------|---------|
| `lpStartupInfo.lpReserved` | `ShellInfo`（UNICODE_STRING） | 未明确限制 | 通过 STARTUPINFOW 结构体 |
| `lpEnvironment` | `Environment`（PVOID） | 实际无限制 | 以 `NAME=VALUE\0\0` 格式传递，通过 `CREATE_UNICODE_ENVIRONMENT` 标志启用 |
| `lpCommandLine` | `CommandLine`（UNICODE_STRING） | 32,767 个 Unicode 字符 | 最直观，但内容会显示在进程属性中 |

当调用 `CreateProcessW(cmd.exe, shellcode_as_commandline, ...)` 时，Windows 内核会将命令行字符串（包含 shellcode）拷贝到新进程中 `RTL_USER_PROCESS_PARAMETERS.CommandLine` 字段所在的内存页。**这是内核执行的合法拷贝，不是注入器发起的"远程写入"**——EDR 无法通过监控 `NtWriteVirtualMemory` 发现这一步。

**阶段二：载荷定位（只读链）**

通过三条只读操作找到投毒数据的位置：

```
NtQueryInformationProcess(ProcessBasicInformation) → 获取 PebBaseAddress
  ↓
NtReadVirtualMemoryEx(PebBaseAddress) → 读取完整 PEB 结构体
  ↓
PEB.ProcessParameters 中的字段 → 定位 ShellInfo / Environment / CommandLine 的实际内存地址
  ↓
NtReadVirtualMemoryEx(ShellInfo.Buffer) → 读取载荷内容
```

关键洞察：`NtReadVirtualMemoryEx` 是只读操作，EDR 对其的监控远远弱于写操作。而且 P³ 的定位过程中不需要调用任何"打开远程进程句柄"以外的可疑 API——`CreateProcessW` 已经提供了合法句柄。

SensePost 团队在测试中尝试了 Dirty Vanity（`RtlCreateProcessReflection`）方案——利用 Windows 内置的进程分叉机制。但逆向发现 `RtlCreateProcessReflection` 内部调用了 `NtWriteVirtualMemory` 和 `NtCreateThreadEx`——这两个是 EDR 重点监控的 API，所以最终弃用该方案。

**阶段三：执行跳转（NtSetContextThread 替换 RIP）**

P³ 选择的执行跳转路径是线程上下文修改：

```cpp
// P³ 的 ThreadSetExec 简化实现
CONTEXT ctx = { 0 };
ctx.ContextFlags = CONTEXT_CONTROL;  // 只修改控制寄存器（包括 RIP）

NtGetContextThread(hThread, &ctx);   // 获取当前上下文
ctx.Rip = (DWORD64)shellcode_addr;   // 将指令指针指向 shellcode
NtSetContextThread(hThread, &ctx);   // 写入新上下文
```

为什么这比 Dirty Vanity 好？

| 维度 | Dirty Vanity (RtlCreateProcessReflection) | P³ 线程上下文修改 |
|------|------------------------------------------|-------------------|
| 涉及的写 API | 内部调用 NtWriteVirtualMemory | 无写 API |
| 线程创建 | 内部调用 NtCreateThreadEx | 无——使用主线程 |
| 挂起 | 需要挂起被分叉进程 | 不需要（`NtSetContextThread` 无需挂起） |
| EDR 告警面 | 大——间接调用了监控中的所有 API | 小——仅用了线程管理 API |

### null 字节问题的巧妙解决

由于启动参数字符串遇到 `\x00` 截断，任意 shellcode 不能直接传输。SensePost 团队的 `ShellCodeWriter` 类实现了一个精巧的方案：

**核心原语：XOR 生成无零字节的任意值。**

```asm
; 生成 RAX = 0xDEADBEEF（不含零字节）
mov rax, 0x01010101DFACBFEE  ; XOR_A
mov r15, 0x0101010101010101  ; XOR_B
xor rax, r15                 ; rax = 0xDEADBEEF
```

两个操作数都确保不含零字节，而 XOR 结果可以是任意值（包括零）。以此为基础，生成器可以：
- `PushValue`：将任意 64 位值 push 到栈上
- `PushBuffer`：将任意长度的 shellcode 按 64 位反向压栈
- `SetArgRegister`：设置 x64 函数调用的前 4 个参数寄存器
- `Call`：生成 16 字节对齐的 call 指令
- `LoadAndCallShellCode`：将含零字节的 shellcode 写入栈 → `VirtualAlloc` 分配 RW 内存 → 手动 memcpy → `VirtualProtect` 改为 RX → jmp 执行

其中 `SetRAX(0)` 直接被编译为 `xor rax, rax`（两个字节且无零），是所有操作的基元。

### WinApiResolver：运行时 API 解析规避静态检测

P³ 还实现了一个 `WinApiResolver` 类，在运行时通过 `GetModuleHandle` + `GetProcAddress` 解析所有 Native API 的地址。这样做有几个好处：

1. 避免在 PE 导入表（IAT）中留下敏感 API 的痕迹（如 `NtSetContextThread`）
2. 对抗 EDR 的 import 地址表 hook——运行时解析的 API 地址来自 `ntdll.dll` 的导出表，而非 IAT，可能绕过某些基于 IAT hook 的检测
3. 灵活应对不同 Windows 版本间的 API 地址变化

## 我的思考

### 1. "没有新 API，只有新组合"——P³ 的方法论启示

P³ 中没有使用任何一个新的 Windows API。`CreateProcessW`、`NtQueryInformationProcess`、`NtReadVirtualMemoryEx`、`NtSetContextThread` 都是存在了多年的 API。创新点在于**组合方式**：将进程创建参数作为恶意代码的传输通道。

这让我想起 2017 年 AtomBombing 和 2020 年 modexp 的 `lpReserved` 注入——安全研究的规律是，每过几年就会有人从不同角度重新审视同一组 API，发现新的组合可能性。对于红队和检测工程来说，这是个重要启示：**定期回顾已知 API 的"非预期用途"是发现新攻击面的有效方法**。

### 2. 检测面从"写 API"转向"上下文组合"

SensePost 团队坦诚地给出了检测方向——说明他们也意识到了这个问题。最有效的检测面不是单个可疑 API，而是 API 调用序列的上下文组合：

- `VirtualProtectEx + SetThreadContext` 组合——将内存改为可执行后立即修改指令指针
- 对进程参数所在页面执行 `VirtualProtectEx`——正常场景下不应将命令行/环境变量所在页改为可执行
- 参数熵值异常——但单独依赖熵检测误报率会很高，需要结合其他信号

这也验证了 2026 年 EDR 检测的行业趋势：**从单点 API hook 转向行为序列分析和跨信号关联**。

### 3. P³ 的局限性——不是万能银弹

值得指出的是，P³ 并非在所有场景下都能工作：

- **需要新建进程**——不能注入已有进程（如 explorer.exe、lsass.exe），对于需要操纵已有进程的攻击场景不适用
- **Shellcode 大小受限**——CommandLine 最多 32,767 字符，Environment 和 ShellInfo 无明确限制但过大参数本身是异常信号
- **NtSetContextThread 也已被部分 EDR 监控**——虽然监控强度低于 CreateRemoteThread，但一些新一代 EDR 已经开始追查 SetThreadContext 的异常使用
- **需要预先了解 map ID**（针对 BPF Map Poisoning 的类比）——P³ 也需要知道目标系统的 EDR 行为模式才能最大化成功率

## 延伸阅读

1. **[Orange-Cyberdefense/p3-loader](https://github.com/Orange-Cyberdefense/p3-loader)** / P³ 的完整开源实现，包含注入器主体、`ShellCodeWriter` 生成器类、`WinApiResolver` API 解析器和 VS 项目文件
2. **modexp 的 `lpReserved` 注入（2020）** / [Wayback Machine 存档](https://web.archive.org/web/20241211190548/https://modexp.wordpress.com/2020/07/31/wpi-cmdline-envar/) / modexp 在两年前提出了使用 `lpReserved` 传递 shellcode 的原初概念，P³ 在此基础上扩展为三条注入路径并增加了完整的 null-free shellcode 生成器
3. **[A tale of EDR bypass methods](https://s3cur3th1ssh1t.github.io/A-tale-of-EDR-bypass-methods/)** / 一篇经典的 EDR 绕过技术综述，覆盖直接 syscall、API unhooking、硬件断点绕过等方法，有助于将 P³ 放在更广阔的 EDR 绕过技术谱系中理解
4. **[EDR Bypass Techniques 2026 — What Microsoft Actually Killed and What Still Works](https://ringsafe.in/edr-bypass-techniques-2026-what-microsoft-actually-killed-and-what-still-works/)** / 2026 年 EDR 绕过技术的市场概况，帮助判断 P³ 在当前攻防格局中的定位
5. **[Windows Process Injection 技术全览（Elastic Security）](https://www.elastic.co/security-labs/process-injection-journey)** / Elastic Security 实验室的系统性梳理，将 P³ 归入"Process Parameter Manipulation"类别

---

💡 如果这篇文章对你有帮助，欢迎 **点赞 · 在看 · 分享**

🔗 原文：[sensepost.com](https://sensepost.com/blog/2026/process-parameter-poisoning/)
