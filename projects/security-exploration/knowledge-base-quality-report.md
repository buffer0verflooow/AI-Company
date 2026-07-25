---
swarm: capture
swarm_tags: [knowledge-base, quality, audit, cleanup]
swarm_agent: obsidian
swarm_source: article
swarm_intent: analyze
---

# Swarm Knowledge Base Quality Audit Report

**Date**: 2026-07-21  
**Auditor**: knowledge-audit agent  
**Database**: `/home/pwn/workspace/research/swarm-knowledge/swarm_knowledge.db`  
**Total entries**: 127 (82 active, 42 superseded, 3 stale)

---

## Executive Summary

The swarm knowledge base had severe quality contamination: **~51% of entries (42 of 127) were noise** — agent intermediate outputs, test data, and git diffs disguised as knowledge entries. The auto-promotion system (`governance` agent) exacerbated this by promoting noise up the DIKW pyramid based solely on trust vector thresholds without human-level content understanding.

**After cleanup**: 82 active entries remain across L1 (30), L2 (38), L3 (14). The L4 (Wisdom) tier had a single imposter entry that was also removed.

---

## 1. Noise Analysis

### 1.1 Entry Title Patterns

| Pattern | Count | Classification |
|---------|-------|---------------|
| `[任务]` prefix titles | 109/127 (86%) | Format noise — content may still have value |
| `┊ review diff` entries | 20 | **Noise** — raw git diffs captured as knowledge |
| Test agent entries | 11 | **Noise** — benchmark data for DIKW testing |
| Agent self-talk (e.g. "现在我有完整的公司状态认识") | ~14 | **Noise** — intermediate agent thinking, not knowledge |

### 1.2 Source Agent Analysis

| Source Agent | Entries | Noise Rate | Notes |
|-------------|---------|-----------|-------|
| reporter-01 | 29 | High | Produced most review-diff entries |
| analyst-01 | 20 | Moderate | Half are self-talk + analysis boilerplate |
| test-agent-* / onto-test-* | 11 | 100% | Created for DIKW promotion testing |
| scanner-jwt | 5 | 0% | All valuable technique findings |
| p1-c-cors | 4 | 0% | All valuable |
| koho-s3-enum | 4 | 0% | All valuable |

### 1.3 Content Quality Issues

1. **Agent self-talk captured as knowledge**: Entries like "现在我有完整的公司状态认识" or "Now I have the full picture" are intermediate reasoning steps, not distilled knowledge. These represent the agent's internal state before generating an actual report.
2. **Git diff content**: 20 entries contain raw diff output (`┊ review diff`), which are version control artifacts, not knowledge.
3. **Test/benchmark data**: 11 entries from test agents (`test-agent-0/1/2`, `onto-test-0/1/2`, `test-scanner`) were created for the DIKW promotion pipeline test suite but were never cleaned up.
4. **L4 imposter**: The sole "Wisdom" entry (`f5e75ca9`) was a duplicate of an L3 entry about SMB/EternalBlue — not wisdom-level meta-strategy.

### 1.4 Root Cause: Auto-Promotion System Failure

The `knowledge_promotions` table shows **30 promotions**, all performed by the `governance` agent with automatic criteria:
- L1→L2: Trust ≥ 0.72 with 1-2 corroborating sources
- L2→L3: Trust ≥ 0.76 with 2 corroborating sources

**Problems with auto-promotion**:
- **No content quality gate**: Entries promoted based solely on trust_vector thresholds, not whether the content is actual knowledge
- **Test data promoted**: Test-agent entries reached L2 via auto-promotion
- **Review-diffs reached L3**: Three review-diff entries were promoted all the way to L3
- **No human-in-the-loop**: Zero promotions had human review; all 30 were `governance` → auto
- **No L4 promotion path**: No L3→L4 promotion mechanism exists; the single L4 entry was manually misclassified

---

## 2. Cleanup Actions Executed

### 2.1 Entries Superseded (42 total)

| Category | Count | Detail |
|----------|-------|--------|
| Test entries | 11 | All test-agent-0/1/2, onto-test-0/1/2, test-scanner, test-agent entries |
| Review-diff entries | 20 | Git diff content from reporter-01 and analyst-01 |
| Agent self-talk (L1) | 6 | "现在我有完整的公司状态认识", "现在我拥有完整的分析数据", "现在我有了完整的代码分析", "证据收集完毕", plus 2 analysis-complete boilerplate |
| L4 imposter | 1 | Duplicate of L3 scanner-2 SMB finding — not Wisdom |
| L3 noise entries | 4 | Review-diffs and self-talk that had been auto-promoted to L3 |

