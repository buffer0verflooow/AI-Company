---
title: 0xMatheuZ 全站技术纵览：从用户态 Hook 到内核 EDR 对抗的完整攻击链
cover: /home/media/workspace/company/projects/article-curation/assets/cover-12-matheuz-tech-overview.jpg
---

> 来源: https://matheuzsecurity.github.io/

> 作者: 0xMatheuZ（安全研究员，Singularity & RingReaper 作者，Rootkit Researchers Discord 社区运营者）

> 研究周期: 2024-02 至 2026-07（共 13 篇文章）

> 阅读时间: 约 15 分钟

## 一句话总结

0xMatheuZ 在两年半的时间里发表了 13 篇技术研究，构建了一条从用户态 LD_PRELOAD 欺骗到内核态 eBPF/EDR 对抗的完整技术栈。这些文章不是孤立的知识点——它们串联起来就是一部 Linux 攻击性安全研究的路线图：从最基本的进程隐藏，到绕过现代 EDR 的多层检测体系。

---

## 技术全景图

按技术层级从低到高排列 13 篇文章：

```
┌─────────────────────────────────────────────────────────┐
│  Layer 4: EDR 架构对抗                                   │
│  #1 BPF Map Poisoning (2026-07)                          │
│  #3 Breaking eBPF Security (2026-02)                     │
│  #2 Trend Micro Agent Bypass (2026-06)                   │
│  #5 Evading Elastic Security (2025-10)                   │
├─────────────────────────────────────────────────────────┤
│  Layer 3: 高级免杀与传输通道                              │
│  #6 io_uring EDR Evasion (2025-07)                       │
│  #4 Ioctl Secrets CTF Writeup (2025-11)                  │
├─────────────────────────────────────────────────────────┤
│  Layer 2: Rootkit 机制深度分析                            │
│  #7 Breaking LD_PRELOAD Hooks (2025-06)                  │
│  #8 Bypassing LD_PRELOAD Rootkits (2025-05)              │
│  #9 ElfDoor-gcc (2025-04)                                │
│  #10 Detecting ftrace Rootkits (2024-12)                 │
│  #11 Detecting LD_PRELOAD Rootkits (2024-11)             │
│  #12 Removing LKM Rootkit KoviD (2024-08)                │
├─────────────────────────────────────────────────────────┤
│  Layer 1: 持久化基础                                     │
│  #13 Linux Threat Hunting Persistence (2024-02)          │
└─────────────────────────────────────────────────────────┘
```

---

## Layer 1 · 持久化基础（1 篇）

### #13 Linux Threat Hunting Persistence（2024-02-16）

0xMatheuZ 的博客起点是一篇攻防两侧都用得上的"持久化全地图"。文章覆盖了 Linux 上 **14 种持久化技术**：

| 技术 | 检测要点 |
|------|---------|
| SSH 公钥植入 | 扫描 `/home/*/.ssh/authorized_keys` |
| Crontab 定时任务 | 检查所有用户的 crontab 和 `/etc/cron.*` |
| `.bashrc` 后门 | 检查 shell 配置文件中异常的 `exec` / `nc` 调用 |
| APT 包劫持 | 检查 `/etc/apt/apt.conf.d/` 中的异常配置 |
| SUID bash | `find / -perm -4000` 查找异常 SUID 二进制 |
| 恶意 Systemd 服务 | 检查 `/etc/systemd/system/` 中非常规服务文件 |
| LKM Rootkit | `lsmod` + 模块签名验证 |
| LD_PRELOAD Rootkit | 检查 `/etc/ld.so.preload` 和环境变量 |
| PAM 后门 | 审计 `/etc/pam.d/` 配置变更 |
| ACL 持久化 | `getfacl` 检查异常 ACL 条目 |
| init.d 脚本 | `/etc/init.d/` 中未知的启动脚本 |
| MOTD 注入 | `/etc/update-motd.d/` 中的恶意脚本 |
| Mount 进程隐藏 | `mount --bind` 隐藏 PID |
| rc.local | `/etc/rc.local` 异常命令 |

检测手段全部使用 Linux 自带工具（`find`、`grep`、`lsmod`、`getfacl` 等），不依赖商业产品。

---

## Layer 2 · Rootkit 机制深度分析（6 篇）

这 6 篇文章构成了一个完整的 rootkit 检测与对抗知识体系：

### #8 Bypassing LD_PRELOAD Rootkits Is Easy（2025-05-14）

