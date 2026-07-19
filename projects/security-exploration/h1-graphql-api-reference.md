---
tags: [security, h1, api, recon, graphql]
created: 2026-07-16
updated: 2026-07-16
status: active
---

# HackerOne GraphQL API — 无需鉴权的资产发现方法

## 核心发现

HackerOne 的 GraphQL API（`https://hackerone.com/graphql`）**完全开放，无需任何 API Token 或认证**。可通过标准化 GraphQL 查询获取任意公开程序的完整资产列表、元信息和配置。

这是对 `why-not-hackerone-20260716.md` L6（凭证缺失）的实质性突破：**发现阶段不需要凭证**。

## 可用端点

| 端点 | 方法 | 鉴权 | 用途 |
|------|------|------|------|
| `https://hackerone.com/graphql` | POST | ❌ 无需 | 程序元信息、资产范围、公开报告统计 |
| `https://hackerone.com/graphql` | POST | ❌ 无需 | structured_scopes（完整资产表含 instruction） |

### 被封锁的通道（已知）

| 通道 | 原因 |
|------|------|
| `hackerone.com` HTML 页面 | Cloudflare 反爬（浏览器 ERR_CONNECTION_CLOSED） |
| `api.hackerone.com/v1/*` | 需要 API Token（Hacker API） |
| Firecrawl / web_extract | 被 Cloudflare 边缘 IP 拦截 |
| CSV 导出 | 路径 404（可能需登录） |

## 查询模板

### 1. 程序基本信息

```bash
curl -s --max-time 20 \
  -H 'Content-Type: application/json' \
  -H 'User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36' \
  -H 'Origin: https://hackerone.com' \
  --data-raw '{"query":"{team(handle:\"PROGRAM_HANDLE\"){id,name,handle,currency,offers_bounties,state,launched_at,submission_state,profile_picture(size:large),about}}"}' \
  'https://hackerone.com/graphql' | python3 -m json.tool
```

**返回字段**：`id`, `name`, `handle`, `currency`, `offers_bounties`, `state`, `launched_at`, `submission_state`, `profile_picture`, `about`

### 2. 完整资产列表（核心查询）

```bash
curl -s --max-time 15 \
  -H 'Content-Type: application/json' \
  -H 'User-Agent: Mozilla/5.0' \
  -H 'Origin: https://hackerone.com' \
  --data-raw '{"query":"{team(handle:\"PROGRAM_HANDLE\"){structured_scopes(first:50){edges{node{asset_type,asset_identifier,eligible_for_bounty,eligible_for_submission,instruction}}}}}"}' \
  'https://hackerone.com/graphql'
```

**返回字段（每个资产）**：
- `asset_type` — `URL` | `WILDCARD` | `OTHER` | `OTHER_APK` | `IP_ADDRESS` 等
- `asset_identifier` — 域名/URL/IP/描述
- `eligible_for_bounty` — 是否有赏金
- `eligible_for_submission` — 是否可提交
- `instruction` — 测试说明/限制

**分页**：用 `first:N` 控制每页数量，`edges` 返回资产节点数组。大多数程序 <50 个资产，一次查询即可全覆盖。

### 3. 公开报告（可选，有时超时）

```bash
curl -s --max-time 10 \
  -H 'Content-Type: application/json' \
  -H 'User-Agent: Mozilla/5.0' \
  -H 'Origin: https://hackerone.com' \
  --data-raw '{"query":"{team(handle:\"PROGRAM_HANDLE\"){disclosed_reports(first:5){edges{node{title,severity_rating,bounty_awarded_amount,created_at,disclosed_at}}}}}"}' \
  'https://hackerone.com/graphql'
```

⚠️ `statistics` 和 `disclosed_reports` 字段偶发超时，重试即可。核心的 `structured_scopes` 始终稳定。

## 已验证案例：Unico IDtech

```
Program handle: unico_idtech
GraphQL: ✅ 200, ~7KB JSON
资产数量: 30 (27 in-scope + bounty, 3 out-of-scope)
查询耗时: <2秒
```

## 在安全产线中的位置

```
资产发现阶段  (本文档)
    ↓
程序元信息 → 资产清单 → Scope 识别
    ↓
蜂群并行探索
    ↓
独立验证 → 提交
```

此方法填补了 `why-not-hackerone-20260716.md` L6 的**发现阶段**空白——从 HackerOne 拉取资产和 Scope **不需要 API Token**，只有提交报告阶段才需要。

## 注意事项

1. **Rate Limit**: 建议两次请求间隔 ≥2 秒，避免触发 Cloudflare 限流
2. **User-Agent**: 必须带合理的浏览器 UA，否则返回空
3. **Origin Header**: `Origin: https://hackerone.com` 是 GraphQL 端点接受查询的关键
4. **复杂度**: 嵌套查询（如同时请求 scopes + statistics + reports）会超时，拆成独立查询
5. **Backoff**: 超时后等 5 秒重试，通常第二次就成功

## 关联

- [[../../reports/why-not-hackerone-20260716|为何未探索 H1 — 六层阻断分析]]
- [[../../operations/business-lines/security-exploration|安全探索与赏金产线]]
- [[./README|产品线项目页]]
