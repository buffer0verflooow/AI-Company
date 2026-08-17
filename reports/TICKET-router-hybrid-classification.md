# 工单：company_router 混合意图分类(设计 B)

**编号**: ROUTER-HYBRID-001
**创建日期**: 2026-07-30
**目标文件**: `company/automation/company_router.py`、`company/automation/router_config.json`、`company/automation/tests/test_company_router.py`
**执行者**: opencode(自动完成编码 + 测试)

---

## 背景 / 动机

当前 `company_router.py` 用手写词表 + 正则门做产线分类(security / article / video / company)。这个"关键词打分"分类层脆弱:边界不可预测,每来一个误路由就补一条正则(见 git log 一长串 `fix: ... router` / `fix: block article route`)。

`classify_with_fallback`(line ~752)已经有一个**隔离、无工具**的低置信 LLM 兜底(`_llm_fallback_classify`,`--toolsets none --max-turns 1`,带 `COMPANY_ROUTER_BYPASS=1`),但它**只在唯一的 0.45"未识别"档**才触发。

本工单把这个 LLM 分类器从"最后兜底"升级为"模糊带主判据"——即**设计 B:混合级联分类**。

## 不可违反的安全不变量(CRITICAL — 实现时绝不能破坏)

1. **模型只做分类,绝不做强制。** LLM 的输出永远只当作一个候选 `route`,必须重新拼接显式前缀(`/security ` 等)跑一遍 `classify_message()`,让所有确定性门(目标抽取、scope 授权、外部动作审批)重新生效。**禁止**用 LLM 输出手工构造 `RouteDecision` 绕过 `classify_message`。这一机制现在已存在于 `classify_with_fallback`(line ~796),必须保留。
2. **LLM 永远不能放松安全门。** 现有逻辑(line ~797):当升级后的 route 是 security 且 action 落到 `approval_required`(缺 scope 授权),或落到 `main_agent`,保持确定性结果。这条必须保留。
3. **安全 scope 授权只认 config 的 `authorized_targets` 白名单,永不信正文里的"已授权"文本。** 不得改动 `classify_message` 里的授权判断。
4. **外部动作(发布/付款/删除等)永远走人工审批,不因 LLM 而自动派发。** 现有 `decision.external_action` 短路(line ~775)必须保留——external_action 为真时不调用 LLM 升级。
5. **免费确定性前置拒绝照旧,不进 LLM。** 空消息(0.0)、合成通知(`SYNTHETIC_MESSAGE_PREFIX_RE`)、worker/cron/subagent 会话(`_is_non_user_hermes_session`)、内部前缀、纯提问(`_looks_like_question`)——这些短路逻辑一律保留,不能因为混合模式而每轮都调 LLM。

## 需求

### 1. 新增配置开关 `router_mode`(可回退)

在 `router_config.json` 增加:
```json
"router_mode": "keyword"
```
- `"keyword"`(默认):现有行为完全不变——LLM 只在 0.45 档触发(向后兼容、可回退)。
- `"hybrid"`:启用混合级联(下述)。

`load_config` / `classify_with_fallback` 读取该值;缺失时默认 `"keyword"`。

### 2. 混合级联逻辑(`router_mode == "hybrid"` 时)

改造 `classify_with_fallback`,使 LLM 咨询的触发面从"仅 confidence == 0.45"放宽到整个**模糊带**,同时保留所有快路径与安全短路:

- **快路径(跳过 LLM)**:
  - `decision.confidence <= 0.0`(空/合成)→ 直接返回,不调用 LLM。
  - `decision.external_action` 为真 → 直接返回确定性结果(见不变量 4)。
  - 显式前缀命中(confidence == 0.99)→ 直接返回,不调用 LLM。
  - 建议:引入 `hybrid_high_confidence_skip`(默认 0.86)——keyword 分类置信度 `>=` 该值时视为足够确定,跳过 LLM 省成本/延迟。可配置。
- **模糊带(咨询 LLM)**:其余情况(即 keyword 落在 `(0.0, hybrid_high_confidence_skip)` 且非 external_action)→ 调用隔离 LLM 分类器,结果照旧重新过 `classify_message(prefix + message)`。
- LLM 返回 `none` / 低于 `llm_fallback_confidence` 阈值 / 调用失败 → 保持原确定性 decision(fail-safe)。

`"keyword"` 模式下 `classify_with_fallback` 行为与当前完全一致(回归测试必须证明这一点)。

### 3. 判定可观测性(用于监测漂移)

不新增 DB 表。仅在 `RouteDecision.reason` 里清楚标注是否经 LLM 改判(现有 "低置信 LLM 兜底改判为 ..." 前缀已够,hybrid 模式沿用/复用即可)。若改判,`reason` 必须含可区分标记,便于日后 grep `route_events.decision_json` 统计 LLM 改判比例。

## 明确不做(超出本工单范围)

- 不改 `handle_hook` 的下游派发、去重、熔断、并发上限逻辑。
- 不改 `classify_message` 的任何门(授权、外部动作、目标抽取)。
- 不删除 keyword 词表(它们在 hybrid 模式退化为快路径 + 安全门,仍需要)。
- 不引入设计 A(让主 Agent 自己判断/去掉 hook)。
- 不做真实 LLM 调用的联网测试(测试必须用注入的 fake fallback,见下)。

## 测试要求(硬性)

测试框架 = `unittest`(本机**无 pytest**)。运行方式:
```bash
cd /home/pwn/workspace/company && python3 -m unittest automation.tests.test_company_router -v
```
在 `test_company_router.py` 的 `LowConfidenceFallbackTests`(或新增 `HybridRoutingTests` 类)中新增用例,**全部用 `fallback=` 注入假分类器,禁止真实 subprocess/LLM 调用**:

1. `keyword` 模式:高置信 keyword 命中(如显式 `/article`)时,注入的 fallback **不被调用**(用 `called=[]` 断言),行为与当前一致。
2. `keyword` 模式:0.45 档仍触发 fallback(现有行为不回归)。
3. `hybrid` 模式:一条 keyword 会误分或落在模糊带的消息(confidence 0.45~0.85 之间),LLM 改判为正确 route,且 `reason` 含 LLM 改判标记。
4. `hybrid` 模式:`external_action` 为真的消息(如"发布这篇文章到公众号")**不调用** fallback,保持 `approval_required`。
5. `hybrid` 模式:安全授权不变量——LLM 说 `security` 且 confidence 0.95,但目标不在 `authorized_targets` 白名单 → 仍 `approval_required`(复用/对齐现有 `test_fallback_cannot_bypass_security_authorization`)。
6. `hybrid` 模式:LLM 返回 `none` 或调用抛异常 → 保持原确定性 decision(fail-safe)。
7. `hybrid` 模式:高于 `hybrid_high_confidence_skip` 的 keyword 命中跳过 LLM(fallback 不被调用)。
8. `hybrid` 模式:空消息 / 合成通知前缀 → 不调用 fallback。

**验收标准**:
- 上述新用例全部通过。
- **整个 `test_company_router.py` 既有用例 0 回归**(全绿)。
- 顺带确认不影响其它模块:`cd /home/pwn/workspace/company && python3 -m unittest discover automation/tests -v` 尽量全绿(若有与本改动无关的既有失败,单独列出,不强行修复)。

## 交付

- 修改后的 `company_router.py`、`router_config.json`、`test_company_router.py`。
- 简短说明:改了哪些函数、`router_mode` 如何切换、测试运行结果(通过数)。
- 不提交 git、不切分支(由人工审阅后决定)。