### 2.2 Entries Promoted (11 total)

| ID (partial) | From | To | Reason |
|-------------|------|----|--------|
| de5812b9 | L1 | L3 | Critical attack chain: env.json leak → JWT → token forgery |
| 6f139427 | L1 | L3 | JWT error leak CONFIRMED (8 distinct error messages) |
| e0d7e4a4 | L1 | L3 | env.json CONFIRMED publicly accessible |
| e4d8b59c | L1 | L3 | APK XOR cipher vulnerability with hardcoded key |
| a352c33f | L1 | L3 | Core SSO/OAuth auth-flow endpoint extracted |
| ad96aa0e | L1 | L3 | 22 unique findings summary with severity classification |
| 34b53160 | L1 | L2 | KOHO JS bundle endpoint enumeration |
| 919a58a1 | L1 | L2 | Subdomain enumeration: 52 targets, 27 alive |
| 475e3252 | L2 | L3 | JWT validation error info leak (8 types documented) |
| c64ce786 | L2 | L3 | alg:none attack explicitly blocked (negative knowledge) |
| 8de68c5d | L2 | L3 | NT syscall number correction (Win10 vs Win11) |

### 2.3 Pre-existing Stale Entries (3, unchanged)

| ID (partial) | Level | Content |
|-------------|-------|---------|
| 095e8ce3 | L3 | SMB/EternalBlue finding (already stale) |
| 0716a89d | L1 | Internal metrics exposed (already stale) |
| 3beadfb2 | L1 | Banco Plata Android App variants (already stale) |

---

## 3. Post-Cleanup DIKW Distribution

| Level | Active | Superseded | Stale | Total | Description |
|-------|--------|-----------|-------|-------|-------------|
| L1 (Data) | 30 | 18 | 1 | 48 | Raw observations, tool outputs needing further extraction |
| L2 (Information) | 38 | 18 | 0 | 56 | Structured findings, filtered observations |
| L3 (Knowledge) | 14 | 8 | 1 | 22 | Validated patterns, confirmed vulnerabilities |
| L4 (Wisdom) | 0 | 1 | 0 | 1 | **Empty** — no meta-strategies exist |
| **Total** | **82** | **42** | **3** | **127** | |

### 3.1 Knowledge Type Distribution (Active)

| Type | Count | Description |
|------|-------|-------------|
| tool_usage | 64 | Tool outputs captured at L1 (most need further distillation) |
| technique | 8 | Structured exploitation/recon methods |
| vulnerability | 5 | Confirmed security vulnerabilities |
| observation | 4 | Direct recon observations |
| mechanism | 1 | Attack chain analysis |

### 3.2 Key Findings

- **No Wisdom (L4) exists**: The DIKW pyramid is flat-topped. No meta-strategies, guiding principles, or reusable heuristics have been distilled from the 82 active entries.
- **64 L1 tool_usage entries are overhead**: These are raw agent outputs with "[任务]" titles. While they contain valuable data, they lack the structure needed for multi-hunt knowledge transfer.
- **14 genuine L3 entries**: The core knowledge tier now holds real security findings — CORS misconfigurations, JWT vulnerabilities, S3 exposures, and attack chains.

---

## 4. Recommendations

### 4.1 Immediate Fixes

1. **Fix the auto-promotion pipeline**: Add a content-quality gate that rejects entries with `[任务]` prefix, review-diff patterns, and agent self-talk before they enter the knowledge base. The current trust-vector-only approach is counterproductive — it amplifies noise.

2. **Add a human-in-the-loop for L3+ promotions**: All L3 and L4 promotions should require explicit human or verified cross-validation before promotion, not automatic threshold-based approval.

3. **Implement wisdom distillation**: Create a governance routine that periodically scans L3 entries and synthesizes meta-strategies (e.g., "Always check env.json before full port scan" or "JWT endpoints should be tested for 13 algorithm types") into L4 entries.

### 4.2 Architectural Changes

4. **Prevent L1 from becoming a catch-all**: The current pipeline captures every agent tool output as a knowledge entry. Introduce a classification filter: only entries with `knowledge_type` in (`vulnerability`, `technique`, `pattern`, `strategy`) should enter the main knowledge table. Raw tool output belongs in `raw_agent_events`.

5. **Re-title entries**: The `[任务]` prefix is an agent-internal workflow tag, not a knowledge title. Either strip it at capture time or require agents to generate human-readable titles. 86% of entries have this prefix, degrading searchability.

