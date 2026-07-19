---
tags: [operations, autonomy, governance, continuous-operation]
created: 2026-07-15
updated: 2026-07-15
---

# 公司自治运行闭环

> 自治不是取消用户治理，而是让公司在既定目标和授权边界内持续发现、选择、执行和复盘工作。

## 运行闭环

```text
外部市场雷达 + 长期经营任务 + 运行证据 + TVCR 提案
                 ↓
            机会队列与评分
                 ↓
      低风险内部事项自动执行 ──→ 经营账本 ──→ TVCR
                 ↓
      发布/付款/删除/外部安全动作请求审批
```

每日经营者由 `automation/company_operator.py` 实现。每个周期执行：

1. 从待审批 TVCR 提案、已批准运营实验、缺少业务结果的 Run 和长期经营任务中发现机会；
2. 使用稳定幂等键写入机会队列，避免同一事项重复派发；
3. 按优先级、经营评分、风险和审批状态选择最多一个任务；
4. 在隔离目录中调用内部 Worker，要求产生实际交付物；
5. 把运行、Token、工具调用、成本状态和产物写回经营账本；
6. 向管理会话主动发送完成摘要和最多三个决策项。

市场发现由 `automation/market_radar.py` 在经营者之前运行。搜索结果不会直接成为任务；只有通过隐私门、内容风险检查、主题资格词和至少两个独立来源佐证的 `market_pulse` 才能入队。经营 Worker 还必须实际打开至少两个源文，并分别记录正文核验、元数据核验、仅可达和搜索记录级信号。

同主题已评估机会默认冷却 168 小时；只有脉冲评分较上次至少提高 8 分才会提前重开。新脉冲替代旧脉冲时，对应旧开放任务会自动 dismiss，避免公司围绕同一市场噪声反复空转。

## 自治授权边界

默认可以自动执行：

- 读取公司 Wiki、DASHBOARD、TRACKING、经营和财务摘要；
- 在独立运行目录生成分析、Brief、清单、草稿、证据索引和执行包；
- 启动已经由用户批准的运营实验的内部准备部分；
- 发现停滞、结果缺失和审批阻塞并主动提醒。

默认禁止自动执行：

- 公开发布、上传或向第三方发送内容；
- 付款、采购、删除和不可逆修改；
- HackerOne 提交、联系外部人员；
- 主动扫描、利用或探测外部目标；
- 读取或复制秘密、个人数据和未披露漏洞正文；
- 修改公司现有代码、数据库、正式文档或配置。

需要越界时，Worker 必须生成 `approval-request.md`，说明动作、影响、风险和回滚方式，然后停止。

## 运行资产

| 资产 | 位置 |
|------|------|
| 配置与长期经营任务 | `automation/company_operator_config.json` |
| 机会队列 | `operations_control.db` → `autonomy_opportunities` |
| 运行周期 | `operations_control.db` → `autonomy_cycles` |
| 自治执行记录 | `operations_control.db` → `autonomy_runs` |
| 隔离产物 | `operations/runtime/autonomy-runs/<run_id>/` |
| Cron 入口 | `automation/hermes_company_operator.py` |
| 市场信号账本 | `marketing/market_signals.db` |
| 市场雷达产物 | `marketing/runtime/market-radar/<run_id>/` |
| 市场雷达入口 | `automation/hermes_market_radar.py` |

自治 Run 同时进入 `operational_runs`，`source_type=autonomy`。技术完成仍不等于经营成功；采用、节省时间、产生触达或收入等真实结果仍需证据化记录。

## 手动运行

```bash
cd /home/pwn/workspace/company

# 只发现、排队和汇报，不执行 Worker
python3 automation/company_operator.py --plan-only --no-delivery

# 执行一个完整周期
python3 automation/company_operator.py

# 查看当前活动队列
python3 automation/company_operator.py --queue
```

正式计划为每天 `09:00` 运行，单周期最多自动执行一项任务。调整长期任务、风险级别或单周期上限应修改配置并运行自动化测试。
