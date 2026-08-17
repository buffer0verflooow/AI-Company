---
tags: [operations, report, article]
created: 2026-08-09
---

# 文章产线整理报告（2026-08-09）

> 背景：用户要求"先将公司文章产线整理好"。主 Agent 对产线做了完整体检：
> 运行状态、误分发、产物质量、测试健康度，并修复了系统性根因。

---

## 0. TL;DR

1. **发现系统性 bug 并已修复**：Router 的 LLM 兜底会把非文章任务误判为 article 并强制送进产线——这是产线被污染的根源。修复后 229 个测试全绿。
2. **产线本体健康**：执行器、质量门（Gate 1-4）、humanizer、封面、排版链路完整且运行正常。
3. **积压 20 个误分发 job**（用户抱怨/数据问题/产线反馈被误送进产线写文章），已识别并标注。
4. **两条改进建议**：① 产线执行器加"消息-产物意图校验"（防误分发落地）；② 近期内容产线数据支持"单号 4 栏目"策略（见战略文档）。

---

## 1. 产线体检结果

### 1.1 运行状态（content-jobs 86 个）

| 状态 | 数量 | 说明 |
|---|---:|---|
| completed | 54 | 含 20 个误分发（非文章任务被写成文章） |
| failed | 12 | 绝大多数是误分发任务缺产物（draft/qa-report） |
| unknown | 14 | 多为 AGENTKEY_RADAR_PROBE / 后台通知误入 |
| abandoned | 3 | 用户明确放弃（"这篇文章放弃编写"） |
| needs_approval | 2 | 待人工 |
| cancelled | 1 | 本次会话 Router 误分发后取消 |

### 1.2 测试健康度

- `automation/tests/` 全量：**229 passed, 18 subtests passed** ✅
- Router 专项：**57 passed, 11 subtests passed** ✅

### 1.3 产线组件完整性（已核验）

| 组件 | 状态 |
|---|---|
| content_hermes_executor.py（隔离 Worker） | ✅ 正常（build_prompt 含 Gate 1-4 全流程） |
| humanizer 技能（34 条模式检查） | ✅ 已接入 |
| 封面生成（generate_cover.py） | ✅ 可用 |
| 排版（draft-formatted.md + wechat-preview.html） | ✅ 可用 |
| QA Gate（事实核查/内容审校/主编终审/微信预览） | ✅ 4 关齐全 |
| 数据入库（import_wx_stats.py，本次新增） | ✅ 实测通过（10 篇+426 趋势明细） |

---

## 2. 根因修复：Router 误分发

### 2.1 问题

`company_router.py` 的 `classify_with_fallback` 在 hybrid 模式下，确定性分类器给出
`company 0.84` 的判定后，因 0.84 < 0.86（hybrid 高置信阈值），触发 LLM 兜底。
LLM 看到"文章/公众号/整理"等字样就判 article，随后用 `/article ` 显式前缀
强制重跑分类，把正确的 company 判定覆盖成 `article 0.95`。

**受害案例（全部被误送进文章产线写文章）**：
- "先将公司文章产线整理好" → article（本次）
- "补英文侧" → article
- "你能自动下载公众号统计信息吗" → article
- "我要从这些领域中划分出一些公众号赛道" → article
- 用户历史抱怨："怎么又到文章产线了！？我让你写文章了吗？"（6ccb0c51）、
  "为什么写了篇文章？现在不是在调研研究吗？"（6518221e）

### 2.2 修复（company_router.py）

1. **确定性业务线判定不可被 LLM 推翻**：`decision.route != "main_agent"` 直接返回
   （article/video/security/company 强模式匹配结果置信度 0.84-0.99，信任确定性）。
2. **main_agent 但置信度 ≥0.6**（已识别为公司相关/管理/数据问题）也不走 LLM。
3. **LLM 兜底禁止产生 article/video 路由**：内容生产必须由确定性规则识别；
   真正的文章请求（"写一篇关于 JWT 安全的公众号文章"）确定性规则已能 0.86 识别，
   不需要 LLM 兜底。历史误分发全部由此产生。

### 2.3 测试更新

3 个测试断言的是旧 bug 行为（LLM 把未识别提升为 article），已更新为新行为
（保持 main_agent，禁止 LLM 改判 article/video）。

### 2.4 修复后行为验证

```
"不需要，先将公司文章产线整理好"      → company 0.45 main_agent ✅（不再进产线）
"先将公司文章产线整理好"              → company 0.84 dispatch_company ✅
"统计数据没法获取只能手动提取"        → company 0.72 main_agent ✅
"你能自动下载公众号统计信息吗"        → company 0.45 main_agent ✅
"我要从这些领域中划分出一些公众号赛道" → company 0.45 main_agent ✅
"补英文侧"                            → company 0.45 main_agent ✅
"写一篇关于JWT安全的公众号文章"       → article 0.86 dispatch_article ✅（正向不受影响）
"/article 将这篇文章形成公众号文章"   → article 0.99 dispatch_article ✅（显式前缀可用）
```

