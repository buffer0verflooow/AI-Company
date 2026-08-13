# 蜂群策略面板

从蜂群知识库自动同步的高价值策略与模式。
编辑此文件会保留手写内容，自动段标记为 `

<!-- swarm-kb-auto -->
## 🤖 蜂群策略同步 (2026-08-13 21:06 UTC)
*自动从蜂群知识库 L3/L4 条目生成，共计 18 条活跃知识*

---

### 认证与 API 安全 (3 条)

- **[🧠 Knowledge] alg:none攻击被显式阻止** (信任度 73%, 来源: scanner-jwt)
  - alg:none attack tested on prime.bancoplata.mx. When JWT has 3 parts \(header.payload.signature\) with {"alg":"none"}, server returns "Jwt header \[alg\] is not supported". However, when JWT has only 2 par

- **[🧠 Knowledge] JWT验证错误信息全面泄露 - 8种独特错误响应** (信任度 73%, 来源: scanner-jwt)
  - JWT validation error leak on prime.bancoplata.mx: 8 distinct error messages discovered. 1\) HTTP 403 "RBAC: access denied" — no auth header. 2\) HTTP 401 "Jwt is not in the form of Header.Payload.Signat

- **[🧠 Knowledge] JWT签名完全未验证 - 13种算法均接受任意签名** (信任度 73%, 来源: scanner-jwt)
  - CRITICAL: JWT signature is NOT verified on prime.bancoplata.mx. Tested HS256/HS384/HS512/RS256/RS384/RS512/ES256/ES384/ES512/EdDSA/PS256/PS384/PS512 — ALL 13 algorithms with ANY arbitrary signature pr


### 未分类 (11 条)

- **[🧠 Knowledge] usercontent.koho.ca S3 Bucket Properly Locked Down** (信任度 77%, 来源: koho-s3-enum)
  - S3 Bucket Locked Down \(Secure\): usercontent.koho.ca \(ap-south-1\) bucket is fully locked. Direct S3 path-style access returns AllAccessDisabled on all endpoints. Behind CloudFront with signed URL authe

- **[🧠 Knowledge] assets.koho.ca Subdomain Takeover PoC - zen\_engineer** (信任度 77%, 来源: koho-s3-enum)
  - Subdomain Takeover PoC Found: assets.koho.ca bucket contains poc.html \(141 bytes, SSE-S3 encrypted\) uploaded 2024-07-17 by 'zen\_engineer'. Content: '&lt;!DOCTYPE html&gt;&lt;html&gt;&lt;title&gt;SubDomain Takeover&lt;/tit

- **[🧠 Knowledge] /1.0/context wildcard CORS on GET requests** (信任度 77%, 来源: p1-c-cors)
  - api.koho.ca/1.0/context has wildcard CORS: GET returns access-control-allow-origin: \* regardless of Origin header \(evil.com, www.koho.ca all get wildcard\). Also has access-control-allow-credentials: t

- **[🧠 Knowledge] api.koho.ca CORS trusted origin confirmed** (信任度 77%, 来源: p1-c-cors)
  - api.koho.ca CORS confirmed: www.koho.ca is sole trusted origin with credentials=true. evil.com/null origin rejected \(empty ACAO returned\). api.koho.ca root path returns ACAO: https://www.koho.ca with 

- **[🧠 Knowledge] \[任务\] ⏱ Timeout — denying command** (信任度 73%, 来源: researcher-02)
  - ⏱ Timeout — denying command ⚠ Approval: cd ~/workspace/research/swarm-knowledge &amp;&amp; .venv/bin/python3 - &lt;&lt;'EOF' import sqlite3 db = sqlite3.connect\('swarm\_knowl… → timed out \(no response\) ┊ review diff

- **[🧠 Knowledge] \[任务\] ⏱ Timeout — denying command** (信任度 73%, 来源: researcher-01)
  - ⏱ Timeout — denying command ⚠ Approval: cd ~/workspace/company &amp;&amp; python3 -c " import sqlite3 for db in \['operations/runtime/company\_router.db','operations/ope… → timed out \(no response\) ⏱ Timeout — d

- **[🧠 Knowledge] \[任务\] ⏱ Timeout — denying command** (信任度 73%, 来源: scanner-02)
  - ⏱ Timeout — denying command ⚠ Approval: cd /home/pwn/workspace/research/swarm-knowledge &amp;&amp; .venv/bin/python -c " import sys sys.path.insert\(0, '.'\) from src im… → timed out \(no response\) 补盲扫描完成。以下为交付。

- **[🧠 Knowledge] \[任务\] ⏱ Timeout — denying command** (信任度 73%, 来源: scanner-02)
  - ⏱ Timeout — denying command ⚠ Approval: ls /home/pwn/workspace/company/projects/ &amp;&amp; echo "---" &amp;&amp; ls /home/pwn/workspace/company/ &amp;&amp; echo "---router db---" &amp;&amp; … → timed out \(no response\) ┊ review diff

- **[🧠 Knowledge] \[任务\] ⏱ Timeout — denying command** (信任度 73%, 来源: analyst-01)
  - ⏱ Timeout — denying command ⚠ Approval: python3 -c " import sqlite3 db = sqlite3.connect\('/home/pwn/workspace/research/swarm-knowledge/swarm\_knowledge.db'\) db.… → timed out \(no response\) ⏱ Timeout — d

- **[🧠 Knowledge] \[任务\] ┊ review diff** (信任度 73%, 来源: analyst-01)
  - ┊ review diff a/.hermes\_tmp/kb\_query\_analyst.py → b/.hermes\_tmp/kb\_query\_analyst.py @@ -0,0 +1,47 @@ +import sys +sys.path.insert\(0, '/home/pwn/workspace/research/swarm-knowledge'\) +from src import Sw

- **[🧠 Knowledge] \[纠正\] Correction: NtAllocateVirtualMemory syscall number is 0x18 on Win10 vs 0x19 on W...** (信任度 50%, 来源: hermes)
  - Correction: NtAllocateVirtualMemory syscall number is 0x18 on Win10 vs 0x19 on Win11. Wrong number causes STATUS\_INVALID\_PARAMETER. This is a fix for EDR bypass.


### Web 漏洞模式 (4 条)

- **[🧠 Knowledge] \[任务\] ┊ review diff** (信任度 73%, 来源: reporter-01)
  - ┊ review diff a/company/.tmp\_read\_kb\_598e4a3e.py → b/company/.tmp\_read\_kb\_598e4a3e.py @@ -0,0 +1,27 @@ +\#!/usr/bin/env python3 +"""Read knowledge entry 598e4a3e from swarm\_knowledge.db for reporter me

- **[🧠 Knowledge] \[任务\] ┊ review diff** (信任度 73%, 来源: reporter-01)
  - ┊ review diff a//home/pwn/workspace/company/reports/swarm-7d8cb7f0-5118-4ef3-9672-247c9fb1f2c2-reporter-report.md → b//home/pwn/workspace/company/reports/swarm-7d8cb7f0-5118-4ef3-9672-247c9fb1f2c2-rep

- **[🧠 Knowledge] \[任务\] ⏱ Timeout — denying command** (信任度 73%, 来源: researcher-01)
  - ⏱ Timeout — denying command ⚠ Approval: curl -sL --max-time 25 "https://arxiv.org/abs/2505.22010" -o /tmp/vulbinllm.html &amp;&amp; python3 -c " import re,html t=open\(… → timed out \(no response\) ⏱ Timeout — d

- **[🧠 Knowledge] \[任务\] 侦察完成。本地资料（策略文档 ×4、wiki 文章 ×3、KB 条目 ×8、OWASP 框架参考）已消化。无授权外部目标，故未做主动网络探测（符合约束 2），本...** (信任度 73%, 来源: scanner-01)
  - 侦察完成。本地资料（策略文档 ×4、wiki 文章 ×3、KB 条目 ×8、OWASP 框架参考）已消化。无授权外部目标，故未做主动网络探测（符合约束 2），本任务为概念域攻击面映射。以下为结构化扫描结果。 ════════════════════════════════════════════ SCANNER 侦察报告 — AI 安全攻击面映射 ═════════════════════════

<!-- /swarm-kb-auto -->

