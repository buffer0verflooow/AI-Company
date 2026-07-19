---
tags: [methodology, llm, wiki, knowledge-management]
created: 2026-07-04
updated: 2026-07-04
sources: [raw/karpathy-llm-wiki.md]
---

# LLM Wiki 方法论

Andrej Karpathy 提出的用 LLM 构建持久化个人/团队知识库的方法论。

## 核心区别：Wiki vs RAG

| RAG | LLM Wiki |
|-----|----------|
| 每次查询从原始文档检索 | 知识提前编译到 wiki 中 |
| 无积累，每次重新推导 | 复利增长，越用越丰富 |
| LLM 是查询引擎 | LLM 是**全职 wiki 维护者** |

## 三层架构

1. **raw/** — 原始资料，不可变，LLM 只读
2. **wiki/** — LLM 生成和维护的 markdown 页面
3. **Schema** (.hermes.md) — 告诉 LLM 如何维护 wiki 的操作手册

## 三个操作

### 摄入 (Ingest)
新资料 → LLM 读取 → 写摘要页 → 更新 index → 更新关联页 → 记 log。一个资料可能触达 10-15 个页面。

### 查询 (Query)
提问 → 读 index → 读相关页 → 综合回答（附 `[[页面链接]]` 引用） → 有价值的答案存回 wiki。

### 巡检 (Lint)
定期检查：页面矛盾、过时声明、孤立页面、缺失交叉引用、数据缺口。

## 关键文件

- **index.md** — 内容索引，所有页面的目录+摘要，查询时第一入口
- **log.md** — 时间线日志，追加式记录所有操作

## 工具链

- **Obsidian** — Wiki IDE，提供 graph view、wikilink、Dataview、Marp
- **Obsidian Web Clipper** — 网页转 markdown，快速积累原始资料
- **Git** — 版本控制，每次大操作可 commit
- **qmd** — 本地 markdown 搜索引擎（BM25 + 向量 + LLM 重排）

## 为什么有效

维护知识库最难的不是阅读和思考，而是**整理**——更新交叉引用、保持摘要最新、标记矛盾。LLM 不会厌倦、不会遗忘、能一次更新 15 个文件。维护成本趋近于零。

人类的职责：策展资料、指导分析、提出好问题、思考意义。
LLM 的职责：剩下的一切。
