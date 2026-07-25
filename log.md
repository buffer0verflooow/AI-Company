---
tags: [log, meta]
created: 2026-07-04
updated: 2026-07-19
---

# 知识库日志

AI 自动追加。记录每次操作的时间线。

## [2026-07-21] 市场接入 | 市场雷达桥接到内容项目

- **桥接文档**：新建 `marketing/market-to-content-bridge.md`，将 4 个市场主题映射到 ai-edu-series 和 article-curation 的具体选题、排期建议和优先级
- **最高分脉冲**："企业 AI 智能体安全治理需求"(81.23, 7 来源) → 建议提前 ai-edu-series #15/#16 排期
- **文档更新**：DASHBOARD 市场雷达行更新为"8 轮/170 信号/桥接已关联"；marketing README 更新为稳定运行+桥接进展
- **摘要**：外部市场雷达从纯技术运行接入内容决策链路。雷达数据现可直接影响选题优先级和排期。市场验证 Worker 沙箱违规问题已记录为遗留风险。

## [2026-07-19] 知识库整理 | 数据同步与索引更新

- **进度数据修复**:
  - [[product/README|产品部]] — ai-edu-series 进度 2/20→10/20，article-curation 4篇→14篇
  - [[DASHBOARD|公司仪表盘]] — ai-edu-series 5/20→10/20，已完成文章 5→10 篇，最近更新日期修正
- **索引更新**:
  - [[strategy/README|战略部]] — 新增 [[strategy/market-demand-analysis|市场需求分析：AI+安全赛道]] 文档引用
  - [[index|全文索引]] — 策略文档表新增市场分析条目，更新日期
- **状态修正**: 标记 2 个废弃内容任务（AI-AGENT-SEC-01/02）和 1 个滞留运行任务（1ee77731）
- **摘要**: 修复产品部 README 和仪表盘中的过时进度数据（滞后 2-12 天），补录缺失的策略文档索引，清理运行时目录中无状态文件的废弃任务。未删除任何用户文件或数据。

## [2026-07-15] 市场发现 | 外部市场雷达与机会验证闭环

- **第三方审查**：审计 AnySearch Skill v2.1.0，固定上游提交 `b1a1bae6...` 与 ZIP SHA-256 `920eddb2...`；不全局安装、不自动注册、不读取 Skill `.env`
- **公开采集**：新增 `market_radar.py`，只发送配置白名单中的公开查询；禁用人员/邮箱搜索和包含本地或敏感数据的查询
- **信号治理**：新增 `marketing/market_signals.db`，实现 URL 规范化、跨周期去重、提示注入标记、战略/商业/新鲜度评分和主题资格门
- **多来源门**：只有达到分数且至少两个独立域名佐证的主题才能生成市场脉冲；旧脉冲由新一轮同主题结果自动 supersede
- **真实 E2E（内容需求）**：`MKT-RUN-832f3c788415` 采集 39 条结果并形成 3 个脉冲；`AUTO-RUN-aa6d4405550a` 打开 2 个独立正文，验证中文 AI 智能体安全内容机会，形成 Brief、反证、低成本实验和停止条件
- **真实 E2E（相邻市场）**：`MKT-RUN-8bb98f9b66e8` 采集 48 条结果并形成 4 个脉冲；`AUTO-RUN-066cbbf59084` 发现并验证 Agent 可观测性、评估、工作流、成本与治理机会，推荐先用安全内容专栏和自检清单做低成本实验
- **证据分级**：正文已打开的来源标记 `verified_source`，仅在搜索响应出现的内容标记 `discovered_signal`，禁止把发现数量写成已验证来源数量
- **证据审计**：相邻市场 Run 最终记录为 2 个正文核验、1 个元数据核验、1 个仅可达和 1 个搜索记录级信号；Agentic AI 安全市场规模只作为相邻参考，不冒充整个机会 TAM
- **主题节流**：已评估主题冷却 168 小时；只有评分提高至少 8 分才提前重开，新脉冲替代旧脉冲时自动 dismiss 旧开放机会
- **定时运行**：Hermes Cron `company-market-radar` 每日 08:30 运行；`company-daily-operator` 09:00 消费市场脉冲
- **回归与运行审计**：自动化测试 64/64 通过；测试市场库与正式市场库已隔离；Cron 均 active，Hook 健康，部署脚本与仓库入口 SHA-256 一致

## [2026-07-15] 经营自治 | 每日经营者与首个主动 Run 上线

- **主动入口**：新增 `company_operator.py`，不依赖用户消息，从长期经营任务、TVCR 提案、已批准实验和缺失业务结果中建立机会队列
- **决策机制**：按优先级、经营评分、风险和审批状态选择任务；单周期最多执行 1 项，发布、付款、删除、HackerOne 提交和外部安全动作继续阻断
- **隔离执行**：Worker 只能写入 `operations/runtime/autonomy-runs/<run_id>/`，必须产生 `action-report.md` 与 `result.json`
- **经营计量**：自治运行写入 `operational_runs`，同步记录 Hermes 会话、Token、工具调用、成本状态和产物，供 TVCR 次日评价
- **首个 E2E**：`AUTO-RUN-1ec362301fc4` 主动选择模型成本盲区，完成 53/56 会话价格映射、成本核验报告、CSV、计算脚本和待确认应用包；估算字段与 actual 字段严格分离
- **定时运行**：Hermes Cron `company-daily-operator` 已启用，每日 09:00 执行并向管理会话发送摘要
- **回归**：自动化测试 52/52 通过

## [2026-07-15] 流程自动化 | 产品线路由、主动回传与知识治理闭环

