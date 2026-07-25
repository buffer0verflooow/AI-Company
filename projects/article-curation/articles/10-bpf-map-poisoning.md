---
title: BPF 映射投毒：从内部击穿 EDR 的监控体系
cover: /home/media/workspace/company/projects/article-curation/assets/cover-10-bpf-map-poisoning.jpg
---

> 原文: https://matheuzsecurity.github.io/hacking/bpf-map-poisoning-edr-evasion/
>
> 作者: 0xMatheuZ（安全研究员，Singularity Rootkit 作者）
>
> 阅读时间: 约 12 分钟

## 一句话总结

大多数 EDR 绕过技术靠"躲"——使用匿名 mmap、直接调用 syscall、memfd 执行。而 BPF Map Poisoning 反其道而行：直接走进 EDR 内部，重写它的内存里的监控配置。不是绕过检测，而是让检测本身失明。

## 为什么选这篇

eBPF（extended Berkeley Packet Filter）作为 Linux 内核可观测性的事实标准，已经被 Falco、Tracee、Tetragon、CrowdStrike Falcon、Elastic Defend 等安全产品广泛采用。其核心卖点是"在内核中监控，用户空间无法干预"——但这个隐含假设正在被打破。

0xMatheuZ 展示的攻击极其精简（约 150 行 C 代码），却直击 eBPF 安全工具最根本的架构缺陷：**检测逻辑和它要保护的数据共享了同一套访问控制边界**。对于正在使用或评估 eBPF 安全方案的团队，这篇文章是一记清醒的警钟——技术选型时应该把它纳入风险模型，而不是假设"eBPF 本身是安全的"。

## 核心观点

1. **BPF map 是 eBPF 安全工具的单点故障**——几乎所有 eBPF 安全工具都将监控状态（哪些 syscall 要跟踪、哪些 PID 受信任、哪些路径要告警）存储在 BPF maps 中，而 BPF maps 通过 `bpf(2)` syscall 可直接写入
2. **攻击者不需要内核漏洞**——`BPF_MAP_UPDATE_ELEM` 是内核的一等 API，不是内存损坏、不是 hook 旁路，而是修改 BPF map 的标准文档化接口
3. **攻防不平等的核心在于归属校验缺失**——安全工具的 eBPF 程序无法区分"合法写入"（工具自身 agent 更新配置）和"恶意写入"（攻击者清零监控规则），因为 BPF API 层面没有写入者身份校验
4. **恢复能力强**——攻击者可以在 payload 执行完毕后将原始值写回，消除运行时痕迹，让事后排查极其困难

## 深度解读

### BPF Maps：藏在 eBPF 安全工具心脏的开关

理解 BPF Map Poisoning 的前提是理解 BPF maps 在 eBPF 安全工具中的角色。eBPF 程序本身是"无状态"的——它不能保存全局变量，不能分配内核内存。所有跨事件的状态共享、配置参数、运行时数据都存储在 BPF maps 中。

对于 Falco 这样的安全工具，典型的 BPF map 布局包括：

| BPF Map 类型 | 用途 | 攻击价值 |
|-------------|------|---------|
| `interesting_sys`（数组型） | 512 个条目，每个索引对应一个 syscall 号，值 1=监控，0=跳过 | ⭐ 最高——关掉它整个 syscall 类别的监控就失效了 |
| `trusted_pids`（哈希表） | 白名单 PID 列表，匹配的进程不产生告警 | ⭐⭐⭐ 将自己的进程加入白名单 |
| `syscall_exit_ta`（数组型） | 出口追踪配置 | ⭐⭐ 辅助盲区覆盖 |

攻击的核心杠杆是 `interesting_sys`。这是一个数组型 BPF map，有 512 个条目（0-511，恰好覆盖所有 x86_64 syscall 号），初始值全为 1。Falco 的 eBPF 程序在每次 syscall 触发时，第一件事就是查这个数组——如果值为 0，直接返回，不生成任何事件，不记录任何日志。

**代码层面就是这么简单**（伪代码）：

