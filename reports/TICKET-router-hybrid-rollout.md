# 工单：提交 hybrid 路由改造 + 启用 hybrid 灰度(设计 B 收尾)

**编号**: ROUTER-HYBRID-002(承接 ROUTER-HYBRID-001)
**创建日期**: 2026-07-30
**仓库**: `/home/pwn/workspace/company`(这是独立 git 仓库,不是外层 workspace)
**执行者**: opencode(完成 git 提交操作,不联网)

---

## 背景

ROUTER-HYBRID-001 已完成 hybrid 混合意图分类改造,改动在工作树未提交:
- `automation/company_router.py`(classify_with_fallback 加 router_mode 分支)
- `automation/router_config.json`(新增 `router_mode`、`hybrid_high_confidence_skip`)
- `automation/tests/test_company_router.py`(新增 HybridRoutingTests 8 例)

测试已验证:`cd /home/pwn/workspace/company && python3 -m unittest automation.tests.test_company_router -v` → 57/57 通过,全模块 229/229。

本工单负责**干净地提交**这些改动,并**启用 hybrid**。

## 用户已确认的两个决策

1. **范围 = 提交 + 同时启用 hybrid。** 分两个独立 commit,让 hybrid 启用可单独回退。
2. **配置卫生 = 把 `dispatch_security`/`auto_run_security` 还原为 HEAD 的 `true`。** 这两行是与本次无关的既有脏改动;还原后它们与 HEAD 一致、不进入任何 commit,使 router_config.json 的 diff 只含路由改动。
   ⚠️ 注意:还原为 `true` 会重新开启安全自动派发——这是用户明确要求的,照做即可,不要擅自改回 false。

## 绝对禁止(CRITICAL)

- **禁止 `git add -A` / `git add .` / `git commit -a`。** 只能按下面显式列出的路径 `git add <具体文件>`。
- **禁止暂存或提交以下既有脏文件**(与本次无关,mtime 证明是先前产物):
  - `operations/README.md`
  - `wiki/swarm-strategies.md`
- **禁止提交工单文件本身**(`reports/TICKET-router-hybrid-classification.md`、`reports/TICKET-router-hybrid-rollout.md`)——保持 untracked。
- **禁止 `git push`。** 只在本地提交。
- **禁止改动 `company_router.py` / `test_company_router.py` 的现有逻辑**——它们已通过测试,本工单只做提交,不改代码。

## 执行步骤

### 步骤 0：核对现场
```bash
cd /home/pwn/workspace/company
git status
git branch --show-current
```
确认处于 company 仓库、能看到上述 3 个改动文件 + 2 个既有脏文件。在当前分支上提交(沿用本仓库既有的直接提交惯例,不新建分支)。

### 步骤 1：还原无关脏改动
把 `automation/router_config.json` 里这两行改回 `true`(还原到 HEAD 值):
```
"dispatch_security": true,
"auto_run_security": true,
```
改完后确认:`git diff automation/router_config.json` 只剩 `router_mode`、`hybrid_high_confidence_skip` 两处新增(此时 router_mode 仍应为 `"keyword"`)。

### 步骤 2：跑测试确认绿
```bash
python3 -m unittest automation.tests.test_company_router -v
```
必须 57/57 通过。不绿则停止并报告,不要提交。

### 步骤 3：Commit A —— 特性提交(router_mode 保持 keyword)
只暂存这 3 个文件:
```bash
git add automation/company_router.py automation/tests/test_company_router.py automation/router_config.json
git status   # 再次确认暂存区里没有 operations/README.md、wiki/swarm-strategies.md、工单文件
```
提交信息:
```
feat: hybrid intent classification for company router (opt-in)

将隔离 LLM 分类器从"仅 0.45 兜底"升级为 hybrid 模式下的模糊带主判据。
新增 router_mode 开关(默认 keyword=行为不变、可回退)与 hybrid_high_confidence_skip(0.86)。
LLM 只出候选 route,仍强制过 classify_message,scope 授权/外部动作审批门不变。
新增 HybridRoutingTests 8 例;router 57/57、全模块 229/229 通过。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
```

### 步骤 4：Commit B —— 启用 hybrid 灰度
把 `automation/router_config.json` 的 `router_mode` 由 `"keyword"` 改为 `"hybrid"`:
```bash
git add automation/router_config.json
git commit
```
提交信息:
```
chore: enable hybrid router_mode (gray rollout)

线上启用 hybrid 意图分类。可秒级回退:将 router_mode 改回 "keyword" 即恢复旧行为。

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
```

### 步骤 5：终检
```bash
git log --oneline -3
git show --stat HEAD~1 HEAD    # 确认两个 commit 只碰了预期文件
git status                     # 确认 operations/README.md、wiki/swarm-strategies.md 仍是 untracked/unstaged 的既有脏状态,工单文件仍 untracked
```

## 验收标准

- 恰好 2 个新 commit,`git show --stat` 显示:
  - Commit A 只含 `automation/company_router.py`、`automation/tests/test_company_router.py`、`automation/router_config.json`。
  - Commit B 只含 `automation/router_config.json`(单行 router_mode 改动)。
- `operations/README.md`、`wiki/swarm-strategies.md` **未被提交**,仍保持先前脏状态。
- `router_config.json` 最终:`router_mode="hybrid"`、`hybrid_high_confidence_skip=0.86`、`dispatch_security=true`、`auto_run_security=true`。
- 未 push。
- 用中文简要报告:两个 commit 的 hash 与 stat、测试通过数、git status 收尾情况。
