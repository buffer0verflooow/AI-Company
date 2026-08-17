---
tags: [department, operations, responsibility]
created: 2026-08-12
source: strategy/company-operations-lessons-from-managed-agents.md (动作 1)
---

# 运行时责任矩阵 (Runtime Responsibility Matrix)

> 原则: 公司本身是一个"托管式 Agent 系统"。每个运营动作必须明确 [人]/[自动化]/[人审自动化]——边界画错, 规模与风险同时失控。自动化能跑的不要占人, 有外部副作用或不可逆的必须留人。

## 责任标记说明

- **[人]**: 必须人工执行/决策, 不可自动化(价值观判断、对外关系、最终发布)
- **[自动化]**: 脚本/Agent 全自动, 无人参与, 结果落盘可追溯
- **[人审自动化]**: 自动化产出 → 人工审批/抽查 → 才生效(外部动作、花钱、发布、删除)

## 一、内容产线 (content pipeline)

| 动作 | 责任 | 执行者 | 说明 |
|---|---|---|---|
| 选题登记 | [自动化] | 选题池文档 | 采集/雷达信号 → 选题池, 人去挑 |
| 选题决策(写哪篇) | [人] | 用户/主编 | 节奏+赛道+时机判断 |
| 素材包采集(evidence) | [自动化] | 主 Agent + Worker | 多源交叉验证落盘 |
| 撰写(draft) | [自动化] | content_hermes_executor → Worker | deepseek-v4-flash, 质量靠约束 |
| humanizer 去 AI 味 | [自动化] | Worker(humanizer skill) | 34 条自查 |
| QA 四门 | [自动化] | Worker | 事实核查/内容审校/主编终审/微信预览 |
| 排版(wechat 格式) | [自动化] | wrap_*_format.py | CSS 内联, fence 定位 |
| 封面生成 | [自动化] | Codex(读文章自由发挥) | 留 make_cover.py 可重跑 |
| 推送草稿箱 | [自动化] | wechat_push_v2.py | 进草稿箱, 非发布 |
| **发布到公众号** | **[人审自动化]** | 用户在草稿箱确认 → 公众号点发布 | 外部动作, 用户唯一授权点 |
| 已发布 → wiki + swarm KB | [自动化] | capture_from_obsidian.py + 桥 | 铁律: 技术文章必入库 |

## 二、情报与知识 (intel & knowledge)

| 动作 | 责任 | 执行者 | 说明 |
|---|---|---|---|
| 每日情报采集(20 源) | [自动化] | security_intel.py (cron 08:00) | 3 次重试, 单源失败容忍 |
| 赛道分类/日报 | [自动化] | security_intel.py | TRACK_RULES 引擎 |
| 选题建议 | [人审自动化] | 主 Agent 提炼 → 用户挑 | 机器人给候选, 人选 |
| 知识库 capture | [自动化] | 桥(cron 05:00) | swarm:capture 标记笔记 |
| 蜂群调研任务 | [自动化] | swarm_runner.py | 自建 executor, 不走 Hermes delegate |
| **调研结论给外部** | [人审自动化] | 用户审 → 主 Agent 发布 | 对外内容一律人审 |

## 三、公司运营循环 (company operator)

| 动作 | 责任 | 执行者 | 说明 |
|---|---|---|---|
| 运营证据扫描 | [自动化] | company_operator.py (cron 09:00) | 扫描新软件/文件/证据 |
| 机会队列生成 | [自动化] | company_operator.py | standing missions + 证据 → 队列 |
| **low 风险动作自动执行** | [自动化] | company_operator.py | auto_execute_risk_levels=["low"] |
| **中/高风险动作** | [人审自动化] | operator 生成审批 digest → 用户批 | approval_digest_items=3 |
| 动作结果 → 运营账本 | [自动化] | operator 回写 | TVCR 可评估 |
| 自动修复公司代码 | [自动化] | company-daily-auto-fix (cron 04:00) | Codex 修 + 安全预检 |
| 日报/周报 | [自动化] | daily digest / TVCR | 落盘 deliver=local |

## 四、财务 (finance) — 全部人审

| 动作 | 责任 | 执行者 | 说明 |
|---|---|---|---|
| 成本记录/结账 | [自动化] | finance_ledger.py | 记录 API 消耗 |
| **付费/充值/购买** | **[人]** | 用户 | 公司唯一花钱人 |
| 定价调整 | [人审自动化] | 建议 → 用户定 | pricing.py 只算建议 |

## 五、外部系统 (external) — 有副作用必人审

| 动作 | 责任 | 执行者 | 说明 |
|---|---|---|---|
| 微信推送(草稿箱) | [自动化] | wechat_push_v2.py | 草稿箱=沙箱, 可撤回 |
| **微信正式发布** | **[人审自动化]** | 用户在公众号手动发 | 外部动作 |
| GitHub 推送 | [人审自动化] | 用户批准 → git push | 改公开仓库前人审 |
| HackerOne 提交 | [人审自动化] | 用户审报告 → 提交 | 对外身份绑定 |
| 删文件/清库 | [人] | 用户明确指示 | 不可逆 |

## 六、基础设施 (infra)

| 动作 | 责任 | 执行者 | 说明 |
|---|---|---|---|
| cron 任务健康 | [自动化] | cron 状态 + 周查 | error 自动暴露 |
| cron 配置修改 | [人审自动化] | 主 Agent 提议 → 用户确认 | pin 模型防漂移 |
| 代码部署到 swarm-knowledge | [人审自动化] | 主 Agent 审查 → 用户点头 | 交叉仓库边界 |
| 环境/凭证 | [人] | 用户 | .env 不落文档 |

## 当前已知边界缺口

1. **content_hermes_executor 误报 failed**(时序): Worker max_turns 截断后主 Agent 收尾, executor 提前查产物 → 误判。修正: 产物存在+时间戳晚于 status → completed。**已修正 1 例 (8ce517a2)**, executor 本身未改(低优先, 人工兜底够用)。
2. **蜂群任务无明确"发布审批"点**: 蜂群跑 research 任务产报告, 但"报告对外用"与"报告内部用"边界没硬性区分——建议对外报告必经用户审(见二)。
3. **自动修复(cron 04:00)会改代码**: 有安全预检(company_auto_fix_guard.py), 但改的是公司自动化代码——风险等级建议标 [人审自动化] 若改动超出 lint/小修范围。

## 维护

- 新增自动化组件时, 在对应表格加一行并标注责任。
- 责任升级(自动化→人审)优先于降级; 拿不准就标 [人审自动化]。
- 每季度随战略评审过一遍, 检查"责任漂移"(本该人审的变成全自动)。
