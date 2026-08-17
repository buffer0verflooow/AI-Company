# AI 安全方向评估 — 上下文包（2026-08-08）

> 供 Claude Code 独立评估使用。评估对象：用户个人公司（~/workspace/company）是否应把 AI 安全作为战略方向，切入哪个细分，用什么形态（内容/工具/服务）落地。

## 一、市场信号（公司市场雷达 2026-08-08，MKT-RUN-985fde8a36f4）

### 强脉冲 1：企业 AI 智能体安全治理需求（agent-security-demand）
- 分数 80.36，置信度 0.8，独立来源 7，信号数 11，状态 `new`
- 代表性信号：
  - marketscale.com: Three fault lines reshaping enterprise AI in 2026: adoption, cost, security
  - CISA/NSA/Five Eyes 联合公告（agentic AI）via x.com
  - persistencemarketresearch.com: AI Governance Market Size & Share, Growth Trends to 2033
  - evolvancemarketresearch.com: AI Governance Statistics 2026
  - labs.cloudsecurityalliance.org: The AI Agent Governance Gap: What CISOs Need Now
  - 知乎/悬镜问境: AI 驱动全周期智能体安全治理
  - ISC.AI 2026: 智能体时代安全治理从共识走向行动
  - Reddit: Best AI Agentic security tools for AI company?

### 强脉冲 2：中文 AI 安全内容与产品选题需求（ai-security-content-demand）
- 分数 74.12，置信度 0.771，独立来源 7，信号数 8，状态 `new`
- 代表性信号：
  - 安全内参 secrss.com: 面向 AI 智能体的红队测试实战——基于 OWASP ASI 2026 的金融场景攻防实践
  - IBM 2026 年 AI 智能体指南
  - 奇安信: 2026 AI 智能体领航者深度观察
  - 腾讯云/华为云: 智能体安全与可信 AI
  - CERNET 高校智能体安全实战指南

### 弱信号：AI 安全服务（ai-security-services）
- 全部 <50 分，无入选脉冲（jobs 渠道全是噪音，如 FedEx Customs Agent）

### 关键观察（供参考，非结论）
1. **治理/合规需求（CISA/五眼、AI Governance Market）与攻防实战需求（OWASP ASI 2026 红队）同时走强**——两个脉冲其实是同一浪潮的两面。
2. **服务类信号为零**：市场有内容需求、有治理焦虑，但没有出现"能交付的服务"信号——供给侧空白。
3. 中文内容需求（74 分）几乎与全球治理需求（80 分）同强，且中文来源集中在安全内参/奇安信/ISC 等专业渠道。

## 二、公司现有资产（能接住什么）

### 产品线
| 产线 | 状态 | 相关资产 |
|---|---|---|
| security-exploration | 活跃（蜂群 + HackerOne） | 蜂群系统、H1 战绩（Unico 173+ 发现、Banco Plata v4）、`projects/security-exploration/` 已有 ai-agent-security-pilot-content-brief.md + ai-security-article-curation-list.md（271 行，VentureBeat/Cloudflare/Wiz 顶级来源策展） |
| article-production | 30 篇文章，4354 阅读 | 公众号"硬核数据风"安全深挖定位，标题格式"技术关键词：核心数据" |
| video-production | 未激活 | — |

### 技术资产
- **swarm-knowledge 蜂群系统**：自研多 agent 系统（stigmergic、SQLite 协调、probe→verifier→lead 汇聚、共享信号板），MARBLE database benchmark 100/100 F1=1.0（2026-08-07 完成）。注意：判据化 verifier 贡献大，独立评估进行中。
- **安全技能栈**：H1 bug bounty（Web/API only，无移动端）、多模型蜂群编排、Codex/Claude CLI 自动化。
- **内容产线**：ai-edu-series（AI 工程从零开始，个人系统学习笔记）+ 公众号硬核安全深挖文，产线已验证（Codex 撰写 + 三 Gate QA + 微信推送）。

### 用户个人背景
- 安全/网络渗透背景（H1 赏金猎人），中文沟通，独立开发者（个人公司模拟）。
- 对蜂群/Agent 架构直觉极强，擅长发现设计缺口。
- 有 AI 眼镜项目（LinkSee）在并行推进——注意精力分配。

## 三、需要评估的问题

1. **方向判断**：AI 安全作为战略方向的真实性/持续性如何？（vs 泡沫/炒作？）证据级别？
2. **切入点选择**：对"个人 + 小团队"形态，以下哪个切入点最优（或组合）？
   - A. 内容占位：公众号/中文 AI 安全深挖（承接 ai-security-content-demand 74 分信号）
   - B. 工具/开源：蜂群系统转 AI 安全测试工具（agentic AI 红队，承接 OWASP ASI 2026 方向）
   - C. 服务：AI 安全评估/红队服务（承接治理需求，但个人产能瓶颈？）
   - D. 知识库/情报：蜂群知识库转 AI 安全情报源
3. **差异化**：个人 vs 厂商（奇安信/悬镜/安全内参）的差异化空间在哪？个人优势（蜂群架构、H1 实战、内容直给）如何放大？
4. **节奏**：如果做，第一性动作是什么（24h 内）？与并行项目（AI 眼镜、H1 赏金）如何不冲突？
5. **明确评级**：YES（all-in）/ 有条件 YES（什么条件）/ NO，并给证据链。

## 四、输出要求
- 中文 markdown 报告，写入 /home/pwn/workspace/company/strategy/ai-security-direction-evaluation.md
- 结构：方向判断 → 切入点对比表（含成本/产能/差异化/风险）→ 推荐路径 → 24h 第一动作 → 风险与反证
- 不要修改任何代码/配置，只做评估。
