---
title: BPF 映射投毒：从内部击穿 EDR 的监控体系
cover: /home/media/workspace/company/projects/article-curation/assets/cover-10-bpf-map-poisoning.jpg
---

> 原文: https://matheuzsecurity.github.io/hacking/bpf-map-poisoning-edr-evasion/

> 作者: 0xMatheuZ（安全研究员，Singularity Rootkit 作者）

> 阅读时间: 约 8 分钟

## 一句话总结

大多数 EDR 绕过技术靠"躲"——使用匿名 mmap、直接调用 syscall、memfd 执行。而 BPF Map Poisoning 反其道而行：直接走进 EDR 内部，重写它的内存里的监控配置。

## 他们实际做了什么

0xMatheuZ 以 Falco 作为演示目标（因为它是开源的、可审计的、易于在实验室中复现），但在文章开头就明确指出：CrowdStrike Falcon on Linux、Elastic Defend、Tetragon 等任何把监控状态存储在 BPF maps 中的安全工具，都存在相同的架构性问题。

核心攻击流程分三步：

1. **侦察**：用 `bpftool map list` 找到 Falco 的 `interesting_sys` 和 `syscall_exit_ta` map ID
2. **致盲**：通过 `bpf(BPF_MAP_UPDATE_ELEM)` 将自己目标 syscall 对应条目的值从 1（监控）改为 0（跳过）
3. **还原**：执行完 payload 后，将原始值写回，消除痕迹

## 具体发现了什么

攻击的核心杠杆是 `interesting_sys` 这个数组型 BPF map，有 512 个条目，每个索引对应一个 syscall 号，值 1 表示监控、0 表示跳过。Falco 的 eBPF 程序在每次 syscall 触发时，第一件事就是查这个数组——如果值为 0，直接返回，不生成任何事件。

### 攻击变体

| 变体 | 方法 | 效果 |
|------|------|------|
| PID 白名单投毒 | 向 `trusted_pids` 哈希表插入自己的 PID | EDR 将所有来自该进程的告警视为合法 |
| Syscall 监控抑制 | 清零 `interesting_sys` 中特定条目的值 | 整个系统上所有进程对于这些 syscall 都不可见 |

文章选用的是第二种变体，目标 syscall 有 8 个：execve(59)、execveat(322)、openat(257)、open(2)、connect(42)、socket(41)、read(0)、write(1)。

### 实验证据

| 场景 | Falco 表现 |
|------|-----------|
| 运行 `falco_blind` 前：`cat /etc/shadow`（16:46:12） | 立即告警：`Sensitive file opened for reading` |
| 运行 `falco_blind` 后：payload 期间 `cat /etc/shadow`（17:04:02） | Falco 日志完全没有 17:04:02 的任何事件 |
| restore 后：再次 `cat /etc/shadow`（17:05:53） | 立即告警（17:05:53），无需重启 Falco |

Falco 进程始终在运行，eBPF 程序始终挂在 syscall tracepoint 上——它只是在该时间段内没有产生任何输出。

## 他们公开了什么

完整的攻击工具 `falco_blind.c`（约 150 行 C 代码），核心机制：

- 直接通过 `bpf(2)` syscall 的 `BPF_MAP_GET_FD_BY_ID` 打开目标 map
- 使用 `BPF_MAP_LOOKUP_ELEM` 保存原始值
- 使用 `BPF_MAP_UPDATE_ELEM` 写入零值
- payload 执行后恢复原始值

编译运行：
```bash
gcc -O2 -o falco_blind falco_blind.c
sudo ./falco_blind 155 153
```

参数 155 是 `interesting_sys` 的 map ID，153 是 `syscall_exit_ta` 的 map ID（第二个参数仅用于权限校验，实际只写第一个）。

## 为什么这能行得通

`bpf(BPF_MAP_UPDATE_ELEM)` 是内核的一等 API 调用。它不是内存损坏、不是内核漏洞、也不是 hook 旁路。它是修改 BPF map 的标准文档化方式——正是 EDR 厂商自己用来在运行时配置工具的方式。

Falco 的 eBPF 程序无法区分"合法写入"（Falco 自身 agent 更新配置）和"恶意写入"（攻击者清零监控规则）。两者走同一个 `BPF_MAP_UPDATE_ELEM` 接口，拥有相同的权限级别。BPF API 层面不存在签名、内核级写入策略、或按 map 的归属校验。**检测逻辑和配置状态共享了与被监控威胁相同的访问控制边界。**

### 前提条件

- 需要 `CAP_BPF` 或 `CAP_SYS_ADMIN`（`BPF_MAP_GET_FD_BY_ID` 无条件要求特权）
- 在测试中针对的是 `falco-modern-bpf.service`（`engine.kind=modern_ebpf`）
- 这是一个后渗透技术——适合红队已有 root 权限后，在不触发告警的情况下进行横向移动、凭据窃取或持久化安装

## 如何防御

`security_bpf_map` LSM hook 可以完全封堵此攻击。当进程调用 `bpf(BPF_MAP_UPDATE_ELEM)` 时，内核在授权前会调用 `security_bpf_map(map, fmode)`。LSM 模块或 BPF LSM 程序可以强制只允许 Falco agent 进程写入 Falco 的 maps，其他任何调用者返回 `-EPERM`。

检查方法：
```bash
sudo bpftool map update id 155 key hex 3b 00 00 00 value hex 00
# Operation not permitted → map 受保护
# 无报错 → map 可写，技术可用
```

大多数 Falco 部署默认不启用此保护。

### 取证线索

- `BPF_MAP_UPDATE_ELEM` 调用可通过 `auditd` 审计（`auditctl -a always,exit -F arch=b64 -S bpf -k bpf_map_write`），但审计记录包含调用进程 PID/UID 和 `bpf()` syscall 号，**不包含被修改的具体 map ID**
- 另一个信号是事件间隔——如果 SIEM 对 Falco 每分钟产生的 `execve` 或 `openat` 事件有基线，某个事件类型的静默窗口值得调查

---

💡 如果这篇文章对你有帮助，欢迎 **点赞 · 在看 · 分享**

🔗 原文：[matheuzsecurity.github.io](https://matheuzsecurity.github.io/hacking/bpf-map-poisoning-edr-evasion/)
