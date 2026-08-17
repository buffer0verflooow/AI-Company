# 工单：方法论研究门控 —— 修复研究任务被误派为 recon 扫描

**编号**: ROUTER-METHODOLOGY-001
**创建日期**: 2026-08-12
**目标文件**: `company/automation/company_router.py`、`company/automation/tests/test_company_router.py`、`company/automation/tests/test_router_replay_49063587.py`
**触发事件**: run 49063587（company-recon-5_aa6fbe，27,891 tokens 零探测产出）

---

## 背景 / 动机

用户在 session `20260812_171115_aa6fbe` 问"AI 二进制漏洞挖掘的方法有哪些？要技术细节"（信息查询，正确走 main_agent），随后补一句方法论陈述：

> 现在方法是将二进制反编译成伪代码，然后使用SAST/静态代码分析等方法，来查找漏洞；或者通过动态fuzz，模拟执行来找漏洞

这条被 `classify_message` 判定为 security/intent=recon 派发蜂群：
- "fuzz" 命中 ACTIVE_SECURITY_TERMS → `_is_security_request` 短路直接 True（company_router.py 旧 :356-357）
- 判定顺序 security 在 research 之前（旧 :626-631）
- intent 判定因同一 "fuzz" 词命中 active 表 → recon
- 即使 security 不拦截，research 也判不出来：RESEARCH_TERMS（竞品/调研/市场/选型…）无技术方法论词，`_is_research_request` 直接 False

结果：研究任务 → recon 扫描形态 → 载荷无目标 → 全部 scanner BLOCKED → 27,891 tokens 零探测产出，仅交出一份"系统性路由错配"诊断报告。

**根因**: security 判定是"任一 active 词即命中"的或逻辑 + 优先级高于 research + research 词表偏商务调研、无技术方法论词。这是结构性问题，不是补关键词能修的——收紧只会让这类消息更早死。

## 修复方案（逻辑门控，非关键词收紧）

在 `classify_message` 的 security 判定**之前**插入 `_is_methodology_research_request` 门控：

```
安全任务（保持 security）
  ├─ 有主动攻击动词：扫描/探测/枚举/爆破/绕过/攻击/验证漏洞/写poc/recon/probe/brute/fuzz<目标>
  ├─ 有目标实体：IP/域名/APK（extract_target 命中）
  └─ 或普通 security 词命中且无方法论强信号
方法论陈述（→ research）
  ├─ 有方法论强信号词：方法/方法论/技术细节/语法树/代码图/全景/原理/主流/盘点/梳理/现状/怎么做/如何实现
  ├─ 有技术/安全上下文：漏洞/逆向/fuzz/反编译/伪代码/二进制/SAST/静态分析/模拟执行/exploit/渗透
  └─ 无主动动词 + 无目标实体 → research（intent=research, dispatch_swarm）
```

新增正则（company_router.py）：
- `METHODOLOGY_STRONG_RE` — 方法论强信号词
- `METHODOLOGY_CONTEXT_RE` — 技术/安全上下文
- `ACTIVE_TASK_VERB_RE` — 主动攻击动词（含 fuzz 作动词的用法，如"fuzz 一下那个二进制"）

## 验证

- `python3 -m unittest automation.tests.test_company_router` → 62/62 通过（新增 3 例：方法论正例、主动任务反例）
- 新增 `test_router_replay_49063587.py`（4 例，真实消息回放）：
  - 消息 1（带问号）→ company/main_agent（既有正确行为不变）
  - 消息 2（方法论陈述）→ research/dispatch_swarm/intent=research（本次修复目标）
  - 4 条真实主动任务（扫描 example.com / 分析本机 APK / 给 acme 写 poc / 扫描 10.0.0.5）→ 全部保持 security
- 全模块回归：`python3 -m unittest discover -s automation/tests -p "test_*.py"` → 246/246 通过

## 边界声明

- 带问号的纯信息查询仍走 main_agent（QUESTION_RE 拦截在门控之前，顺序未动）
- 带目标实体（IP/域名/APK）或主动攻击动词的消息绝不被门控抢走
- "有哪些" 类问句命中 QUESTION_RE → main_agent，符合"信息查询不自动派发"不变量
- 门控只改分类，不碰授权逻辑（authorized_targets 白名单机制未动）

## 后续观察点

- 下次出现"检索/盘点/梳理 + 技术词"的陈述句，确认走 research 而非 security
- 若出现方法论词+目标实体混搭的漏网案例（如"研究一下 10.0.0.5 的绕过方法"），检查 ACTIVE_TASK_VERB_RE 是否已覆盖
