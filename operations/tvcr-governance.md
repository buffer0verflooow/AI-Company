---
tags: [operations, tvcr, governance, continuous-improvement]
created: 2026-07-15
updated: 2026-07-15
---

# TVCR 经营治理闭环

> TVCR 是独立经营评估角色。它分析 Time、Value、Cost、Risk，但不直接修改生产系统。

## 两条运行线

```text
业务运行线：目标 → 投入 → 产线执行 → 交付 → 市场/用户结果 → 经营账本
                                                      ↓
经营治理线：TVCR 评估 → 经营提案 → 用户审批 → 运营实验 → 复核结果
                                             ↓
                         业务 / 产品 / 流程 / 资源 / 技术落地

自治运行线：长期任务 / 停滞信号 / 已批准实验 → 机会队列 → 有边界执行 → 经营账本
```

技术日志只是经营证据的一部分。高 Token、高耗时或失败率不能直接推出“修改代码”；必须先判断交付物是否被采用、发布、触达用户、产生收入或形成可复用资产。

## 数据资产

| 资产 | 位置 | 作用 |
|------|------|------|
| 技术路由账 | `operations/runtime/company_router.db` | 路由、状态、错误、回传、自愈 |
| 财务证据账 | `finance/finance_ledger.db` | 实际收支、预测、已确认成本 |
| 经营账本 | `operations/runtime/operations_control.db` | 产品线投入、产出、TVCR 提案、实验与复盘 |
| 每日证据包 | `operations/runtime/tvcr-reviews/YYYY-MM-DD/evidence.json` | 前一经营日的可核验数据 |
| TVCR 报告 | 同目录 `tvcr-report.md` | 经营分析与数据缺口 |
| 待审批提案 | 同目录 `proposals.json` | 结构化经营改进选项 |

## 经营记录原则

每次产品线任务自动归集以下投入：

- 运行时间、模型、输入/输出/推理 Token、缓存 Token、工具调用；
- 已确认成本与未定价状态；
- 产物、质量门和运行状态；
- 原始运行证据位置。

业务结果不能由系统猜测，需在结果出现后补录：

- 是否被用户采用；
- 是否发布；
- 触达、线索、收入或其他价值指标；
- 人工处理时间、返工和结果说明。

```bash
cd /home/pwn/workspace/company
python3 automation/operations_control.py sync-runs
python3 automation/operations_control.py record-outcome <run_id> \
  --accepted yes --published no --value-score 4 --human-minutes 10 \
  --notes "用户采用，等待排期发布"
```

未知模型价格必须保持 `unknown/unpriced`，不能按零成本计算。缺少采用或触达数据时，TVCR 必须写明“无法判断 ROI”。

## 每日复盘

每日 `00:30` 对前一自然日执行：

1. 同步技术运行证据到经营账本；
2. 生成按产品线汇总的证据包；
3. TVCR Agent 从业务、产品、流程、资源到技术逐层分析；
4. 只生成报告与提案，不修改生产系统；
5. 向用户发送精简日报：一句结论、最多 3 个待决策项；完整证据留在运营目录。

手动执行：

```bash
python3 automation/tvcr_daily_review.py
python3 automation/tvcr_daily_review.py --date 2026-07-15
```

## 审批与运营实验

报告中的提案具有稳定 ID，例如 `TVCR-P-20260715-01`。用户可回复：

```text
批准 TVCR-P-20260715-01
拒绝 TVCR-P-20260715-02
批准第1项
```

批准后系统只创建 `planned` 运营实验，不直接生成代码任务。公司主 Agent 应按以下顺序落实：

1. 明确业务目标、服务范围或停止项；
2. 调整产品分级、质量标准和成功指标；
3. 调整 SOP、审批门和停止条件；
4. 调整 Agent、模型、人员和预算分配；
5. 仅在前述决策需要系统固化时修改 Prompt、配置或代码。

实验状态：

```text
planned → running → evaluating → succeeded / failed / stopped / rolled_back
```

```bash
python3 automation/operations_control.py experiment <experiment_id> running
python3 automation/operations_control.py experiment <experiment_id> evaluating
python3 automation/operations_control.py experiment <experiment_id> succeeded \
  --result-json '{"direct_tokens_change":"-35%","acceptance_change":"0%"}' \
  --conclusion "保留分级流程"
```

## 节奏

- 实时：严重失败、数据或合规风险告警；
- 每日：运行异常、数据缺口和候选经营提案；
- 每周：评估运营实验，决定保留、调整或回滚；
- 每月：评估产品线继续、暂停、合并和资源再分配。

## 治理边界

- TVCR Agent 与生产 Agent 分离，生产者不能独立评价自己的 ROI；
- 没有用户批准，提案保持 `pending_approval`；
- 经营实验批准不等于批准公开发布、付款、删除或外部安全测试；
- 任何技术变更仍需测试、灰度和回滚；
- 实验结束必须记录真实结果，不能以“代码已修改”代替经营成功。
- 每日经营者可以自动启动低风险内部任务和已批准实验的内部部分，但不能绕过发布、付款、删除或外部安全动作审批。
