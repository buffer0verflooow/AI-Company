# 质检报告 —— 跨仓库灰度接入 1(内容线接入 v2 蜂群)

- 交付提交(公司仓库):**`865a31d`**(父 `f5febfe`;4 文件,+1331 −42)
  - `automation/content_hermes_executor.py`(+255):`--stdin-json` 契约模式(与 `--job-dir` 并存)
  - `automation/company_router.py`(+456):内容派发 v2 灰度分支 + 纵深默认闸 + v2 CLI/worker 构造
  - `automation/tests/test_content_swarm_v2.py`(新增 31 用例);`test_swarm_integration.py`(过期 v1 断言修正)
- 前置配置键提交:`f5febfe`(我落:`swarm_v2_db` + `swarm_v2_gray{enabled:false,...}` + 空 agent/judge;既有键未动)
- 质检介质:`~/workspace/qa-gray2/`(`probe_gray2.py` / `mutate_gray2.py` / `r1-r3.out` / `mut*.out` / `company-tests.out`)
- 质检方:Hermes 独立质检(自建探针 + 变异反证 + 自跑公司测试;**未复用交付方断言/夹具**)

## 1. 结论

**放行**(默认关、零行为变更成立;5 项告警见 §5,均非阻塞)。

## 2. 独立验证

| 项 | 结果 |
|---|---|
| 公司仓库全量测试(我自跑) | **403 passed / 77 subtests passed / 0 failed**(基线 371 passed + **1 既有失败** = 过期 v1 断言,本批修正后归零) |
| 独立探针 | **28 PASS / 0 FAIL** |
| 变异反证 | **RED 5 / GREEN 0 / INVALID 0**(基线门 28/0) |
| 默认行为未变 | 真实配置件载入 ⇒ 灰度判定不命中(`reason=v2_gray_disabled`),且**未触发任何 v2 CLI**(bomb 计数 0) |

## 3. 探针覆盖面(28 项,自建;离线,不调 LLM、不触真实 job 目录)

- **S1 灰度默认闸(11)**:默认关 / `run_types` 空 / `ratio=0` / `run_type` 不在灰度集 / 命中(全开)/ **缺 `swarm_v2_gray` 整块 ⇒ 默认关** / **块内缺 `enabled` 键 ⇒ 默认关** / **纵深闸:db 未配置、agent 未配置、judge 与 agent 相同(自判)** —— 逐条给出 `v2_*` 原因;非内容线任务不适用。
- **S2 token 读数(4)**:空 usage ⇒ `None`(**不填 0**)/ 有读数 ⇒ 真实求和 / 非法入参 ⇒ `None`(不抛不猜)/ 源码面:仅非 `None` 才写 `token_cost`。
- **S3 stdin 契约(6)**:stdout = **单行** JSON 且含 content / **usage 读不到 ⇒ 无 `token_cost` 字段(非 0)** / job 目录 + `request.json` 落盘 / 执行失败 ⇒ **非零退出**(让 v2 判 rejected)/ **非法 task_id(路径穿越)⇒ 拒且零越界写入** / 缺 route ⇒ 明拒。
- **S4 legacy 不回归(3)**:`--job-dir` 模式仍在且退出码语义不变(=0)/ **不写 stdout JSON 契约**(两路径互不污染)/ 两参数皆缺 ⇒ argparse 报错。
- **S5 派发点(3)**:先取灰度判定再分支(顺序不可倒)/ 默认关下零 v2 CLI / 并发闸常量被内容派发点引用(v2 路径同受约束)。

## 4. 变异反证:RED 5 / GREEN 0 / INVALID 0

| 变异 | 结果 |
|---|---|
| `M1` 灰度默认关失效(缺省 `enabled=True`) | RED(触发 S1a/S1b 两条缺键 fail-closed 用例) |
| `M2` token 未测伪造为 0 | RED(触发 S2.1/S2.4) |
| `M3` 纵深默认闸放行(agent 未配置) | RED |
| `M4` 路径守卫失效(任 task_id 拼路径) | RED |
| `M5` 自判闸删除(agent == judge 放行) | RED |

**过程修正**:首轮 `M1` 曾 GREEN,根因是**我探针未覆盖"配置块/键缺失"场景**(交付方改动只在"键缺失"时生效)。已增补 S1a/S1b 两条 fail-closed 用例,M1 转 RED。

## 5. 告警与既有事实(非阻塞,供裁决/后续)

1. **双重执行极小概率**(交付方如实申报):`market publish` 成功后若 v2 worker 启动即失败,任务留在市场 pending **同时**回退原路径 ⇒ 理论上同一任务两处执行。取舍是"宁可不丢任务"。建议启用前明确:首发期间以人工复核为准,或后续加"发布成功 ⇒ 不再回退"的补偿阀。
2. **`SWARM_CLIENT_SALT` 未设 ⇒ 灰度命中却总回退**:发布走 `--publisher client`,v2 侧 fail-closed 要求盐;启用前必须显式设置,否则表现为"开了但没进市场"。
3. **`token_cost` 口径**:取 usage 计数求和,是否与 v2 `total_tokens` 完全同构待裁决(属"不做第三件事"的 `operations_control.py` 口径统一范畴)。
4. **口径依赖**:stdin 模式解析 `content_job_dir` 依次取 `COMPANY_CONTENT_JOB_DIR` → `automation/router_config.json`;若以 `--config <其他路径>` 覆盖且未设环境变量,契约会拒绝(不会误写)。
5. **批 1 归档代码与本次同名函数冲突风险**:归档 `~/workspace/swarm-progress/archive-gray1/` 中的安全线分支含同名 `v2_gray_config/v2_swarm_command` 等;若日后落库需人工合并(交付方已申报,确认属实)。
6. 本环境无 `ruff`,未跑 lint(交付方申报);公司测试全绿。

## 6. 复跑命令

```bash
python3 ~/workspace/qa-gray2/probe_gray2.py        # 28 PASS / 0 FAIL
python3 ~/workspace/qa-gray2/mutate_gray2.py       # RED 5 / GREEN 0 / INVALID 0
cd /home/pwn/workspace/company && python3 -m pytest -q automation/tests   # 403 passed / 0 failed
```
