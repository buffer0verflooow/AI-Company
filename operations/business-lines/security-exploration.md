---
tags: [operations, business-line, security, bug-bounty, swarm]
created: 2026-07-15
updated: 2026-07-15
status: active
---

# 🛡️ 安全探索与赏金产线

> 在明确授权范围内，以蜂群式探索、独立验证和知识复用开展漏洞赏金与安全研究，并向公司内容业务提供经过披露审核的原创素材。

## 产线定位

该产线是当前工作站的主要业务功能，也是公司第一条具备直接现金收入潜力的产品线。

产出分为三类：

1. **赏金交付**：可复现的 HackerOne 报告和实际赏金收入。
2. **私有知识**：目标信息、探索轨迹、失败路径、验证证据和可复用攻击模式。
3. **公开内容候选**：在漏洞修复、披露许可、脱敏和人工审批后，转化为文章、视频和课程。

## 本机运行资产

| 资产 | 本机路径 | 作用 | 数据级别 |
|------|----------|------|----------|
| 蜂群知识与编排 | `/home/pwn/workspace/research/swarm-knowledge` | 任务市场、Controller/Worker、DIKW、验证门禁 | 内部 |
| 安全技能库 | `/home/pwn/workspace/research/recon-skills` | 侦察、验证、报告 Skill | 内部 |
| HackerOne 项目 | `/home/pwn/workspace/hackerone` | 项目证据、报告、Submission | 严格隔离 |
| 编排脚本 | `/home/pwn/workspace/scripts` | 阶段执行与远程节点管理 | 内部 |
| 二进制结构恢复 | `/home/pwn/workspace/research/RecStruct` | 逆向与结构恢复研究 | 内部 |

## 工作流

```text
授权与 Scope 确认
→ 资产发现与技术指纹
→ 蜂群并行探索
→ 漏洞假设
→ 独立验证门禁
→ 报告与证据校验
→ HackerOne 提交
→ 实际结果与负面知识回写
```

公开内容走独立支路：

```text
已修复/允许披露的研究
→ 删除客户、账号、Token、目标细节
→ 合规与事实审核
→ 内容 Brief
→ [[article-production|文章产线]]
→ [[video-production|视频产线]]
```

## 知识边界

- 未披露漏洞不得进入公司公共 Wiki、文章项目或视频提示词。
- 原始响应、APK、凭证、用户数据和 PoC 只保存在项目隔离区。
- 可提升为公司知识的是通用方法、工具经验、失败模式和已公开案例。
- 公司市场研究可以提供研究主题，但不能扩大授权目标范围。

## 核心指标

| 类别 | 指标 |
|------|------|
| 收入 | 实际 accepted / paid 赏金，不使用估算金额作为收入 |
| 质量 | 有效发现率、误报率、独立验证通过率 |
| 效率 | 每个有效发现的 Token、时间和模型成本 |
| 知识 | 历史知识复用率、重复探索减少率、负面知识命中率 |
| 内容 | 合规转化文章/视频数、从披露到发布的周期 |
| 安全 | Scope 违规数、敏感信息泄漏数，目标均为 0 |

## 当前状态

- 蜂群运行核心已存在并通过近期架构修复验证。
- 已有多个 HackerOne 项目、Submission 和验证记录。
- 公司知识库已在本机部署，并完成产品线登记。
- Hermes 公司 Router 已接入，安全任务可自动创建 Swarm Run 并由 Worker 执行。
- 已完成真实微信 E2E：3 个任务全部完成，失效 Runner 自动恢复，结果主动推送并镜像回原会话。
- 安全知识晋升网关已上线：首次扫描 66 条 active 知识，64 条敏感阻断、2 条待验证、0 条自动进入 Wiki。
- Research 结果可自动进入文章/视频内部生产任务；外部发布仍需人工审批。
- 财务分账已建立：当前实际收入为 $0，报告估值只进入 forecast；61 个 Hermes 会话仍保持 unpriced。
- ZenMux / OhMyGPT 共 9 条可核验报价已进入公司财务价格目录；下一阶段是将目录接入 Hermes 历史会话重算，并补录真实 accepted/paid 凭证。
- HackerOne GraphQL 无鉴权资产发现方法已记录：`../../projects/security-exploration/h1-graphql-api-reference`；对应 Hermes Skill: `hackerone-graphql`。

## 关联

- [[../../projects/security-exploration/README|产品线项目页]]
- [[../../projects/security-exploration/h1-graphql-api-reference|H1 GraphQL API 参考]]
- [[../README|运营部]]
- [[../../strategy/market-demand-analysis|AI+安全市场需求分析]]
- [[article-production|文章产线]]
- [[video-production|视频产线]]
