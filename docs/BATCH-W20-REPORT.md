# BATCH-W20 收工报告 —— worker 落盘输出必须有一道无界之墙

- 交付方:Claude Code(glm-5.3-flash,用户指定"提前到今天由会话直接实现",未走 swarm 派发 ⇒ 零蜂群预算消耗)
- 起点 HEAD:`bbdfd4e`(company 仓 main);分支 `batchW20`(worktree `/home/pwn/w20-worktree`);终点 HEAD 见 §4
- 活库 `swarm_v2.db`:**全程零写入**(改动路径不触碰库;测试 scratch 全在仓库根 `automation/tests/.w20-scratch/`,已清理)
- 不 push / 不 amend / 不 rebase;未动 `router_config.json` 任何既有键;未引入第三方依赖

## 1. 排查表(六条派工路径 × 全部位点)

| 位点 | 调用方 | 改前形态 | 本批处置 |
|---|---|---|---|
| `company_router.py` content 线(原 :2340) | `_launch_content_executor` | `open("a") → Popen(stdout=log_fh)` | ✅ `log_boundary.spawn_bounded` |
| `company_router.py` content-v2(原 :2586) | `launch_v2_content_worker` | 同上 | ✅ 同上 |
| `company_router.py` vuln-v2(原 :3202) | `launch_v2_security_worker` | 同上 | ✅ 同上 |
| `company_router.py` ops-v2(原 :3421) | `launch_v2_research_worker` | 同上 | ✅ 同上 |
| `company_router.py` dev-v2(原 :3748) | `launch_v2_dev_worker` | 同上(cwd=repo) | ✅ 同上 |
| `swarm_pool_supervisor.py:290` `default_launch` | 常驻池 detached 启动 | 同上 | ✅ 同上 |
| `content_hermes_executor.py` | — | grep 全文:无 `Popen`/`log_dir`/`open(` 直连位点 | 无需改 |
| `_safe_io.py:417` / `swarm_pool_supervisor.py:307` | 行级追加助手 | `with path.open("a") as s: s.write(line)` | 非子进程 stdout 沉淀池,行级有界,不在缺陷面 |

改后全仓 `grep 'stdout=log_fh'` = 0 处(由接线测试 `test_four_v2_submit_sites_wired_to_gate` 反证锁定)。

## 2. 方案与取舍

**机制:管道 + detached 边界进程(`automation/log_boundary.py`,纯 stdlib,259 行)。**
worker 的 stdout/stderr 不再直连文件,而是管道写端;读端由独立边界进程
(`python3 automation/log_boundary.py --fd N --path P …`,`start_new_session=True`)按
`max_file_bytes × max_files` 轮转落盘。默认 64MiB × 4 + 当前 ⇒ 总占位硬上限 ≈ 5×64MiB。

**① 上限与轮转,为何不丢证据**:当前文件永远持有最近字节(崩溃现场);每次淘汰最旧轮转都
在**新当前文件**写入淘汰记录(`evicted=… dropped_total=…` + UTC 时间戳);EOF 写终态记录
(`total_bytes/rotations/dropped_bytes`)。被淘汰的字节如实陈述"已淘汰 N 字节",不伪造完整假象。

**② 启动前磁盘水位门**:`_disk_headroom_precheck(config)` 挂在四条 v2 submit 线的
`_v2_daily_top_precheck` 之后(同一零副作用前置链,仍在 `v2 run create` 之前);
`spawn_bounded` 内再门一次(防线冗余,supervisor 路径不经过 router 预检)。拒绝 =
`DiskHeadroomError(RuntimeError)`,**未建目录、未起进程、未写库**。下限默认自适应:
`min(20GiB, 所在文件系统总量 5%)` —— 取值时机在调用时,`SWARM_LOG_MIN_FREE_BYTES`
显式覆盖则绝对生效(本机实测必要性:`/tmp` 是 3.9G 的 tmpfs,绝对 20GiB 会把小盘永久锁死)。
读数不可得 ⇒ 响亮降级放行(与日顶预检同口径)。

**③ 进程级写入边界,为何不可绕过**:worker 的 fd1/fd2 **就是**管道写端——写多快都只进
边界进程的 64KB 读循环,磁盘占位由边界进程决定;子进程再 fork 孙进程,fd 随继承链传播。
边界进程是**独立 detached 进程**,不是 worker 里的线程/回调,worker 代码路径上不存在
"换掉落盘方式"的开关;除非被派工方显式另开文件(那已是它自选的产物路径,归执行面既有
纪律管),否则无绕出面。

**④ 失败语义**:边界进程**从不杀 worker、不碰 worker 的退出码**(只丢字节)⇒ 不存在
"被边界掐死"的新终态,`stop_reason`/判定对账口径零改动;淘汰/终态记录进日志文件本身,
判定器读的是 `agent_trace` 与产物,不受日志截断影响。水位门拒绝发生在造 run 之前 ⇒
与日顶预检同款"零悬挂 run"语义。其余 Popen 语义(stdin=DEVNULL、stderr=STDOUT、
`start_new_session=True`、`close_fds=True`、失败上抛)逐字保留。

