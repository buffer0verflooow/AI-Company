---
title: 逆向工具包：CallHook、TrueDiffing、ExportFinder 与 NetHook
cover: /tmp/cover-truecyber.jpg
author: pwn
status: 反例 — 无质量，用户判定不值得投入
reason: 纯工具包翻译、无深度分析、无独立见解。类似 truecyber 类纯翻译文章应提醒用户避免投入。
---

<style>
/* From wechat-article.css — tag selectors only */
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, 'Helvetica Neue', 'PingFang SC', 'Microsoft YaHei', sans-serif; font-size: 17px; line-height: 1.8; color: #2c3e50; max-width: 100%; margin: 0 auto; padding: 16px; }
h2 { font-size: 1.3em; border-left: 4px solid #1a73e8; padding-left: 12px; margin-top: 2em; margin-bottom: 0.8em; color: #1a1a1a; }
h3 { font-size: 1.1em; margin-top: 1.5em; margin-bottom: 0.6em; color: #333; }
p { margin: 0.8em 0; text-align: justify; word-break: break-word; }
pre { background-color: #f6f8fa; border-radius: 6px; padding: 16px; overflow-x: auto; font-size: 14px; line-height: 1.45; font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', Consolas, 'Liberation Mono', Menlo, monospace; white-space: pre; word-break: normal; }
code { font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', Consolas, 'Liberation Mono', Menlo, monospace; font-size: 0.9em; background-color: #f0f2f5; padding: 2px 6px; border-radius: 4px; }
pre code { background: none; padding: 0; }
blockquote { border-left: 4px solid #1a73e8; padding: 12px 16px; margin: 16px 0; background-color: #f8f9fa; color: #555; }
blockquote p { margin: 0; }
ul, ol { padding-left: 24px; }
li { margin: 0.4em 0; }
strong { color: #1a1a1a; }
a { color: #1a73e8; text-decoration: none; }
a:hover { text-decoration: underline; }
img { max-width: 100%; border-radius: 4px; margin: 16px 0; }
hr { border: none; border-top: 1px solid #e0e0e0; margin: 2em 0; }
</style>

# 逆向工具包：CallHook、TrueDiffing、ExportFinder 与 NetHook

> 或者说，一套工具就是一个调试循环

每次逆向工程会话都以同样的方式开始：你有一个二进制文件，而它毫无兴趣向你解释自己。没有符号表，没有源码，没有文档，而且很可能有人花了不少精力确保它保持这种状态。你拥有的是一组问题，而你回答这些问题的速度，就是整场游戏。

过去一年里，我们围绕这些问题构建了一系列工具。不是一个全能但什么都做不好的"逆向套件"，而是小巧、锋利的 Windows 工具，每款快速回答一个问题，然后不挡你的路。这篇文章带你快速了解其中三款：**CallHook**、**TrueDiffing** 和 **ExportFinder**，再说说 **NetHook** 的定位。

它们也指向一个更大的图景。这四款工具是 **Reverse Engineering for Red Teamers**——我们下一期培训（即将推出）——的实战装备。如果你想第一时间知道上线时间以及新工具的发布消息，订阅邮件列表——两者都会在那里首发。

[图片：四工具逆向循环示意图 — CallHook 观察调用，ExportFinder 解析符号，TrueDiffing 比较构建版本，NetHook 拦截流量，附带一条反馈箭头回到起点。]

## 读二进制文件的问题

**静态分析**告诉你程序**能**做什么。把它放进反汇编器，跟踪交叉引用，你会得到全部可能行为的空间。这个空间巨大无比，其中大部分永远不会被执行，而且没有任何信息告诉你程序在凌晨 3:00 决定回连时实际走了哪条分支。

**动态分析**告诉你程序**刚刚做了什么**，这有用得多，但也难捕捉得多。挂上调试器，你获得精度，但同时也获得一个"停掉世界"的工作流：断点、检查、单步、继续、丢失位置、从头再来。这对单个函数还行。但有趣的行为分散在四个线程、一个子进程和八秒你总在跳过的启动过程中时，这套流程就崩了。

这套工具包的存在就是为了弥合这个差距：观察整个运行过程，然后深入分析那十二条真正关键的指令。

## CallHook：它到底在调什么？

CallHook 注入到正在运行的进程中，按顺序记录它发起的调用及其参数。不只是 IAT 中的导入，也不只是沙箱恰好 hook 的那几个 API：在全单步模式下，它观察执行本身，因此动态解析的调用、内部辅助函数，以及任何通过指针到达的代码，都会和显而易见的 kernel32 调用一起出现。

[图片：CallHook 运行中追踪 — 一个 PowerShell 进程的 3,593 次调用，选中的调用参数以十六进制和 ASCII 形式转储。]

截图中几个细节比看上去重要得多：

**调用方和被调用方都被解析了**，调用方携带着它的偏移（`rpcrt4!I_RpcTransServerNewConnection+0xDFB`）。那个偏移是屏幕上最有用的数字：它直接把你扔到反汇编器中的调用点，无需搜索。

**线程和深度是每次调用独立的**。深度让你看到调用树的形状，无需手工重建栈；线程列让两个并发流不会读成一条混乱的顺序。

**子进程会被追踪**。顶部的标签页显示 powershell (14320) 和它生成的 conhost (1792) 子进程被一起追踪。任何在派生辅助进程里做真正工作的东西都不再隐形。

**加载器活动也能追踪**。打开加载器追踪，你能看到模块解析的实时过程——大量有趣的行为隐藏在这里：侧加载、延迟导入和手动映射都会在此显形。

**参数是捕获的，不是猜的**。下方面板显示原始指针值以及背后内存的十六进制和 ASCII 转储。当被调用方无文档时，arg0 里的字节通常告诉你是这个函数做什么的。

追踪结果可按函数、模块或参数内容搜索并可保存。这能把一个行为问题变成一个可 diff 的产物：捕获工作案例的追踪，捕获失败案例的追踪，然后对比两者。

## TrueDiffing：什么变了？

Diffing 是逆向工程中杠杆率最高的技术，因为发布补丁的厂商已经精确告诉了你 bug 在哪里。难度从来不在概念上，而在于重新编译会移动一切。地址变了，函数顺序变了，编译器内联了新的东西，字节级别的 diff 会点亮整个文件。

TrueDiffing 在你实际思考的层面工作。它解析两个文件，跨版本匹配函数，然后将每个函数分类为已修改、新增、已删除或未变更。下面截图的计数器用一行告诉你整个构建的情况：278 个修改，1,947 个新增，1,671 个删除，共 3,896 个函数。

[图片：TrueDiffing 函数级对比 — 左侧是按已修改/新增/删除分类的函数列表，右侧是选中函数的指令级 diff，显示操作码字节以及彩色标记的新增/删除指令。]

看看那个 diff 的头部：`primary 0x10fe4 → secondary 0x11c7c`，附带 `+5/-5 insns` 摘要。函数在两个构建之间移动了超过 3KB，但仍然被匹配并在指令级对齐，操作码字节紧邻助记符，这样你能看到单靠反汇编文本会隐藏的编码变化。这个对齐能力就是全部要点：你在 diff 代码，不是在 diff 偏移。

它也不限于 Windows 二进制文件。截图中的构建是 **ELF，ARM 32-bit**——恰恰是那种在任务中当有趣设备不是笔记本时会出现的目标。旁边还有一个 Strings 标签页，理由相同：一个变化了的字符串往往是通往变化了的功能的最快路径。

当指令列表不够时，切换到图形视图：

[图片：TrueDiffing 图形视图 — 两个控制流图并排显示，基本块按未变更/已修改/新增/删除着色，边标记为已走通/未走通路径。]

两张控制流图并排放置，每个基本块按变更情况着色，每条边标记为已走或未走路径。这就是你发现边界检查悄悄多了一个分支、或循环多了一个出口的方式。悬停一个块就能得到完整的反汇编；两个面板可独立缩放和平移，这样你可以在两边同时保持感兴趣的子图在视野内。完成后，导出报告交给当时没坐在你旁边的人。

最明显的用途是 1-day 分析，但它在日常工作中同样频繁地证明自己的价值：对比加混淆前后的自己的构建，检查供应商在"小版本"更新中改了些什么，或者区分同一个恶意软件家族的两个样本。

## ExportFinder：它真正导入了什么？

CallHook 告诉你一个调用去了 `kernel32!AcquireSRWLockExclusive`。然后你去 kernel32.dll 里找那个函数，发现它不在那里。至少，在有意义的层面上不在。

[图片：ExportFinder 显示 kernel32.dll 的导出表 — 左侧是文件夹中的 DLL 列表，右侧是序数、RVA、名称和转发目标的表格，包括标记为转发器的条目。]

ExportFinder 读取导出表，向你展示真正有什么：序数、RVA、名称，如果有转发目标也一并显示。截图立即点明了问题。`AcquireSRWLockExclusive` 没有 RVA。它是一个**转发器**，指向 `NTDLL.RtlAcquireSRWLockExclusive`，而你要读的代码在一个完全不同的模块里。`AddDllDirectory` 转发到一个 `api-ms-win-core-libraryloader` API 集，解析到哪里又取决于宿主的版本。

这不是冷知识。从中可以得出三点：

**纯序号导出没有名称可以搜索**。大量库只按序号导出，调用 `#57` 序号在你看到有序表之前毫无意义。ExportFinder 在同一视图中列出序号和名称，因此一个序号导入变成了一个真实的目标。

**全文件夹搜索回答"谁导出了这个？"**。指向 System32（截图中的 3,732 个 DLL），可选递归，输入一个导出名，得到提供它的每一个模块。这是从追踪中解析陌生符号的最快方式，也是在你映射代理或计划劫持时枚举候选的最快方式——你需要完整的排序导出列表来构建匹配的存根。

**它只读取文件**。ExportFinder 从不加载或执行它检查的 DLL。这是检查不受信任样本和运行它之间的硬性区别。

它还是**免费的**。没有许可证，没有试用计时器。下载它，放在你其他工具的同一个文件夹里。

## NetHook：它发送了什么？

第四个问题，在二进制文件开始与什么东西通信时就会出现。NetHook 在进程的流量被加密**之前**捕获、检查和重写它——通过 hook 应用程序用来发送和接收数据的 API 调用。证书固定、自定义 TLS 栈和无法理解代理的代码全部不再成问题，因为你读取的是从未到达网络的缓冲区。

[图片：NetHook 附加到一个 PowerShell 进程 — 捕获表显示 EncryptMessage、DecryptMessage 和 recv 调用，包含源和目标地址；旁边是数据包面板，以十六进制和 ASCII 显示纯文本 HTTP 请求。]

从左到右读那个捕获表：EncryptMessage 发出的数据，DecryptMessage 和 recv 返回的数据，连接目标是一个 443 端口。右侧面板是同一个流量，以纯文本 HTTP 请求的形式显示，十六进制在一侧，ASCII 在另一侧，因为 hook 位于缓冲区上而非套接字上。从 Capture 模式切换到 Intercept 模式后，数据包会被保持而非传递，这样你可以编辑它，然后转发、修改或丢弃——测试从此从观察变为交互。

我们之前已经详细写过它，所以不在此重复：参见《使用 NetHook 进行厚客户端渗透测试》获取完整操作指南，或访问 NetHook 产品页面。

## 放在一起用

以下是工具都在同一个工具箱里时，一次真实会话的样子。假设你拿到一个更新程序二进制文件，它在做一些不该做的事。

**用 CallHook 运行它。** 全单步追踪、所有线程、加载器追踪打开、追踪它生成的子进程。90 秒后你有几千个调用，以及更有用的——一个深度列，显示所有有趣的事情都发生在一个深度为 4 的调用方下面。

**用 ExportFinder 解析那个异常调用。** 一个被调用方是从你从未见过的 DLL 导入的序号。全文件夹搜索找到它，导出表命名了它，一个转发器链指向实际实现它的模块。

**用 TrueDiffing 对比两个版本。** 行为是新的，因此对比上个月的构建和这个月的。在 3,896 个函数中，278 个变了，而图形视图显示其中一个多了一个之前没有的分支。那就是你的函数。

**用 NetHook 观察网络。** 确认它发送了什么，明文的，你就有了完整的故事：哪条代码路径、为什么变了、什么东西离开了这台机器。

每一步花了几分钟，产生了一个可以交给其他人的产物。没有一步需要坐在断点上期望对的线程命中它。

## 这些工具是 RE 培训的骨干

所有这些同时存在是有原因的。这里的每一个工具都是我们下一期培训 **Reverse Engineering for Red Teamers** 的一部分，而课程是围绕它们构建的，而不是围绕幻灯片。你将追踪一个真实进程，解析它导入了什么，将两个构建 diff 到发生变化的分支，并拦截它发送的内容——使用同样的工具，面对你在真实任务中遇到的那类目标。

课程**即将上线**。完整的课程大纲已经在线，如果你想确切知道它涵盖了什么。工具模块正在围绕这些工具构建中。上线公告将通过邮件列表首发。

## 试试它们

CallHook、TrueDiffing 和 NetHook 各自带有免费试用，ExportFinder 则完全免费。它们都在 Software hub 上。把它们对准你一直想弄明白的东西，看看在你吃午饭之前能走多远。

---

*免责声明：本文为 TrueCyber Inc. 官方博客文章的中文翻译。原文作者 Mr.Un1k0d3r (Charles F. Hamilton)，发布于 blog.truecyber.world。翻译已获得授权。所有产品截图和功能描述版权归 TrueCyber Inc. 所有。*
