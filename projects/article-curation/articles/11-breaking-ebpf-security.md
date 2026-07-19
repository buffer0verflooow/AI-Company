---
title: 攻破 eBPF 安全：内核 Rootkit 如何让可观测性工具集体失明
cover: /home/media/workspace/company/projects/article-curation/assets/cover-11-breaking-ebpf-security.jpg
---

> 原文: https://matheuzsecurity.github.io/hacking/ebpf-security-tools-hacking/

> 作者: 0xMatheuZ（安全研究员，Singularity Rootkit 作者）

> 阅读时间: 约 10 分钟

## 一句话总结

eBPF 安全工具（Falco、Tracee、Tetragon 等）的监控能力建立在一个隐含假设之上——内核是可信的观察者。当攻击者能加载内核模块时，最有价值的目标不是 eBPF 程序本身，而是它们周围的数据传输管道：迭代器、ring buffer、perf buffer、map 操作。这些管道一旦被 hook，eBPF 工具就会在一个"一切正常"的假象中彻底失明。

## 他们实际做了什么

0xMatheuZ 基于 Singularity Rootkit（一个开源的隐蔽内核 rootkit 项目），系统性地研究了如何通过 ftrace hooking 使 eBPF 可观测性工具失效。研究覆盖了 eBPF 数据流的全部四个关键路径：

1. **BPF 迭代器**（GhostScan、Decloaker 等工具用于枚举进程和网络连接）
2. **Ring Buffer**（Falco modern eBPF probe 的 kernel→userspace 事件通道）
3. **Perf Buffer**（Tracee 和老版本 Falco 的事件通道）
4. **BPF Maps**（工具存储进程元数据、维护白名单/黑名单的关键数据结构）

## 具体发现了什么

### 攻击面全景

研究揭示了 eBPF 安全工具的数据流不是"单片"的——它是一个从内核到用户空间的多层管道，**每一层都是可拦截的**。

```
内核事件 → eBPF 程序 → [迭代器/ringbuf/perf/map] → 用户空间 → 告警
                          ↑ 全部可被 ftrace hook 拦截
```

### hook 的 13 个内核函数

文章提供了完整的 hook 表：

| 类别 | 被 hook 的函数 | 影响 |
|------|---------------|------|
| 迭代器 | `bpf_iter_run_prog` | 枚举进程/连接时跳过隐藏目标 |
| 迭代器 | `bpf_seq_write` / `bpf_seq_printf` | 过滤输出中的隐藏 PID 数据 |
| Ring Buffer | `bpf_ringbuf_reserve` | 拒绝为隐藏进程分配缓冲区 |
| Ring Buffer | `bpf_ringbuf_output` | 丢弃隐藏进程的事件数据 |
| Ring Buffer | `bpf_ringbuf_submit` | 阻止隐藏进程事件提交到环形缓冲区 |
| Perf Event | `perf_event_output` | 丢弃隐藏进程的 perf 事件 |
| Perf Event | `perf_trace_run_bpf_submit` | 阻止 tracepoint 数据提交 |
| BPF Map | `bpf_map_lookup_elem` | 对隐藏 PID 的查找返回 NULL |
| BPF Map | `bpf_map_update_elem` | 对隐藏 PID 的更新静默失败 |
| eBPF 执行 | `__bpf_prog_run` | 对隐藏进程直接返回 0，不执行 eBPF 程序 |
| syscall | `__x64_sys_bpf` / `__ia32_sys_bpf` | 监控/过滤 bpf() 系统调用 |

### 进程隐藏机制

- **PID 追踪**：维护 `hidden_pids[32]` 数组 + `child_pids[4096]` 子进程追踪
- **父子进程链**：向上遍历最多 10 层父进程，自动隐藏所有子进程（隐藏一个 shell → 所有 spawn 的进程全部隐藏）
- **网络隐藏**：按端口（8081）和 IP 地址过滤 TCP/UDP 连接

### 实际绕过结果

| 目标工具 | 测试项目 | 结果 |
|---------|---------|------|
| **Falco** | 反向 shell（端口 8081） | 未检出任何事 |
| **Falco** | 执行交互式 bash shell | 未检出 |
| **Falco** | 修改/读取 `/etc/passwd`、`/etc/shadow` | 未检出 |
| **Falco** | 建立多个 TCP 连接 | 未检出 |
| **Falco** | 信号提权（kill -59） | 未检出 |
| **Tracee** | 进程枚举 | 隐藏进程不出现在列表中 |
| **Tracee** | Syscall 追踪 | 隐藏进程的 syscall 不出现在追踪中 |
| **Tracee** | 网络连接 | 隐藏连接不可见 |
| **GhostScan** | 多方法进程枚举对比 | 全部一致显示"不存在" |
| **Decloaker** | 隐藏网络连接检测 | 目标端口连接不可见 |

## 他们公开了什么

完整的 PoC 代码在 [Singularity Rootkit](https://github.com/MatheuZSecurity/Singularity) 仓库中，包括：

- **迭代器过滤逻辑**：识别 TCP/UDP/进程迭代器上下文结构体，对隐藏目标返回 0（跳过）
- **Ring Buffer 三层拦截**：在 reserve → output → submit 三个时机分别过滤
- **Falco 事件结构识别**：通过 `falco_event_hdr` 结构体验证（type 1-400、len 0-65535、nparams < 20），精确识别 Falco 事件并过滤
- **子进程追踪**：`is_child_of_hidden_process()` 函数，利用 `rcu_read_lock` 安全遍历 task_struct 的 `real_parent` 链
- **Map 操作静默拦截**：`bpf_map_lookup_elem` 对隐藏 PID 返回 NULL（"条目不存在"），`bpf_map_update_elem` 静默成功（"看起来写入了但实际上没有"）

## 为什么这能行得通

五个核心发现：

1. **打数据通道，不打程序本身**：eBPF 程序可以"正常运行"，但只要控制内核侧的事件传输管道，输出就永远不会到达用户空间
2. **迭代器是另一类攻击面**：GhostScan、Decloaker 等基于迭代器的工具依赖 `bpf_iter_run_prog()`，在这里过滤就能让多个系统视角一致地说谎
3. **事件传递是咽喉点**：不管工具用的是 ring buffer 还是 perf buffer，kernel→userspace 边界都提供了天然拦截点
4. **状态可以选择性擦除**：Map 的 lookup/update 是让隐藏 PID "不存在"的便利位置，不会破坏系统的其余部分
5. **内核一旦被控制，可观测性就是尽力而为**：eBPF 在可信内核下提高了可见性门槛，但它不能加固一个已被攻陷的内核

## 防御含义

- 不能仅依赖内核级可观测性。需要纵深防御：Secure Boot + 签名模块、网络层检测、硬件信任根 + 远程证明
- 一个被攻陷的内核无法自我保护——eBPF 提高了可信内核下的安全门槛，但不能成为针对恶意内核的最后防线
- 真正的防线在于阻止内核 compromise 本身：启动信任、模块签名强制、主机外的分层检测

---

💡 如果这篇文章对你有帮助，欢迎 **点赞 · 在看 · 分享**

🔗 原文：[matheuzsecurity.github.io](https://matheuzsecurity.github.io/hacking/ebpf-security-tools-hacking/)
