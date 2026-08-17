---
title: SakDriver：逆向分析一个内核驱动 Rootkit
cover: /tmp/cover-sakdriver.jpg
author: pwn
---

<style>
body, p, li, table, blockquote {
  font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
  font-size: 14px;
  line-height: 1.8;
  color: #1f2937;
}
p { margin: 0 0 12px; }
h1 {
  font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
  font-size: 24px; line-height: 1.45;
  color: #0f172a; margin: 20px 0 18px;
}
h2 {
  font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
  font-size: 18px; line-height: 1.5;
  color: #0f172a; border-left: 4px solid #2563eb;
  padding-left: 10px; margin: 28px 0 16px;
}
h3 {
  font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
  font-size: 16px; line-height: 1.6;
  color: #1e3a8a; margin: 20px 0 10px;
}
/* Code blocks — dark background, MUST have white-space:pre */
pre {
  background: #1e293b; color: #e2e8f0;
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
  font-size: 13px; line-height: 1.65;
  border-radius: 4px; padding: 14px 16px;
  margin: 14px 0 18px; overflow-x: auto;
  white-space: pre;
}
pre code {
  background: transparent; color: #e2e8f0;
  font-family: inherit; font-size: 13px;
}
/* Inline code */
code {
  background: #eff6ff; color: #1d4ed8;
  border-radius: 3px; padding: 1px 4px;
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
  font-size: 13px;
}
blockquote {
  background: #eff6ff; border-left: 4px solid #3b82f6;
  margin: 14px 0 18px; padding: 10px 14px;
  color: #1e3a8a;
}
blockquote p { margin: 0; }
table {
  width: 100%; border-collapse: collapse;
  margin: 14px 0 18px;
}
th, td {
  border: 1px solid #dbeafe; padding: 8px 12px;
  text-align: left; font-size: 13px;
}
th { background: #eff6ff; font-weight: 600; }
img { max-width: 100%; height: auto; margin: 12px 0; }
a { color: #2563eb; text-decoration: none; }
hr { border: none; border-top: 1px solid #e5e7eb; margin: 20px 0; }
</style>

# SakDriver：逆向分析一个内核驱动 Rootkit

Cobalt Strike 是一个付费渗透测试产品，允许攻击者在受害机器上部署名为"Beacon"的代理。Beacon 为攻击者提供了大量功能，包括但不限于命令执行、键盘记录、文件传输、SOCKS 代理、提权、mimikatz、端口扫描和横向移动。Beacon 是内存型/无文件的——它由无阶段或多阶段 shellcode 组成，一旦通过利用漏洞或执行 shellcode 加载器加载，就会以反射方式将自己注入到进程内存中，而不触及磁盘。它通过 HTTP、HTTPS、DNS、SMB 命名管道以及正向和反向 TCP 支持 C2 和分段传输。

Beacon 载荷因其编写良好、稳定且高度可定制的特性，已成为定向攻击者和犯罪用户中的热门选择。

## 技术分析

拿到 Cobalt Strike Beacon 样本后，我运行了基本的检查和工具来收集代理的初步信息。结果发现，我面对的不是一个用户态 Beacon，而是一个以内核驱动形式加载的内核 rootkit。

它主动 hook 和 patch 了内核中的 ETW（Event Tracing for Windows，Windows 事件跟踪）结构，在防御系统甚至还没来得及记录一个事件之前就将其致盲。它还通过 DKOM（Direct Kernel Object Manipulation，直接内核对象操作）隐藏和取消隐藏进程，通过 `NSI_HIDE_PORT` 隐藏 C2 通信端口，并通过 WFP（Windows Filtering Platform，Windows 筛选平台——Windows 防火墙的核心架构）使用 `WFP_UNBLOCK_IP` 解除对 IP 的屏蔽。

它还操作了虚拟机监控器中的时间戳计数器，很可能是在 rootkit 处于沙箱环境中时。驱动命名为 `SakDriver`，PDB 路径指向 `E:\tools\oldfilelaoda\x64\Release\CrackerDrv.pdb`。

在 IDA 中加载后，在 `DriverEntry` 函数（驱动的入口函数）中，它使用了由 I/O 管理器为每个已安装和已加载的驱动创建的 `DriverObject`——该对象包含驱动例程入口点（如 `DriverEntry`、`Unload`、`MajorFunction` 等）的存储，以及 `DriverName` 等信息。

进一步深入函数，它使用了 `DriverSection` 成员（指向内核模式中 `_KLDR_DATA_TABLE_ENTRY` 的指针，包含已加载模块的信息），将卸载例程设置为 `DriverUnload`，然后从偏移 `0x48` 处加载 DLL 名称。

如果驱动已存在于系统中，它会遍历 `FullDllName->Buffer` 来查找字符串 `\SystemRoot\System32\Drivers`——使用自定义循环而非内核 API——并搜索驱动名称。

然后它调用一个函数，该函数接收驱动名称，打开到 `\\Registry\\Machine\\System\\CurrentControlSet\\Services`（存储每个服务信息的位置）的句柄，分配一块 0x218 字节的带 NX（No-Execute，不可执行）标记的非分页内存（tag 为 `CvrD`），然后遍历 `Services` 下的每个子键，用 `KEY_BASIC_INFORMATION` 填充 `PoolWithTag` 区域。

接着它为目标驱动打开句柄，查询其 `ImagePath`（指向文件位置），将路径存储到全局状态中，然后将该驱动注册为一个微过滤驱动（minifilter driver，一种用于监控、拦截和修改文件系统 I/O 请求的驱动类型），并将 `IRP_MJ_DIRECTORY_CONTROL` 设置为 `MajorFunction`，以控制目录 I/O 请求，从而逃避反病毒软件的扫描。

然后它收集受害者的操作系统指纹信息，构建一个 POST 请求，将这些信息发送到 C2 服务器（`43.160.247.24`）的 `/api/event.php` 端点，使用自定义 User-Agent `SakDriver 1.0`。

如果是全新安装，它会将驱动安装到以下路径之一：`\Device\HarddiskVolume%lu\Windows\System32\drivers\DRIVER_NAME`、`\??\C:\Windows\System32\drivers\DRIVER_NAME`，命名为 `sak_XXX.sys`——其中的 `XXX` 是一个随机种子，由内存区域、频率性能计数器、PID 和 TID 的 XOR 运算生成。同时，它在注册表 `\Registry\Machine\System\CurrentControlSet\Services\SERVICE_NAME` 中将服务注册为 `msXXXX`，显示名称为 `Microsoft Player Service`，描述为 `Provides support for %04X operations`，以伪装成合法的 Windows 服务。出错时则注册为 `SakDriver`（不太 OPSec 友好 :)）。一切顺利的话，它向 C2 发送 `install_success` 标记并删除木马投放器。

现在它查询系统以获取 `ntoskrnl.exe` 的内存地址，然后在内存中构建自己的导入地址表（IAT），以使用内核 API——通过 `sub_14000E34C` 去混淆 API 名称并在运行时解析其地址。该函数接收 `ntoskrnl` 的地址和已解密的 API 名称，手动解析 PE 头以查找导出目录，从而获得这些 API 的地址，同时帮助绕过 EDR 的静态扫描。

接着它调用 `CmRegisterCallbackEx` 注册一个 RegistryCallback 例程，该例程在每个线程对注册表执行操作时被调用。这个名为 `Function` 的注册表回调函数执行如下操作：如果某个反病毒软件试图修改 rootkit 的注册表键，它会捕获该修改并返回 `STATUS_ACCESS_DENIED`，从而中止注册表修改。

有趣的部分来了。作者的做法是，利用这个注册表回调从用户态代理接收 C2 指令。当用户态代理使用用户态 API 在良性的注册表位置写入指令时，这完全绕过了 EDR 扫描——尽管每次写入时驱动都会检查特定的魔术字节 `0x2625B7146B` 并从中提取指令。

| 命令 ID | 功能 |
|---------|------|
| 22661 | 任意读/写进程内存（将内核线程附加到目标进程的地址空间，锁定并映射内存到内核，然后读/写进程内存） |
| 13592 | 执行 KVA Shadowing 绕过，以注入并从用户态进程窃取信息 |
| 13129 | 在受害进程中分配内存以执行进程注入 |
| 13128 | 线程执行完成后释放已分配的内存 |
| 12937 | 在两个不同进程之间复制数据（rootkit 和用户态） |
| 10246 | 附加到用户态进程并解析 DLL 以定位 API 名称的地址，如 VirtualAlloc |
| 9072 | 强制删除受保护或正在执行的文件 |
| 8784 | 通过内核 APC 排队实现内核到用户态的 DLL 注入（解析 PEB 以定位 kernel32.dll，并通过解析 DLL 找到 LoadLibrary 的地址） |
| 8306 | 防御措施：每当系统执行 OpenProcess 或 OpenThread 时设置回调，拦截为 rootkit 打开的句柄和线程，剥离 `PROCESS_TERMINATE`、`PROCESS_QUERY_INFORMATION` 等权限；同时将低权限进程的访问权限提升到更高级别（设置为 `PROCESS_ALL_ACCESS`），专门针对 v8（仍在调查中） |
| 16964 | 查询内存以获取区域的基本信息 |
| 22323 | 通过读/写虚拟指针来读/写任意物理内存地址——该虚拟指针通过构建一个具有读写权限的自定义页表并注入到 PML4 的空闲空间中获得。同时支持直接从物理地址复制数据 |
| 13831 | 通过进程名解析 PID |
| 13664 | 伪造硬件，以鼠标或 PS2 键盘/鼠标数据的形式发送数据包并执行带有伪造输入的回调。涉及 `\Driver\mouhid` 和 `\Driver\i8042prt` |
| 37720 | 通过查询 PFN 数据库并映射地址来读取进程内存 |
| 39029 | 修改页面的保护属性 |
| 38536 | 从 EPROCESS 结构返回进程段基址 |
| 38435 | 遍历进程的 PEB，循环遍历已加载的 DLL，与攻击者提供的名称（如 kernel32.dll）匹配，并返回 DLL 的基址 |
| 37973 | 提取驱动名称 |
| 29557 | 去混淆 API 并获取其地址，同时在 CKCL、ETWP、系统调用表、HvlGetQpcBias、GetCpuClock 上施加系统级 hook。另外扫描 win32kfull.sys 的 text 段中特定模式，以获取未文档化函数的地址 |
| 29556 | 伪造硬件，以键盘或 PS2 键盘/鼠标数据的形式发送数据包并执行回调。涉及 `\Driver\kbdhid` 和 `\Driver\i8042prt` |
| 25736 | 对磁盘、Nvidia GPU、SMBIOS 执行硬件伪造 |
| 25897 | 使用攻击者的 DLL/恶意函数执行用户线程 |
| 29462 | 反射式 PE 加载 |

然后它在注册表上施加了一层隐身层，以避免反病毒软件和 EDR 对驱动服务的任何读/写查询。

以上是它在回调函数中执行的操作。回到主入口，它现在执行网络过滤——拦截安全解决方案对 C2 IP 和端口发起的请求。它还通过 hook NSI 驱动隐藏了一些端口：6891、12341、12342、12343、6543、7543、9199。

然后它尝试创建一个 WFP 设备对象，以便观察网络栈，并将 `just-do-it.icu` 和 `91.99.165.207` 添加到阻止表中。

## IOCs

- **域名和 IP**：`just-do-it.icu`、`91.99.165.207`、`43.160.247.24`——这些域名和 IP 尚未被 VirusTotal 标记，基础设施仍在运行中
- **端口**：6891、12341、12342、12343、6543、7543、9199
- **端点**：`/api/event.php`
- **哈希**：`4e95aba17c1a423cda5cc9f9f04f7cf8db17e294eb31ed1aa85063601b82fe8d`
- **驱动名称**：SakDriver、sak_xxx.sys、ms_xxxx

## Yara 规则

```yara
rule SakDriver_Rootkit {

    meta:
        description = "Detects an advanced Windows kernel rootkit featuring ETW/CKCL hooking, NSI manipulation, HWID spoofing, and WFP."
        author = "0xSec"
        date = "2026-07-26"

    strings:
        /*
            Use of ntoskrnl.exe / ntkrnlpa.exe
        */
        $ntoskrnl_str = "ntoskrnl.exe" ascii
        $ntkrnlpa_str = "ntkrnlpa.exe" ascii

        $ascii_str1 = "AWAVAUATASARAQAPPQSRUTVW" ascii
        $ascii_str2 = "_^\\]Z[YXAXAYAZA[" ascii

        /*
            Encrypted API Strings
        */
        $enc_api_alloc   = "iCtZ[WZ[OYkWM44#/\t +(:0J" ascii // ZwAllocateVirtualMemory
        $enc_api_win32k  = "lkB_Y\x0b\x0bQHHH\\`@" ascii       // __win32kstub_
        $enc_api_ntuser  = "}@`ERJhO^NDiV.%-4D" ascii       // NtUserQueryWindow

        /*
           WFP Targets
        */
        $domain       = "just-do-it.icu" ascii wide
        $ip           = "91.99.165.207" ascii wide
        $dev_sakdriver   = "\\Device\\SakDriverWFP" ascii wide

        /*
           Debug Strings
        */
        $dbg_etw_hook    = "[%s] ssdt call back ptr is 0x%p" ascii
        $dbg_khook_init  = "k_hook::Initialize" ascii
        $dbg_nsi_hide    = "[NSI] Adding default hidden ports..." ascii
        $dbg_wfp_fail    = "Failed to start auto-unblock thread" ascii
        $dbg_wfp_ip_block = "Failed to add IP to block list." ascii

        /*
            HWID spoof strings
        */
        $hwid_mouse = "\\Driver\\mouhid" ascii
        $hwid_keyboard = "\\Driver\\kbdhid" ascii
        $hwid_gpu = "\\Driver\\nvlddmkm" ascii
        $hwid_ps2 = "\\Driver\\i8042prt" ascii

    condition:
        uint16(0) == 0x5A4D // PE magic
        and filesize < 5MB
        and (
            ($ascii_str1 and $ascii_str2)
            and all of ($hwid_*)
            or ($ntoskrnl_str and $ntkrnlpa_str)
            or 2 of ($enc_api_*)
            or ($dev_sakdriver and $domain and $ip)
            or all of ($dbg_*)
        )
}
```

---

*免责声明：本文为 0xSec 博客文章的中文翻译。原文作者 0xSec，发布于 0xsec.gitbook.io。所有技术内容版权归原作者所有。翻译仅供教育研究目的。*