- **Research E2E**：Run `db6bcc86-9572-4639-a02e-fa122d44c00a`，3/3 completed；隔离 PID 失效后由 Cron 自动恢复 Runner，结果主动发送至原微信会话并镜像进会话历史
- **主动回传**：新增 `company-product-result-notifier` 无模型 Cron；仅收到明确发送成功才标记交付，失败使用指数退避
- **内容适配器**：文章/视频创作从主 Agent 注释升级为独立后台执行；文章 Run `3dc43b1c-22e7-4901-ac05-ebd6164080f5` 生成 `draft.md` 与 `qa-report.md`，24/24 测试真实全绿，三道 Gate 通过
- **审批边界**：管理态问题不误触生产；肯定式发布/上传仍审批，明确“不发布”可继续内部草稿生产
- **知识晋升**：新增验证、脱敏、披露状态、人工审批四道门；首次扫描 66 条 active 知识，64 条敏感阻断、2 条待验证、0 条自动晋升
- **财务分账**：新增证据化实际交易、赏金 forecast、模型用量快照；首次同步实际收入 $0，forecast `$10,850–$30,150`，60 个会话待定价
- **视频 E2E**：`video-e2e-20260715` 生成脚本、分镜和制作计划；明确 Pixelle 运行时未激活，未生成/上传 MP4
- **回归**：自动化测试 30/30 通过

## [2026-07-15] 流程自动化 | 公司 Router 接入安全蜂群

- **新增**：[[automation/README|公司自动路由]]、`company_router.py`、Hermes Worker 执行器与测试
- **Hermes 配置**：修复默认 Skill `sworm-knowledge-hook` → `swarm-knowledge-hook`；注册并批准 `pre_llm_call` Hook
- **安全门禁**：外部主动探测缺少授权时不分发；外部发布、提交、付款、删除保持人工审批
- **数据库迁移**：备份正式 DB 后补齐 `spawn_requests.dedup_key/claimed_by` 和 `swarm_runs.strategy_version`
- **E2E 验证**：Run `40829ad1-8706-4869-beec-d44a0aa57c3d`，2 个 analyst + 1 个 reporter，3/3 completed
- **结果回注**：同一会话下一轮 Hook 成功读取 completed Run 并注入结果，未创建重复任务

## [2026-07-15] 基础设施部署 | 公司资产与安全产品线融合

- **部署来源**：远端 `dikw-migration-20260714.tar.gz`，SHA-256 校验通过
- **部署目录**：`/home/pwn/workspace/company`
- **新建页面**：[[DEPLOYMENT]], [[operations/business-lines/security-exploration]], [[projects/security-exploration/README]]
- **更新页面**：[[Home]], [[DASHBOARD]], [[index]], [[operations/README]], [[operations/business-lines/README]], [[projects/README]], [[product/README]]
- **数据边界**：HackerOne 证据与未披露漏洞不进入公共 Wiki，只登记运行位置
- **摘要**：公司总部知识库已部署到当前工作站，原蜂群与 HackOne 能力正式降级并登记为公司安全探索产品线

## [2026-07-05] QA 修复 + 内容迁移 | ai-edu-series 治理

- **QA 修复**:
  - [[projects/ai-edu-series/articles/01-agent-engineering-intro|#01]] — 标题数字统一（120→"不到 200 行"）、性能数据添加出处标注
  - [[projects/ai-edu-series/articles/02-tool-schema-design|#02]] — 内联图片确认存在、与 #01 重叠内容精简为引用
- **内容迁移**: ai-edu-series 目录中原 #03-#06（文章精选类内容）迁移至 [[projects/article-curation/TRACKING|article-curation]]，重编号为 #05-#08。#07 与 article-curation #03 重复，跳过
- **更新文件**: [[projects/ai-edu-series/TRACKING]], [[projects/ai-edu-series/articles/INDEX]], [[projects/article-curation/TRACKING]], [[projects/article-curation/articles/INDEX]], [[DASHBOARD]]
- **摘要**: 解决 ai-edu-series 目录中原创教育内容和文章精选内容混放的问题。QA 报告中所有 🔴/🟡 问题全部修复

## [2026-07-05] 知识库整理 | 全量更新

- **新建文件**:
  - [[engineering/README]], [[design/README]], [[marketing/README]], [[product/README]], [[sales/README]], [[finance/README]], [[operations/README]], [[strategy/README]] — 8 个部门首页
  - [[projects/article-curation/TRACKING]] — 文章精选项目追踪
- **更新文件**:
  - [[DASHBOARD]] — 活跃项目 2→3，文章数更新，新增里程碑
  - [[projects/README]] — 新增 article-curation 项目
  - [[index]] — 注册部门 README、策略文档、新项目
  - [[Home]] — 重写链接确保无死链
- **摘要**: 知识库从「有框架无内容」整治为「框架完整+内容同步」。8 个空部门目录全部补齐 README.md，仪表盘数据与实际进度对齐，所有 wikilink 可点击。

## [2026-07-04] 初始化 | LLM Wiki 方法论

- 新建页面: [[wiki/llm-wiki-methodology]]
- 新建页面: [[index]], [[log]]
- 来源: Karpathy "LLM Wiki" gist
- 摘要: 建立公司 LLM Wiki 知识库基础设施，采用三层架构（raw/wiki/schema）

## [2026-07-04] 基础设施 | 项目追踪系统

- 新建页面: [[DASHBOARD]]
- 新建页面: [[projects/README]]
- 新建页面: [[projects/ai-edu-series/TRACKING]]
- 更新页面: [[index]], [[Home]]
- 摘要: 建立公司级项目追踪系统
