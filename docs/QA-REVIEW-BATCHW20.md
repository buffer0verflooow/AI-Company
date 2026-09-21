# QA-REVIEW-BATCHW20 —— worker 落盘输出必须有一道无界之墙

质检方:Hermes(自建探针 `~/workspace/qa-w20/probe_w20.py` + 变异 harness `mutate_w20.py`,**不复用交付方任何断言/夹具**)
被检:公司仓 分支 `batchW20`(worktree `/home/pwn/w20-worktree`),起点 `bbdfd4e`,交付 3 commits
(`6e6b26f` 代码 / `1d7e1af` 测试 / `0be2c4a` 报告),冻结 HEAD `0be2c4a`,工作树 clean
日期:2026-09-21

## 1. 结论:**放行**(自建探针 23/23、变异 RED 4/4、全量 599 passed/117 subtests、改前 collection error;**1 项登记级发现**见 §6)

## 2. 冻结

`git rev-parse HEAD` = `0be2c4a`;`git status --short` 空;6 个变更文件逐文件 sha256 存
`~/workspace/qa-w20/frozen.sha256`。

## 3. 自建探针 23/23 PASS

| 面 | 我的向量 | 实测 |
|---|---|---|
| 静态接线 | router/supervisor 里 `stdout=log_fh` 与 `open("a")` 残留 **0**;`spawn_bounded` 调用点 **5 处**;四线 `_disk_headroom_precheck` 均在 `v2 run create` **之前**(逐函数体正则定位,非全文计数) | 5/5 PASS |
| 有界(实测,非读代码) | 8KB×flush 无界 writer 走 `spawn_bounded`:`max_file_bytes=1MB / max_files=2`、0.5s 写出 **1.64GB** ⇒ 磁盘总占位 **2,646,419 B ≤ 上限 3,266,240 B**;文件数 3 ≤ 3;轮转 **1597 次**;当前文件持**最近字节**(worker 末行在内);淘汰留痕 `evicted=… dropped_total=…` 在轮转后文件;EOF 终态含 `total_bytes/rotations/dropped_bytes`;`dropped_bytes=1,635,901,690 > 0`(证明确有丢弃而非"全留了");除日志外零额外落地文件 | 8/8 PASS |
| worker 终态不被边界改变 | `os.waitpid` 取到 **exit code 0** | 1/1 PASS |
| append 语义 | 旧内容保留 + 新内容追加(非截断重写) | 1/1 PASS |
| 水位门 | 余量不足 ⇒ `DiskHeadroomError` 且**目标目录零残留**(未建目录/未建文件/未起进程);读数不可得 ⇒ **`OSError` 原样上抛**(不静默放行);自适应下限 = `min(20GiB, 盘总量5%)`(982GiB 盘 ⇒ 20.0GiB) | 3/3 PASS |
| Popen 语义逐字保留 | `stdin=DEVNULL` / `stderr=STDOUT` / `start_new_session=True` / `close_fds=True`;worker `stdout` = **int 管道写端**(不再是文件对象);边界进程 = 独立 detached(新会话 + `close_fds` + `pass_fds=(r,)`);worker 起不来 ⇒ **异常上抛**(不假装成功) | 3/3 PASS |
| 边界进程生命周期 | worker 收尾后自身退出,**无残留进程** | 1/1 PASS |

## 4. 变异反证 RED 4/4(独立变异 → 探针 → 还原 → sha 校验)

| 变异 | 实测 |
|---|---|
| 轮转条件短路 `if False and size >= max_file_bytes:` | **RED**:文件数 1、占位 1,638,400,204 ≫ 上限、淘汰留痕无、`dropped_bytes=0` |
| 水位门短路 `if False and free < limit:` | **RED**:余量不足时不再拒绝 |
| 边界进程 `start_new_session=False` | **RED**:detached 断言 |
| 去淘汰留痕(`current.write(b'')`) | **RED**:淘汰留痕项 |

四条均 `sha还原=OK`。

## 5. 交付方自报数字逐项核对