```c
// Falco eBPF probe 的核心逻辑（简化）
int interesting = bpf_map_lookup_elem(&interesting_sys, &syscall_nr);
if (!interesting) return 0;  // 什么也不做
// ... 继续监控逻辑
```

攻击者只需要执行一个 `bpf(BPF_MAP_UPDATE_ELEM, interesting_sys_fd, &syscall_nr, &zero_value)`——然后这个 syscall 在整个系统范围内就从 Falco 的视野中消失了。

### 三种攻击变体的战术对比

文章中提到了两种主要变体，但值得进一步细化：

**变体 A：Syscall 监控抑制（文章重点）**
- 操作：将 `interesting_sys` 中特定 syscall 条目清零
- 目标：execve(59)、execveat(322)、openat(257)、open(2)、connect(42)、socket(41)、read(0)、write(1)
- 效果：整个系统上所有进程的这 8 个 syscall 对 Falco 完全不可见
- 优缺点：操作简单、单次写入即可；但影响面广，可能被横向对比发现（"为什么今天 execve 事件量下降了 90%"）

**变体 B：PID 白名单投毒**
- 操作：向 `trusted_pids` 哈希表插入目标 PID
- 效果：该 PID 产生的所有事件被 Falco 视为合法
- 优缺点：手术刀式精准，只影响单个进程；但需要知道或猜测 map ID，且某些实现会周期性清理白名单

**变体 C：Map 级联盲化（本文作者的 #11 文章扩展）**
- 操作：在 `bpf_map_lookup_elem` 函数层面通过 ftrace hook 拦截，对隐藏 PID 返回 NULL
- 效果：更彻底——不管工具使用什么 map，只要 lookup 就返回"条目不存在"
- 优缺点：需要内核模块加载权限（更高门槛）；但通用性更强，一次性致盲所有 eBPF 工具

### 为什么 Falco 的配置不是只读的？

这是 BPF Map Poisoning 最令人不安的地方。Falco 的 eBPF 程序是"一次性加载"的，执行后自身不能再修改 maps 内容。但 Falco 用户空间 agent 需要运行时更新配置（比如添加信任进程、调整规则），而用户空间 agent 更新配置的方式就是通过 `bpf(BPF_MAP_UPDATE_ELEM)`——与攻击者使用的同一个 API、同一个接口、**同一个权限级别**。

BPF 规范定义了 map 创建时的标志位（`BPF_F_WRONLY`、`BPF_F_RDONLY`），但：

1. 大多数 eBPF 安全工具在创建 map 时没有设置只读标志
2. 即使 map 被标记为只读，`bpf()` syscall 层面的保护仍然可以通过修改 map 属性绕过（取决于具体实现）
3. **根本问题是内核没有提供"这个 map 只能由进程 A 写入"的细粒度策略机制**

`security_bpf_map` LSM hook 可以解决这个问题，但它需要：
- 内核编译时启用 `CONFIG_SECURITY` 和 `CONFIG_BPF_LSM`
- 额外的 LSM 模块或 BPF LSM 程序强制写入者校验
- 大多数 Falco 部署默认不启用此保护

### 与 Kernel Rootkit 致盲（#11）的异同

