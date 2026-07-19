# QA 报告 — #02 工具定义与 Schema——让 Agent 学会使用工具

> 审核日期: 2026-07-05
> 审核员: Hermes Agent (Gate 1+2+3)

---

## Gate 1: 事实核查

### 链接可达性

| 链接 | 状态 | 备注 |
|------|:---:|------|
| github.com/rohitg00/ai-engineering-from-scratch | ✅ 200 | — |
| github.com/.../phases/14-agent-engineering | ✅ 200 | — |
| arxiv.org/abs/2302.04761 (Toolformer) | ✅ 200 | — |
| gorilla.cs.berkeley.edu/leaderboard.html | ✅ 200 | — |
| composio.dev/blog/how-to-build-tools-for-ai-agents | ✅ 200 | — |
| docs.databricks.com/.../agent-system-design-patterns | ❌ 301 | **URL 路径已变更** → /aws/en/agents/agent-system-design-patterns |
| platform.openai.com/docs/guides/function-calling | ⚠️ 403 | Cloudflare 拦截 curl，URL 本身可能正确 |
| docs.anthropic.com/.../tool-use/overview | ⚠️ geo-block | 重定向到 claude.com/app-unavailable-in-region |
| ai.google.dev/gemini-api/docs/function-calling | ✅ 200 | — |

### 无法独立验证的声明

| 声明 | 位置 | 建议 |
|------|------|------|
| Composio 实测: 62% → 89% (改描述后) | L45 | 标注"据 Composio 2025 年博客数据" |
| 原子工具 vs 单体工具准确率差 15-30% | L453 | 标注来源或给出具体实验条件 |
| 清晰错误消息让弱模型重试次数减半 | L408 | 标注"实测数据"，补充实验条件 |
| BFCL V4 场景占比 | L207-213 | 需对照 BFCL 官网核实 |
| 强模型首次重试成功率接近 100% | L408 | 过于绝对，建议改为"大幅提升" |

### 已验证的事实

- ✅ Toolformer 论文: Timo Schick 等人 2023 年发表，标题正确
- ✅ BFCL V4 由伯克利维护
- ✅ Composio 博客 URL 可达
- ✅ snake_case vs camelCase 的 tokenization 分析合理
- ✅ 三大 Provider Schema 差异描述准确（L474-484）
- ✅ Python `bool` 是 `int` 的子类 (L292) — 技术事实正确

---

## Gate 2: 内容审校

### 🟡 需要修正

**1. 违反风格指南: "📦 读完你会得到" (L22)**

同 #01，违反 wechat-publisher skill 禁止列表。建议改为 `📦 内容地图`。

**2. Databricks URL 路径变更 (L583)**

> 当前: `docs.databricks.com/aws/en/generative-ai/guide/agent-system-design-patterns`
> 正确: `docs.databricks.com/aws/en/agents/agent-system-design-patterns`

**3. 内联图片引用 (L58)**

> `![工具 Schema 三要素](../assets/FIG_02.01-tool-schema-three-elements.png)`

需确认该文件存在。wenyan 发布时内联图片可能不会自动上传到微信 CDN。

### 🟢 良好实践

- 结构遵循连载模板 ✓
- 前置知识引用（L227）恰当 ✓
- 代码示例生产级质量（dataclass + 类型注解 + 完整校验） ✓
- 「Use when... Do not use for...」模式教学清晰 ✓
- 沙盒化章节（L492-504）补充了安全视角 ✓
- Schema 自查清单（L544-559）实用 ✓
- 国产模型对比表格与 #01 一致 ✓

### 潜在问题

**4. 与 #01 的内容重叠 (L384-396)**
错误处理的设计哲学在两篇文章中重复完整解释。第二篇可以精简为一句引用 #01，避免读者感到重复。

**5. 弱模型/强模型区分不够精确 (L408)**
"弱模型"和"强模型"是定性描述，建议引用具体模型名或评测数据。

---

## Gate 3: 主编终审

| 检查项 | 状态 | 备注 |
|--------|:---:|------|
| 选题与 ai-edu-series 定位一致 | ✅ | Phase 14 工具 Schema 篇 |
| 源项目仍可访问 | ✅ | GitHub 200 OK |
| 无敏感/争议内容 | ✅ | — |
| 封面图存在 | ✅ | cover-phase-14-tool-schema.png + thumb |
| 文末含源项目链接 | ✅ | — |
| MIT License 声明 | ✅ | L12, L590 |
| 下一篇预告正确 | ✅ | 「函数调用：Agent 与外部世界的桥梁」 |
| 上一篇引用正确 | ✅ | 正确引用了 #01 |

---

## 修正清单（发布前必须完成）

- [ ] 🟡 L22: "读完你会得到" 改为 "内容地图"
- [ ] 🟡 L583: Databricks URL 改为 /aws/en/agents/... 路径
- [ ] 🟡 L58: 确认 FIG_02.01-tool-schema-three-elements.png 存在
- [ ] ⚠️ 无法验证的性能数据添加出处标注
- [ ] 💡 L384-396: 考虑精简与 #01 重叠的错误处理解释