| 自报 | 我的实测 | 判定 |
|---|---|---|
| 全量 599 passed / 117 subtests | worktree **599 passed, 117 subtests, 0 failed(23.11s)**;**落 main 后主树再跑一次同样 599/117(22.38s)** | ✅ 两次一致 |
| 改前红 = collection error | 起点 `bbdfd4e` 上放同测试 ⇒ `ModuleNotFoundError: No module named 'automation.log_boundary'`,**collection error** | ✅ |
| 改后 a) 3 秒 8.7GB → 占位 332MB | 我独立复测(0.5s 写 1.64GB)⇒ 占位 2.65MB / 上限 3.27MB,**同一结论**:落盘量与写入量解耦,总量硬封顶 | ✅ 同向 |
| `grep 'stdout=log_fh'` 全仓 0 处 | 静态核对:**0 处**(router + supervisor) | ✅ |
| 活库 sha `78d17dcd0270cb68` | 本批代码/测试路径零 DB 访问(我核对改动面:无 sqlite 调用);该读数为会话窗口读数,未复算 | ⚠️ 未复算(不影响结论) |

## 6. 登记级发现(不阻断放行,建议裁决)

**新语义下"父目录不存在"的行为与改前不同**:`spawn_bounded` 先 `assert_disk_headroom(log_path.parent)`
**再** `mkdir(parents=True, exist_ok=True)`;而改前 `default_launch` / 六处派工块是**先 `mkdir(parents=True)`
再 `open("a")`**。实测:传一条**父目录链全部不存在**的 `log_path` ⇒ `spawn_bounded` 抛
`FileNotFoundError: …/a/b/c`(改前形态会自己把目录链建出来)。

- **现值可达性**:低。content 线 `job_dir.mkdir()` 在派工前已建;四条 v2 线 `log_dir` 来自
  `router_config.json` 且当前存在;supervisor 路径的失败形态是**响亮的** `rc=3 + reason`(不是静默)。
- **触发面**:全新部署 / 日志目录被清理后重建(2026-09-20 事故里工作区正是被整体删除过)⇒ pool 拉起会失败;
  与 `swarm_pool_supervisor.py:default_launch` 原注释"失败上抛,绝不假装成功"一致,属**响亮失败**而非静默降级。
- **建议裁决**:二选一 —— ①本批返工:把 `mkdir(parents=True)` 提到 `assert_disk_headroom` 之前(或对
  `FileNotFoundError` 走 mkdir 重试);②登记为后续批(不改则部署说明里要写明"log_dir 必须先存在")。
  我倾向 ①(一行改动,且与"零副作用"不冲突:建目录发生在水位门**之后**会导致门失效,故应把门改成
  "先按需建目录、再测水位")。**本条不构成放行阻断**:交付面在生产现值上全部成立。

## 7. 未覆盖负空间

- **边界进程自身被 kill ⇒ worker 写入得 `EPIPE`**(改前直连文件无此失败面):交付方已在报告 §6 自报,
  我确认该面**未加固**(无存活监控)。属新增失败面的如实登记。
- **400 分钟级长跑轮转磨损未验**(我的实测为秒级,交付方为分钟级)。
- 未做**真实公司派工单**的落盘演练(本批不启开关/不起常驻);未验 `SWARM_LOG_*` 三个环境变量在常驻
  supervisor 继承链上的取值来源(单测调用时读值已在探针里覆盖)。

## 8. 门禁声明

本批**无需开关**即生效(改的是派工落盘路径本身)⇒ 与"里程碑验收是否因本批可评"无关:
W20 是 09-20 磁盘事故的**根因修复**项,验的是"落盘有界 + 水位门零副作用 + 失败语义不变"三件事,三者均实测成立。
唯一需用户拍板的是 §6 的父目录语义(建议返工一行,或接受并把前置条件写进部署说明)。

## 9. 质检方自身错误登记

1. 变异 harness 首轮锚点写成 `pass_fds=(r,)).`(实际 `pass_fds=(r,))`)⇒ `AssertionError` 中断,后两变体未跑;
   修正后补齐,均 RED。
2. 无探针自身假红:23/23 与 4 条变异在**未改动副本**上先 PASS 再用于指控(变异 RED = FAIL 数严格大于基线 0)。