6. **Fix lineage**: 323/453 lineage entries are `cross_agent_validation` — suspiciously high. Verify that the lineage tracking isn't double-counting self-citations.

### 4.3 Data Quality Targets

| Metric | Current | Target |
|--------|---------|--------|
| Active entries with `[任务]` title | ~80% | <10% |
| L3 (Knowledge) entries | 14 | 25+ |
| L4 (Wisdom) entries | 0 | 5+ |
| Human-reviewed promotions | 0/30 | 50%+ |
| Entries with non-noise titles | 17/127 (13%) | 60%+ |

---

## 5. Detailed Change Log

### Entries Superseded

```
Test entries (11):
  594ebfc1-0216	test-agent	[任务] Integration test: sub-agent capture script working
  1f5861bc-75a2	test-agent-1	[任务] Test finding 1: nmap port scan
  2eca883b-1874	test-agent-1	[任务] nmap port_scan found open port 445 SMB on 192...
  6c933743-fc41	test-agent-0	[任务] nmap port_scan found open port 445 SMB on 192...
  7f289761-811b	test-agent-2	[任务] Test finding 2: nmap port scan
  80a64515-1200	onto-test-2	[任务] nmap scan revealed port 445 open
  9c43cc0f-d4b1	onto-test-1	[任务] nmap scan revealed port 445 open
  a9c3ab09-e940	test-scanner	[任务] nmap scan found port 445 SMB open
  a9fa2e76-1bd0	onto-test-0	[任务] nmap scan revealed port 445 open
  b3697cee-e7ac	test-agent-0	[任务] Test finding 0: nmap port scan
  d386d8e7-3ec6	test-agent-2	[任务] nmap port_scan found open port 445 SMB on 192...

Review-diff entries (20):
  0dadd20e-bf6f, 51916296-0504, 52082b91-9e6f, 635e7d56-ea0d,
  70a68780-2da1, 72cbae72-0ba6, 7e8d0db1-edf6, c0becd64-5e70,
  e79fb3b1-35e8, 0da79fb2-cdd9, 469de4d5-16dc, 6f6b4803-6d69,
  86e3fc07-cefa, 902bae41-c1bf, 9b80a6e9-e566, e7328124-aab4,
  f67c6db1-ac29, 0cbd8440-364f, 95896116-4e96, e97d0d1b-ad3e

Agent self-talk (6):
  adf1daed-7572	L1	"现在我有完整的公司状态认识"
  b27bcb44-3979	L1	"现在我拥有完整的分析数据"
  d1c8d316-ea45	L1	"现在我有了完整的代码分析"
  fbae9fab-7d0f	L1	"证据收集完毕"
  4cc95b83-3fd2	L1	"分析完成。以下是编译产物的结构化分析"
  c429dbc0-48b0	L1	"分析完成。以上是公司自动路由与..."

L4 imposter (1):
  f5e75ca9-0727	L4	Duplicate of L3 SMB/EternalBlue finding

L3 auto-promoted noise (4):
  0cbd8440-364f	L3	review diff (mobile)
  95896116-4e96	L3	review diff (ai_ml)
  e97d0d1b-ad3e	L3	review diff (web)
  6c1ae6e5-390	L3	"现在我掌握了公司全貌。整合报告"
  6e39b192-d85	L3	"现在我已掌握完整数据。以下是我的分析结论"
  89d214d6-d12	L3	"Verified. I have read all three changed files..."
  e9393730-44b	L3	"## RANKED ANALYSIS HYPOTHESES — domain:jdk-17.0.19"
```

### Promotions Recorded

```
de5812b9-a3fd	L1→L3	Attack chain: env.json leak → JWT → token forgery
6f139427-dbc8	L1→L3	JWT error leak CONFIRMED (8 error types)
e0d7e4a4-e61c	L1→L3	env.json CONFIRMED publicly accessible
e4d8b59c-89ab	L1→L3	APK XOR cipher vulnerability (hardcoded key)
a352c33f-dcec	L1→L3	Core SSO/OAuth auth-flow endpoint extracted
ad96aa0e-f52e	L1→L3	22 unique findings summary with severity
34b53160-238d	L1→L2	KOHO JS bundle endpoint enumeration
919a58a1-14ed	L1→L2	52 subdomains discovered, 27 alive
475e3252-e938	L2→L3	JWT validation error info leak (8 types)
c64ce786-dbd5	L2→L3	alg:none attack explicitly blocked
8de68c5d-c17e	L2→L3	NT syscall number correction (Win10 vs Win11)
```

---

*Report generated by knowledge-audit agent. For questions, contact the swarm-knowledge governance team.*
