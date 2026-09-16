# 质检报告 —— batchCap1(清墓碑死链 + v2 管道排练)

- 交付提交(公司仓库):**`5d33800`**(父 `9739e36`;8 文件,+678 −57)
- 派工书:`~/workspace/swarm-progress/batchCap1-prompt.md`;裁决依据 `DECISIONS.md` **D-16**;上游审查 `research/swarm-knowledge/docs/CAPABILITY-AUDIT-2026-09-16.md`(`8ecc067`)
- 质检方:Hermes 独立质检(自建探针 + 自跑回归 + 自读副本库;**未复用交付方断言/夹具**)
- 质检介质:`~/workspace/qa-cap1/`(`probe_guard.py` / `probe_security_guard.py` / `copy.db`)

## 1. 结论

**放行**。交付物 A(死链显式化)与 B(管道排练)均经独立取证成立;**未发现交付方缺陷**;3 项独立发现(1 中 2 低)均为**系统级既有缺口**,与本批改动无因果,登记供裁决。本批不 push(等用户指令)。

## 2. 独立验证(全部自跑)

| 项 | 我实测 | 交付方自报 | 一致 |
|---|---|---|---|
| 公司全量测试 | `427 passed, 77 subtests passed`(4.28s) | 427 / 基线 407 | ✅ |
| 护栏取真伪(自建 6 例) | **6/6 PASS**:不存在 / 墓碑目录 / **真 v1 归档库** / 非 sqlite ⇒ 拒;v2 活库 + 副本 ⇒ 收 | — | ✅(含交付方未测的"真 v1 库"向量) |
| 安全线护栏 | `submit_security` / `launch_runner` 均 `RuntimeError`,文案含「v1 已停用 … gray1-wip.patch」;helper 指向存在的 v1 文件时正确放行 | 同 | ✅ |
| 桥脚本 | `--dry-run` ⇒ `rc=3` + `WARNING: v2 KB 尚无条目(记忆层未实现…)`,**不再静默 0** | rc=3 | ✅ |
| promotion gateway | `--scan` ⇒ `rc=0` + 显式 `WARNING: v2 KB 尚无条目…`(空库合法无候选) | 同 | ✅ |
| 健康检查 | `rc=0`、0 异常;v1 墓碑标"已停用"而非 error | 同 | ✅ |
| 交付库零写入 | `swarm_v2.db` mtime 仍 **`2026-09-16 21:58:19`**(批运行 23:00–23:25)、`-wal` 0B;`runs=0 / tasks=0 / KB=0 / audit=仅 3 条 switch_toggle` | 未申报 | ✅ |
| 工作树纪律 | 收工 `git status` 与开工一致(他方 67 项未提交改动**未被触碰**);单 commit、无 amend | 同 | ✅ |

## 3. 交付物 A 的实质核查(不止"跑通测试")

1. **写类 repoint 的判据同构性——我独立复算**:`archive/swarm_knowledge.db` ↔ `swarm_v2.db` 的
   `knowledge_entries` **27 列逐字相同、11 索引、DDL sha256 相同**(`0bf97cc3d8f04129`);
   `raw_agent_events` **14 列逐字相同**。⇒ D-16.1「同构才允许 repoint」的分支判定**成立**。
2. **v1 migration 灌库风险——我做了主动反证**:把 `swarm_v2.db` 复制到 `qa-cap1/copy.db`,用公司脚本
   同一条 bootstrap 跑真实 `scripts/capture.py` ⇒ `CAPTURED:64ba2db6`,**表数 57→57、零新增表、零列变更**,
   仅新增 1 条 `knowledge_entries` + 1 条 `raw_agent_events`。⇒ 写路径**不会**把 v1 表注入 v2 库
   (原因:`capture.py` 的 `db.init()` 只在 `knowledge_entries`/`raw_agent_events` **缺表**时触发,而 v2 两者都在)。
   交付库全程未被该实验触碰(见 §2 末行)。