拆解了 `LD_PRELOAD` rootkit 的工作原理：通过劫持 libc 函数（如 `fopen`、`readdir`）来隐藏文件/进程/网络连接。然后展示了绕过方法——**直接调用 syscall** 绕过 libc，或者使用静态编译的 busybox，因为 LD_PRELOAD 只能劫持动态链接的 libc 函数。

### #7 Breaking LD_PRELOAD Rootkit Hooks（2025-06-21）

利用 **io_uring** 直接提交 I/O 操作到内核，完全绕过 libc 层——因此也绕过了所有基于 LD_PRELOAD 的用户态 hook。这是一个关键的技术跨越：从"绕过 rootkit hooks"升级到了"使用现代内核接口重新定义 I/O 路径"。

### #9 ElfDoor-gcc（2025-04-13）

探讨了 GCC 编译层面的攻击技术，包括 ELF 二进制修改和后门植入。

### #10 Detecting ftrace Rootkits（2024-12-26）

从防御视角分析了基于 ftrace hooking 的 rootkit 检测方法。ftrace 是内核函数追踪器，攻击者利用它来 hook 内核函数而不修改内核代码。文章详细介绍了 ftrace 的工作原理和检测思路。

### #11 Detecting LD_PRELOAD Rootkits from ldd & /proc（2024-11-26）

展示了如何通过 `ldd`、`/proc/pid/maps` 和 `/proc/pid/environ` 检测 LD_PRELOAD rootkit。攻击侧则展示了如何隐藏这些痕迹——包括从 ldd 输出和 /proc 中抹去注入的证据。

### #12 Removing LKM Rootkit KoviD（2024-08-26）

一篇针对 KoviD rootkit 的检测与清除实操指南，涵盖 `lsmod` 异常检测、模块签名验证、以及强制卸载被隐藏的内核模块的方法。

---

## Layer 3 · 高级免杀与传输通道（2 篇）

### #6 Red Team Tactics: Evading EDR on Linux with io_uring（2025-07-04）

