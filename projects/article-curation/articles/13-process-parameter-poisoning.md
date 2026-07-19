---
title: "P³：用进程启动参数投毒，四款 EDR 无一告警"
cover: /home/media/workspace/company/projects/article-curation/assets/cover-13-process-parameter-poisoning.jpg
---

> 原文: https://sensepost.com/blog/2026/process-parameter-poisoning/
> 作者: Max Hirschberger & Ogulcan Ugur（SensePost / Orange Cyberdefense）
> 阅读时间: 约 10 分钟

## 一句话总结

SensePost 团队发现一种新的进程注入手法——利用 `CreateProcessW` 的三个启动参数（命令行、环境变量、ShellInfo）传递恶意代码，全程不使用 `WriteProcessMemory` 和 `VirtualAllocEx`，在 4 款头部 EDR 上成功注入 shellcode 且零告警。

## 他们实际做了什么

传统进程注入依赖 `VirtualAllocEx` / `NtAllocateVirtualMemory` 分配内存，再用 `WriteProcessMemory` / `NtWriteVirtualMemory` 写入 shellcode。EDR 重点监控的正是这两组 API。

SensePost 团队换了个思路：**Windows 在创建新进程时，启动参数本身就会从父进程拷贝到子进程的 PEB 结构里**。为什么不把恶意代码藏在启动参数里？

他们实现了一个完整的注入器，选了三条"投毒"路径：

| 注入途径 | CreateProcessW 参数 | PEB 中的对应字段 |
|----------|---------------------|-------------------|
| ShellInfo | `lpStartupInfo.lpReserved` | `RTL_USER_PROCESS_PARAMETERS.ShellInfo` |
| Environment | `lpEnvironment` | `RTL_USER_PROCESS_PARAMETERS.Environment` |
| CommandLine | `lpCommandLine` | `RTL_USER_PROCESS_PARAMETERS.CommandLine` |

注入器（`CreateProcessWithPoison`）让使用者选择投毒参数和目标程序，然后调用 `CreateProcessW` 启动合法进程。恶意代码随参数一起进入了目标进程的 PEB。

**定位投毒数据**的步骤也绕开了敏感 API：通过 `NtQueryInformationProcess` 获取 PEB 地址 → `NtReadVirtualMemoryEx` 读取 PEB → 再读一次取出 `ProcessParameters` 中的指针——全程只读不写。

**执行 shellcode** 用的是 `NtSetContextThread` 修改主线程的 RIP 指令指针，而不是 `CreateRemoteThread`。由于 `CreateProcessW` 已经返回了主线程的有效句柄，无需额外打开线程。

团队测试了 Dirty Vanity（`RtlCreateProcessReflection` 进程分叉）方案，但发现它内部调用了 `NtWriteVirtualMemory` 和 `NtCreateThreadEx`——反而引入不必要的可疑行为，最终弃用。

## 具体发现了什么 / 能分析出什么

### 与传统注入手法的对比

| 维度 | 传统注入 | P³ |
|------|----------|-----|
| 内存分配 | 需要 VirtualAllocEx | 不需要显式分配（参数自带内存） |
| 内存写入 | 需要 WriteProcessMemory | 通过 CreateProcessW 间接写入 |
| 执行跳转 | CreateRemoteThread 或 APC | SetThreadContext |
| EDR 监控面 | 多个高危 API 被监控 | 仅依赖合法的进程创建和线程管理 API |

**核心优势：不创建挂起进程，不暂停任何线程。** 挂起进程和反复调用 `SuspendThread` 是 EDR 检测进程挖空（Process Hollowing）和注入攻击的已知指标。

### 规避 null 字节的 shellcode 生成器

由于启动参数字符串遇到 `\x00` 截断，任意 shellcode 无法直接传输。团队写了一个 `ShellCodeWriter` 类，用 XOR 原语消除 null 字节：

- `SetRAX`：将任意 64 位值拆成两个无零字节的值 XOR 得到，存入 RAX
- `PushBuffer`：把 shellcode 按 64 位分块反向压栈（栈向下增长）
- `LoadAndCallShellCode`：将任意含零字节的 shellcode 写到栈上 → 调 `VirtualAlloc` 分配 RW 内存 → 手动 memcpy 过去 → `VirtualProtect` 改成 RX → `jmp` 执行

`SetRAX(0)` 直接用 `xor rax, rax`（本身就是两个字节且无零），`SetRAX(0xDEADBEEF)` 则生成两个无零常数的 XOR。

### 四种 payload 选项

注入器内置四种 payload：
1. **弹窗 demo**：最简单的 MessageBox 验证
2. **十六进制 shellcode**：直接提供 hex 字符串注入
3. **DLL 加载**：通过 `LoadLibraryA` 加载指定 DLL
4. **HTTP(S) shellcode**：从远程 URL 拉取 shellcode，自动处理 null 字节

## 他们公开了什么

**GitHub 仓库**: [Orange-Cyberdefense/p3-loader](https://github.com/Orange-Cyberdefense/p3-loader)

包含完整的 C++ 实现：注入器主体、`ShellCodeWriter` 生成器类、`WinApiResolver` API 解析器。提供 VS 项目文件，可直接编译运行。

此外，modexp 在 2020 年的一篇已删除博客（Wayback Machine 有存档）中提出了相同原语——用 `lpReserved` 传递 shellcode。P³ 在此基础上扩展为三条注入路径并增加了完整的 null-free shellcode 生成器。

## 检测思路

文章也坦诚给出了检测方向：

- **VirtualProtectEx + SetThreadContext 组合**：将某内存区域改为可执行后，立即调用 `SetThreadContext` 修改指令指针（且至少包含 `CONTEXT_CONTROL` 标志）
- **对进程参数所在页执行 VirtualProtectEx**：正常情况下不应将命令行/环境变量的内存改为可执行
- **参数熵值异常**：命令行的熵接近 shellcode 或远偏离正常命令行（但仅依赖熵检测误报率高）
- **远程读取其他进程的 ProcessParameters 结构**：P³ 定位数据需要通过 PEB 指针读取远程进程参数

SensePost 的结论：其托管检测服务已能覆盖这类手法的检测指标，但单一 EDR 产品的内置规则明显不够——攻击者只需小幅修改就能绕过现有方案。
