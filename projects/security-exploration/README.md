---
tags: [project, security, swarm, bug-bounty]
created: 2026-07-15
updated: 2026-07-15
status: active
---

# 安全探索产品线项目

本页面是公司视角的产品线入口。运行代码和敏感项目数据继续保存在公司 Wiki 外部的既有目录中，避免 Obsidian、内容代理或发布工具误摄入未披露信息。

## 运行组成

| 组件 | 路径 | 公司角色 |
|------|------|----------|
| Swarm Knowledge | `/home/pwn/workspace/research/swarm-knowledge` | 产品线运行内核 |
| Recon Skills | `/home/pwn/workspace/research/recon-skills` | 领域能力包 |
| HackerOne Campaigns | `/home/pwn/workspace/hackerone` | 客户/项目隔离数据 |
| Orchestration Scripts | `/home/pwn/workspace/scripts` | 节点和流程工具 |

## 公司集成接口

安全产线只向公司层输出结构化摘要：

```yaml
type: security_research_candidate
classification: internal | publishable
disclosure_status: embargoed | approved
source_run_id: string
title: string
abstract: string
reusable_patterns: []
redactions_applied: []
evidence_refs: []
```

其中 `evidence_refs` 只允许引用隔离制品 ID，不得复制原始凭证或目标响应。

## 近期任务

- [x] HackerOne GraphQL 无鉴权资产发现方法已记录（`./h1-graphql-api-reference`）
- [ ] 建立 `organization/product_line/project/engagement` 身份字段
- [ ] 建立安全知识脱敏和披露审批流程
- [ ] 将实际 HackerOne 状态和到账赏金接入财务台账
- [ ] 生成第一份可公开的原创安全内容 Brief
- [ ] 用公开案例跑通文章和视频转化链路

## 关联

- [[../../operations/business-lines/security-exploration|安全探索与赏金产线]]
- [[../../operations/business-lines/article-production|文章产线]]
- [[../../operations/business-lines/video-production|视频产线]]