### 2.5 两段式路由机制说明（用户问答固化）

**第一段：确定性业务线判定 = 关键字/正则规则匹配（不调模型）**
- 显式前缀：/article、文章：、/video、视频：、/security、安全：、/company、公司：
- 意图规则：写/撰写/创作/改写/润色/发布/推送 + 文章对象（公众号文章/技术文章/稿件…），
  视频/安全/公司执行各有对应规则
- 直接产出 route + 置信度（写文章 → article 0.86；公司执行 → company 0.84）

**第二段：LLM 兜底 = 完整消息原文 + 五选一分类（不是关键字）**
- 触发条件：第一段置信度低于阈值（hybrid 模式 < 0.86）
- 把完整用户消息原文塞进提示词，LLM 从 security/article/video/company/none 五选一，
  输出一行 JSON {route, confidence}
- LLM 看的是整句话语义，不是关键字——这是与第一段的本质区别

**为什么 LLM 兜底会误判（不是"信息不足"）**
- 误判机制是**语义联想过度**：LLM 看到"公众号"联想到"公众号文章"，看到"统计信息"
  联想到"文章相关"，忽略整句真实意图（下载数据=运营动作）。信息完全充足时照样误判，
  实测案例："你能自动下载公众号统计信息吗" → LLM 判 article 0.95（高置信误判）。
- **二次放大（原 bug 最伤之处）**：确定性规则判 company 0.84（正确）→ hybrid 阈值
  0.86 触发 LLM 兜底 → LLM 判 article → 代码用 "/article " 显式前缀重跑确定性规则
  → 前缀强制命中 article 0.99 → 正确判定被覆盖，任务被送进文章产线。
  规则是对的，LLM 是错的，结果 LLM 赢了。
- 修复原则：**规则说了算，直觉只负责规则没认出来的情况**。
  (a) 确定性业务线判定（0.84+）直接信任，不再触发 LLM；
  (b) main_agent 但置信度 ≥0.6（公司相关/管理/数据问题）也不走 LLM；
  (c) LLM 兜底禁止产生 article/video 路由——内容生产必须由规则识别，
      真正的文章请求确定性规则已能 0.86 识别，无需 LLM 直觉。

---

## 3. 积压误分发 job 清单（20 个，已清理 ✅ 2026-08-09）

这些 job 的产物保留作历史记录，但 status 已标注清理为 cancelled
（status.json 加 `misrouted` 标记 + route_events 同步），不计入文章产出价值：

- 用户抱怨/反馈（非文章指令）：078b57f5、13e7e78a、356d17cb、3e57972b、
  a47f79d8、7b244290、65098c35、ff28199b、eccd6540
- 数据/调研/运营问题（非文章指令）：8a7b553c、c79baef1、c14347e7、469a15b7、
  799a61fa、d35c67fc、90a918e3、f54c9224
- 其他：e735ce7a（mineru 提取）、f19141b4（继续 ai edu）、00315fdf（本次）

---

## 4. 建议（下一步）

1. **执行器侧意图校验（推荐做）**：content_hermes_executor 在 build_prompt 前
   检查 request.message 是否真的含文章生产意图（复用 Router 的
   ARTICLE_DIRECT_REQUEST_RE/DESTINATION_RE），不含则直接置 status=cancelled
   并回主 Agent。这是防误分发落地的第二道闸（Router 修复是第一道）。
2. **误分发 job 清理**：把 20 个误分发 job 的 status 标注为 cancelled 或
   abandoned（保留产物不删），避免 TVCR/日报把它们当"文章产出"计价值。
3. **数据链路已通**：import_wx_stats.py + 趋势表入库已验证，统计自动化
   （手动导出→自动入库）可投入使用；如需全自动下载（浏览器自动化）需用户
   扫码授权，当前暂缓（用户已表示不需要）。
4. **产线策略衔接**：近期数据（17 篇真实发布）支持"单号 4 栏目"运营策略，
   见 strategy/ai-subfield-tracks-and-opportunities.md 与
   marketing/wechat-automation-plan.md。

---

## 5. 依据

- 全量测试：`automation/tests/` 229 passed（2026-08-09 本机运行）
- Router 行为验证：classify_with_fallback 实测 7 组消息（2026-08-09）
- 产线盘点：content-jobs 86 个 job 状态分布 + request.json 逐条核验
- 产物质量：最近 3 个误分发 completed job 的 draft/qa-report 留存