这是 0xMatheuZ 的另一条技术主线：**利用 io_uring 进行 EDR 免杀**。配套开源了 [RingReaper](https://github.com/MatheuZSecurity/RingReaper) 工具。核心思想：

- io_uring 是现代 Linux 的高性能异步 I/O 接口，直接从用户空间向内核提交 I/O 请求
- 传统 EDR 通过 libc hook、fanotify、auditd 等层面监控，io_uring 绕过了这些监控点
- 文章实现了 Python C2 服务端，通过 io_uring 通道传输数据，绕过基于 syscall tracepoint 的 EDR

### #4 Ioctl Secrets CTF Writeup（2025-11-09）

一个内核模块漏洞利用的 CTF 题解。给定一个 `/dev/ioctl_dev` 字符设备和隐藏的内核模块，需要通过逆向和内核利用获取 flag。展示了 ioctl 接口的漏洞挖掘思路。

---

## Layer 4 · EDR 架构对抗（4 篇）

这是整个博客的技术制高点——从针对特定产品到揭示架构性缺陷。

### #5 Evading Elastic Security: Linux Rootkit Detection Bypass（2025-10-30）

一个完整的 Elastic Security 免杀案例研究：

**发现**：在默认 Elastic agent 环境中，编译并加载 Singularity rootkit 会触发 **约 26 个告警**，内核对象在加载前即被删除并隔离到 quarantine。

**对抗策略**：识别出 6 条具体 YARA 规则后，通过以下方式逐一绕过：

| YARA 规则 | 检测模式 | 绕过方法 |
|-----------|---------|---------|
| `Linux_Rootkit_Generic_61229bdf` | 57 个常见 rootkit 函数名 | 函数名混淆、字符串加密 |
| `Linux_Rootkit_Generic_482bca48` | 可疑前缀和 hook 模式 | 修改符号命名约定 |
| `Linux_Rootkit_Generic_d0c5cfe0` | 初始化和 hooking 组合 | 分阶段执行 |
| `Linux_Rootkit_Generic_f07bcabe` | ftrace helper 函数 | 内联展开 |
| `Linux_Rootkit_Generic_5d17781b` | license 字符串 + kallsyms 组合 | license 字符串混淆 |
| `Linux_Rootkit_BrokePKG_7b7d4581` | 已知 rootkit 特征模式 | 代码结构重组 |

### #2 Trend Micro Deep Security Agent Bypass（2026-06-03）

一篇非常特殊的"压力诱发型"绕过研究：

**核心发现**：
```
本地非特权进程的文件系统/进程事件风暴可以触发 Trend Micro
Deep Security Agent 对 bmhook 和 tmhook 执行 rmmod。
这会创建一个可重复的临时窗口，行为监控缺失或降级。
```

**证据链**：
1. 制造大量文件写入、截断、重命名、符号链接操作和进程 fork/exit
2. `dmesg` 中观察到 livepatch unpatch → tmhook unloaded → repatch 完整序列
3. 确认是 agent 自身调用 `rmmod`，而非模块崩溃
4. 在该窗口内，一个原本被 Trend Micro 阻止的 payload 成功写入磁盘

**关键区别**：这不是漏洞利用、不是远程代码执行、不是永久 kill switch——而是一个**可被非特权进程触发的、临时但可重复的安全控制缺口**。

### #3 Breaking eBPF Security（2026-02-09）

[详见单项分析文章]

### #1 BPF Map Poisoning（2026-07-06）

[详见单项分析文章]

---

## 技术演进路线

从 2024 年 2 月到 2026 年 7 月的 29 个月间，0xMatheuZ 的研究轨迹清晰可见：

```
2024-02  #13 持久化基础（防守视角入门）
    ↓
2024-08  #12 LKM rootkit 清理
2024-11  #11 LD_PRELOAD 检测与隐藏
2024-12  #10 ftrace rootkit 检测
    ↓ （从防守转向攻击）
2025-04  #9  ELF/GCC 技术
2025-05  #8  LD_PRELOAD 绕过
2025-06  #7  io_uring 绕过 LD_PRELOAD hooks
2025-07  #6  io_uring 绕过 EDR（RingReaper 发布）
    ↓ （攻击升级到企业级 EDR）
2025-10  #5  Elastic Security 26 告警全绕过
2025-11  #4  CTF 内核利用
    ↓ （攻击从表面深入架构）
2026-02  #3  eBPF 管道系统性破坏
2026-06  #2  Trend Micro 压力诱发绕过
2026-07  #1  BPF Map 投毒（最新，最优雅）
```

**关键转折点**：2025 年中从用户态 rootkit 分析转向企业 EDR 对抗，2026 年初从针对特定产品转向揭示架构性缺陷。

---

## 核心洞察

通读全部 13 篇文章后，可以提炼出三条贯穿始终的研究主线：

### 1. EDR 的"检测-配置共享边界"是致命弱点

从 BPF Map Poisoning（#1）到 eBPF 管道破坏（#3）再到 Trend Micro 压力绕过（#2），反复证明这一点：**当安全工具的检测逻辑和配置状态共享同一个访问控制边界时，攻击者不需要破坏检测逻辑本身，只需要操控配置状态。** BPF maps、ring buffer、perf buffer——这些是 EDR 的内部通信机制，却被设计成可通过标准 API 从外部访问。

### 2. io_uring 是新战场

三篇文章（#6 #7 #8）都涉及 io_uring——这不是巧合。io_uring 代表的是一种绕过传统监控面的范式：
- 传统 I/O 路径：用户态 libc → syscall → kernel → EDR hook
- io_uring 路径：用户态共享内存 → 内核轮询 → I/O 完成

EDR 在中间完全没有拦截点。RingReaper 是这个思路的工程化实现。

### 3. 内核级对抗的终极结论：可信计算是唯一出路

#3 文章最核心的结论值得单独强调：

> Once the kernel is hostile, observability is best-effort.

当内核被控制后，所有运行在内核中的安全工具都不可信。eBPF 在可信内核下提高了安全门槛，但不能成为对抗恶意内核的最后防线。防御必须进入更底层：Secure Boot、签名模块、硬件信任根、远程证明、网络层检测。

---

## 开源产出

| 项目 | 用途 | GitHub |
|------|------|--------|
| **Singularity** | 隐蔽内核 rootkit（研究用） | github.com/MatheuZSecurity/Singularity |
| **RingReaper** | io_uring EDR 免杀工具 | github.com/MatheuZSecurity/RingReaper |
| **falco_blind.c** | BPF Map 投毒 PoC | 随 #1 文章发布 |
| **Rootkit Researchers** | Discord 社区 | discord.gg/66N5ZQppU7 |

---

💡 如果这篇文章对你有帮助，欢迎 **点赞 · 在看 · 分享**

🔗 博客首页：[matheuzsecurity.github.io](https://matheuzsecurity.github.io/)
