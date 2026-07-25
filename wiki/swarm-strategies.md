# 蜂群策略面板

从蜂群知识库自动同步的高价值策略与模式。
编辑此文件会保留手写内容，自动段标记为 `

<!-- swarm-kb-auto -->
## 🤖 蜂群策略同步 (2026-07-24 21:02 UTC)
*自动从蜂群知识库 L3/L4 条目生成，共计 15 条活跃知识*

---

### 认证与 API 安全 (4 条)

- **[🧠 Knowledge] JWT签名完全未验证 - 13种算法均接受任意签名** (信任度 73%, 来源: scanner-jwt)
  - CRITICAL: JWT signature is NOT verified on prime.bancoplata.mx. Tested HS256/HS384/HS512/RS256/RS384/RS512/ES256/ES384/ES512/EdDSA/PS256/PS384/PS512 — ALL 13 algorithms with ANY arbitrary signature pr

- **[🧠 Knowledge] JWT验证错误信息全面泄露 - 8种独特错误响应** (信任度 73%, 来源: scanner-jwt)
  - JWT validation error leak on prime.bancoplata.mx: 8 distinct error messages discovered. 1) HTTP 403 "RBAC: access denied" — no auth header. 2) HTTP 401 "Jwt is not in the form of Header.Payload.Signat

- **[🧠 Knowledge] alg:none攻击被显式阻止** (信任度 73%, 来源: scanner-jwt)
  - alg:none attack tested on prime.bancoplata.mx. When JWT has 3 parts (header.payload.signature) with {"alg":"none"}, server returns "Jwt header [alg] is not supported". However, when JWT has only 2 par

- **[🧠 Knowledge] [任务] JWT error leak CONFIRMED: prime.bancoplata.mx still returns detailed JWT validat...** (信任度 40%, 来源: deep-verify)
  - JWT error leak CONFIRMED: prime.bancoplata.mx still returns detailed JWT validation errors. Error messages leaked: format check, signature base64 validation, alg=none rejection, issuer validation. Rev


### 知识管理 (1 条)

- **[🧠 Knowledge] Swarm Knowledge Base Quality Audit Report** (信任度 42%, 来源: obsidian)
  - ## Swarm Knowledge Base Quality Audit Report

# Swarm Knowledge Base Quality Audit Report

**Date**: 2026-07-21  
**Auditor**: knowledge-audit agent  
**Database**: `/home/pwn/workspace/research/swarm


### 未分类 (9 条)

- **[🧠 Knowledge] [纠正] Correction: NtAllocateVirtualMemory syscall number is 0x18 on Win10 vs 0x19 on W...** (信任度 50%, 来源: hermes)
  - Correction: NtAllocateVirtualMemory syscall number is 0x18 on Win10 vs 0x19 on Win11. Wrong number causes STATUS_INVALID_PARAMETER. This is a fix for EDR bypass.

- **[🧠 Knowledge] api.koho.ca CORS trusted origin confirmed** (信任度 77%, 来源: p1-c-cors)
  - api.koho.ca CORS confirmed: www.koho.ca is sole trusted origin with credentials=true. evil.com/null origin rejected (empty ACAO returned). api.koho.ca root path returns ACAO: https://www.koho.ca with

- **[🧠 Knowledge] /1.0/context wildcard CORS on GET requests** (信任度 77%, 来源: p1-c-cors)
  - api.koho.ca/1.0/context has wildcard CORS: GET returns access-control-allow-origin: * regardless of Origin header (evil.com, www.koho.ca all get wildcard). Also has access-control-allow-credentials: t

- **[🧠 Knowledge] assets.koho.ca Subdomain Takeover PoC - zen_engineer** (信任度 77%, 来源: koho-s3-enum)
  - Subdomain Takeover PoC Found: assets.koho.ca bucket contains poc.html (141 bytes, SSE-S3 encrypted) uploaded 2024-07-17 by 'zen_engineer'. Content: '<!DOCTYPE html><html><title>SubDomain Takeover</tit

- **[🧠 Knowledge] usercontent.koho.ca S3 Bucket Properly Locked Down** (信任度 77%, 来源: koho-s3-enum)
  - S3 Bucket Locked Down (Secure): usercontent.koho.ca (ap-south-1) bucket is fully locked. Direct S3 path-style access returns AllAccessDisabled on all endpoints. Behind CloudFront with signed URL authe

- **[🧠 Knowledge] [任务] jadx APK analysis: native libencrypt.so uses custom XOR cipher for credentials. ...** (信任度 40%, 来源: analyst-1)
  - jadx APK analysis: native libencrypt.so uses custom XOR cipher for credentials. Hardcoded key at offset 0x4A20. This is a vulnerability because encryption can be trivially reversed.

- **[🧠 Knowledge] [任务] Chain #1 (CRITICAL): env.json leak reveals API architecture -> JWT validation de...** (信任度 40%, 来源: chain-synth)
  - Chain #1 (CRITICAL): env.json leak reveals API architecture -> JWT validation details exposed by prime.bancoplata.mx -> Attacker can construct valid tokens if signing key is obtained. 16+ OAuth endpoi

- **[🧠 Knowledge] [任务] 22 unique findings total: 3 HIGH (API expose, env.json, JWT leak), 6 MEDIUM, 7 L...** (信任度 40%, 来源: final-report)
  - 22 unique findings total: 3 HIGH (API expose, env.json, JWT leak), 6 MEDIUM, 7 LOW, 6 INFO. All within HackerOne scope. Auth endpoints require JWT - no direct RBAC bypass found.

- **[🧠 Knowledge] [任务] env.json CONFIRMED: HTTP 200 at https://auth.bancoplata.mx/envs/env.json. Still ...** (信任度 40%, 来源: deep-verify)
  - env.json CONFIRMED: HTTP 200 at https://auth.bancoplata.mx/envs/env.json. Still publicly accessible. Leaks: authFlowApiDomainPrefix=prime, telegrafApiUrl, snowplowCollectorUrl, centrifugoDomainPrefix,


### Web 漏洞模式 (1 条)

- **[🧠 Knowledge] [任务] /auth/api/v1/auth-flow is the core SSO/OAuth auth flow endpoint extracted from J...** (信任度 40%, 来源: recon-apk)
  - /auth/api/v1/auth-flow is the core SSO/OAuth auth flow endpoint extracted from JS bundle. Handles user login and token issuance. High-value target for OAuth redirect, CSRF, SSRF testing.

<!-- /swarm-kb-auto -->

