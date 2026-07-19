---
tags: [department, operations]
created: 2026-07-04
updated: 2026-07-15
---

# 🔧 运营部

## 当前状态

无专职运营人员，运营工作由 Hermes Agent 编排 AI 代理池完成。

## 管理资产

### 业务产线

- [[business-lines/README|🏭 业务产线]] — 公司的内容生产管线
  - [[business-lines/article-production|📝 文章创作分享]] — 🟢 运营中
  - [[business-lines/video-production|🎬 视频创作]] — 🟡 筹建中
  - [[business-lines/security-exploration|🛡️ 安全探索与赏金]] — 🟢 运营中

### AI 代理池

- [[agent-roster|🤖 AI 代理池]] — **233 个专家代理**，按 12 个分部分布（engineering / security / marketing / product / sales / design / specialized / spatial-computing / game-development / project-management / finance / strategy）
- 调用方式：`agency_agents_search` → `agency_agents_delegate` 委派任务
- 代理归属：按专业映射到各公司部门，由各部门 README 记录活跃代理

### 基础设施

- **Obsidian 知识库** — `~/workspace/company/`，公司数字资产管理
- **Hermes Agent** — AI 工作流编排中枢
- [[projects/wechat-publisher/README|微信公众号发布工具]] — 自动化推送
- **Agency Agents** — 插件路径 `~/.hermes/plugins/agency-agents-router`
- **蜂群运行内核** — `/home/pwn/workspace/research/swarm-knowledge`
- **安全技能库** — `/home/pwn/workspace/research/recon-skills`
- [[tvcr-governance|TVCR 经营治理闭环]] — 经营账本、每日评估、用户审批、运营实验与结果复核
- [[autonomous-company|公司自治运行闭环]] — 主动发现机会、排定优先级、执行低风险内部任务并请求必要审批

## 关联项目

- [[projects/ai-edu-series/TRACKING|AI工程从零开始]]
- [[projects/article-curation/TRACKING|文章精选阅读]]
- [[projects/security-exploration/README|安全探索产品线]]

## 近期事项

- [ ] 建立内容发布 SOP（含质检流程）
- [ ] 制定多平台同步策略
- [ ] 建立数据监控（阅读量、粉丝增长、互动）
- [x] 建立产品线运行投入账本与每日 TVCR 评估基础设施
- [ ] 持续补录采用、发布、触达、收入、知识复用与合规结果
- [ ] 每周审核运营实验，每月决定产品线资源配置