3. **`_classify_security_findings` repoint 无静默面**:该函数读 `agent_tasks.result_summary` 与
   `swarm_runs.conversation_summary` —— 两列在 v2 均存在(实测),repoint 不会退化为"恒空"。
4. **护栏 teeth**:非 v2 文件(真 v1 归档)、非 sqlite 文件、墓碑目录、缺文件四类全部拒绝并给原因。

## 4. 交付物 B(排练)独立复核

自读 `~/workspace/cap1-scratch/cap1-copy.db`(与交付库分离):

| 面 | 实测 |
|---|---|
| run | `cap1-content-run-1`(run_type=content) |
| task | `cap1-task-1` **completed**(token_cost=42)/ `cap1-task-2`、`cap1-task-3` failed ⇒ 成功与失败两臂都真实走过 |
| 审计 | `run_create`×1、`task_publish`×3、`judge_decision`×3、`switch_toggle`×2(仅副本库) |
| 性质 | stub executor(无模型)⇒ **plumbing 级**证明;交付方在报告 §6.5 已如实标注,并给出用户侧触发口径(命中即自动起 per-task worker,**无需常驻池**)—— 与我审查报告 D-16.4 一致 |

## 5. 独立发现(均为系统级既有缺口,非本批缺陷)

| # | 级别 | 现象与决定性证据 | 建议裁决 |
|---|---|---|---|
| **F-CAP1-1** | 中 | **v2 无 run 终态收口**:`grep -rin "swarm_runs SET" src/swarm_v2/` **= 0**(v1 侧曾有 `swarm_runner.py:145`);排练副本实证 run 仍是 `running` 而 3 个任务全部终态。影响:run 级读数(预算水位 V-5 / 健康检查"最近 run 无断点" / run 成败口径)永远看不到收口 | 补实现(或在 PRD 明写"run 不设终态"口径 —— 现状是**无人认领的空白**,非已裁决语义) |
| **F-CAP1-2** | 低 | **健康检查的表内绿**:"24h 内 0 条 completed" 被标 ✅(注释解释为"尚无真实流量")。真实流量开始后同样的 0 会**继续标 ✅** ⇒ 该检查在市场停滞时无法转红 | 补实现:区分 `N/A(无真实流量)` 与健康两态,或加"存在 run 才判活性"的前置 |
| **F-CAP1-3** | 低(预防) | **写类 repoint 的安全性依赖两表存在**:`capture.py` 以 `db.init()` 兜底(缺表即灌 27 个 v1 migration)。当前 v2 两表齐备 ⇒ 安全(已实测零注入);但 guard 只断言"是 v2",不校验"capture 所需表清单" | 补实现(小):guard 增加 capture 前置表断言,防未来 v2 schema 变更后静默灌 v1 表 |

## 6. 交付方自报的核对结论

- 「起点基线 407 → 收工 427」✅ 实测一致;新增 20 例(自跑全绿)。
- 「如实申报 6 项」中,最重要的一条**成立且纠了我的错**:审查报告 §1 记"桥脚本 rc=0 静默失败"——
  我那次的 `rc=$?` 取的是管道末段 `head` 的返回值;**HEAD 上缺库分支实为 rc=2**,真正静默的是
  `except sqlite3.Error` 只 print 的分支。已按实况修订审查报告(见 `docs/CAPABILITY-AUDIT-2026-09-16.md` §1)。
- 「健康检查 24h 检查降级为参考性 `_ok`」——交付方如实登记;**我判定该降级方向可接受但实现方式需改**(见 F-CAP1-2)。

## 7. 门禁声明

- 本批**不改变**任何 v2 开关/身份/灰度参数;真实流量仍未发生(活库 runs=0)。
- M1 七天门禁、E2/E3 真实窗口实验、E5 基线对比**仍不可评**(均因零真实流量)。
- 本批**未 push**;公司仓库 `5d33800` 与 swarm 仓库对应文档待用户指令后再推。