| 维度 | BPF Map Poisoning (#10) | Kernel Rootkit 致盲 (#11) |
|------|------------------------|--------------------------|
| 前提条件 | CAP_BPF 或 CAP_SYS_ADMIN | 内核模块加载权限 |
| 攻击面 | 特定 BPF map 条目 | 13 个内核函数 ftrace hook |
| 攻击粒度 | syscall 级别 | PID/进程级别 |
| 持久性 | 可逆（写回原始值即可） | 需要卸载 hook |
| 工具影响 | 仅影响目标 map 所属工具 | 全局影响所有 eBPF 工具 |
| 检测难度 | 中等（auditd 可记录 bpf() 调用） | 高（hook 本身在更高权限层） |
| 隐蔽性 | 无内核模块，纯用户态操作 | 需要加载内核模块 |

BPF Map Poisoning 的优势在于**不需要加载内核模块**——只是普通用户态进程调用 `bpf(2)` syscall。这意味着即使系统启用了内核模块签名强制（内核锁定），攻击仍然可行。

## 我的思考

### 1. "可观测性的可观测性"——一个递归问题

BPF Map Poisoning 揭示了安全行业一个容易被忽视的问题：**谁来监控监控者？** 如果安全工具本身的运行时状态可以被随便改写，那么基于这些工具生成的告警和数据就不可信。

一个合理的应对思路是**分层可观测性**：将安全工具的运行时完整性校验放在工具之外——比如使用独立的硬件信任根（TPM）定期 attest 关键 BPF map 的哈希值，或者通过独立于主机的网络监测设备验证 Falco 的事件流是否突然中断。

### 2. CAP_BPF 的权限设计需要重新思考

Linux 5.8 引入的 CAP_BPF 将 BPF 相关操作从 CAP_SYS_ADMIN 中拆离出来，降低了 eBPF 的使用门槛。但 BPF Map Poisoning 说明了一个问题：CAP_BPF 赋予了进程读写 BPF maps 的能力，却没有区分"读自己的 map"和"读别人的 map"。

理论上，`bpf(BPF_MAP_GET_FD_BY_ID)` 应该校验调用者与目标 map 创建者之间的权限关系。但当前内核实现仅仅是检查 CAP_BPF 或 CAP_SYS_ADMIN 是否存在，而不关心"这个 map 是谁的"。

### 3. 对于安全产品厂商的建议

如果你的 eBPF 安全产品依赖 BPF maps 存储运行时状态，必须：

- **使用 `security_bpf_map` LSM hook**——这是目前唯一的内核级保护手段
- **运行时完整性校验**——定期（比如每 30 秒）检查关键 map 的条目是否与期望值一致
- **双重通道**——不要将全部检测逻辑依赖单一 BPF map，引入冗余验证路径（例如同时在用户空间保留一份配置快照进行交叉比对）
- **告警 `bpf()` syscall 异常行为**——`auditctl -a always,exit -F arch=b64 -S bpf -k bpf_map_write` 结合异常检测（不是告警所有 bpf() 调用——太多噪声——而是检测短时间内大量 BPF_MAP_UPDATE_ELEM 或针对关键 map ID 的访问）

## 延伸阅读

1. **[Breaking eBPF Security: How Kernel Rootkits Blind Falco, Tracee, and Tetragon](https://matheuzsecurity.github.io/hacking/ebpf-security-tools-hacking/)** / 本系列的姊妹篇（#11），展示从更高权限层（内核模块）全面致盲 eBPF 工具的 13 种 ftrace hook 方法
2. **[Singularity Rootkit](https://github.com/MatheuZSecurity/Singularity)** / 0xMatheuZ 的开源内核 rootkit 项目，包含 BPF Map Poisoning 的完整 PoC 以及更广泛的 eBPF 致盲工具集。GitHub 仓库中 `falco_blind.c` 是 BPF Map Poisoning 的实现核心
3. **[BPF and XDP Reference Guide](https://docs.cilium.io/en/stable/bpf/)** / Cilium 项目的 BPF 参考指南，系统地介绍了 BPF maps 类型和操作 API，有助于理解攻击的技术基础
4. **[Falco 官方文档：内核模块 vs eBPF probe vs modern eBPF](https://falco.org/docs/event-sources/kernel/)** / 理解三种驱动模式的区别有助于判断哪些部署场景会受到 BPF Map Poisoning 影响（`modern_ebpf` 引擎使用 ring buffer，但 map 架构基本相同）
5. **[security_bpf() LSM Hook 文档](https://www.kernel.org/doc/html/latest/bpf/bpf_lsm.html)** / 内核 BPF LSM 机制官方文档——防御 BPF Map Poisoning 的技术方案

---

💡 如果这篇文章对你有帮助，欢迎 **点赞 · 在看 · 分享**

🔗 原文：[matheuzsecurity.github.io](https://matheuzsecurity.github.io/hacking/bpf-map-poisoning-edr-evasion/)