**取舍与被否备选**:❌ 日志内嵌 RLIMIT_FSIZE —— 管道写不受 RLIMIT_FSIZE 约束,且会误伤
worker 的合法产物写;❌ 父进程内起读线程 —— router 死亡即断日志并让 worker 撞 EPIPE,
违背"失败语义不变";❌ 只加告警不加硬边界 —— 派工书明令禁止;❌ logrotate 式外部工具 ——
引入系统依赖,且无法做到"随派工自动生效"。

## 3. 复现演示(三段实测,2026-09-20 深夜,本机)

- **a) 改后有界**:同形无界 writer(纯 8KB×flush 循环)走 `spawn_bounded`,跑满 3 秒:
  实产 **8,727,425,508 B(8.7GB)**,轮转 134 次;磁盘总占位 **332,292,704 B**(5 个文件,
  ≤ 64MiB×5 上限);淘汰记录例 `evicted=a-bounded.log.130 dropped_total=8727425508`;终态
  记录含 `total_bytes/rotations/dropped_bytes`。
- **b) 改前对照**:同一 writer 直连文件(`open("w") + stdout=fh`)仅 **1.5 秒**:
  **4,162,781,184 B(4.2GB)无界增长** —— 与 09-20 21:35 事故(5 秒 10.4GB)同形同量级。
- **c) 水位门拒绝**:`SWARM_LOG_MIN_FREE_BYTES=10^30` ⇒
  `DiskHeadroomError: W20 磁盘水位门拒启:… 零副作用:未创建 run/task/审计,未建目录,
  未起进程`;**拒绝路径残留文件数 = 0**(目标目录保持为空)。

## 4. 提交与逐文件行数

| 文件 | 变更 |
|---|---|
| `automation/log_boundary.py` | 新增 259 行(边界进程 + spawn_bounded + 水位门) |
| `automation/company_router.py` | +35/−106(5 处派工块迁移;新增 `_disk_headroom_precheck` 与 import;4 处预检调用) |
| `automation/swarm_pool_supervisor.py` | +10/−23(`default_launch` 迁移 + import) |

commit:①`<代码>` ②`<测试>` ③`<报告>`(见 git log;分支 `batchW20`,**未 push**)

## 5. 测试读数(改前红 / 改后绿 成对)

- 命令:`python3 -m pytest -q automation/tests/test_w20_output_bound.py`(系统 python3 3.14.4 + pytest 9.1.1,同步、断网、scratch 全在仓库根)
- **改前红**(同一份测试放 main 仓跑,取证后已删):`ModuleNotFoundError: No module named 'automation.log_boundary'` → collection error
- **改后绿**:`8 passed in 8.15s`(封顶/轮转/淘汰记录/append 语义/水位门零副作用/env 覆盖/读数失败响亮抛/四处接线/supervisor 有界)
- **变异反证**:`follow_bounded` 轮转条件改 `if False and …` ⇒
  `test_writer_capped_with_rotation_and_markers` 在占位断言(总占位超上限)转红;还原后 8/8 复绿
- **既有测试迁移**(意图保持、锚点收紧,非放宽):4 处断言旧 Popen 直连形态的测试——
  `test_w7_security_exec_tier.py::test_switch_on_launches_with_exec` 迁移为「两次 Popen:第 1 次边界带
  `pass_fds`,第 2 次 worker stdout=int 管道写端 + `--permission exec` 不变」;w14/w15 三处同批自动过
  (水位门自适应修正后);开关关 ⇒ `popen.assert_not_called()` 原样保留且仍绿
- **全量回归**:`python3 -m pytest -q automation/tests` ⇒ **599 passed, 117 subtests passed in 15.34s**
  (基线同量;0 failed)

## 6. 未做到 / 降级 / 未验证

- 未做:无(派工书 §2 全部四问已覆盖)
- 降级:水位门读数不可得时响亮降级放行(与日顶预检既有口径一致,非本批引入)
- 未验证:真实 400 分钟级 worker 长跑下的轮转磨损(测试为分钟级);bwrap 沙箱内 `dev-trace-verify`
  复跑本测试文件已按契约约束设计(仓库根内 scratch、无网络、系统 python3),以质检方沙箱实测为准
- 边界进程自身崩溃 ⇒ worker 的 stdout 写入将得到 EPIPE(改前直连文件无此失败面);概率与处置:
  边界进程为 60 行 stdlib 读循环,异常即带账退出;若质检认为需进一步加固,可在后续批加边界进程
  存活监控(登记,不在本批扩权范围)

## 7. 跨仓依赖 / 移交项

- 蜂群仓零改动(本缺陷面全在公司仓派工层;执行面内部纪律本已存在)
- **移交用户**:①`batchW20-start/status` 两个 hermes once-job 建议在本批被质检收口后 disable
  (job 已由会话提前交付,避免明日 08:10 重复派发);②09-20 22:14 事故的另一待裁项
  "ws-cleanup 清理范围门"不在本批范围(已见 PLAN-QUEUE 登记)
- 活库 sha(收工读数,仅证本会话零写入窗口):`78d17dcd0270cb68`(WAL 库,sha 会随其他合法
  写入者漂移;本批代码与测试路径零 DB 访问)
- **不 push 声明**:全部提交留在于 `batchW20` 分支,等待质检方独立质检后收口
