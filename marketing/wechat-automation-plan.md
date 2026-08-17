---
tags: [department, marketing, operations]
created: 2026-08-09
updated: 2026-08-09
---

# 公众号自动化运营方案

> 日期: 2026-08-09
> 现状盘点: 公司已有内容产线、市场雷达、wechat_push.py、analytics.py、8 个 cron 任务
> 结论: 公众号可以做到"选题→写稿→审核→草稿→发布→数据回捞"全链路自动化，唯一人工兜底点是发布确认

---

## 0. 结论（TL;DR）

1. **能自动化，且公司已实现 70%**：选题（市场雷达）、写稿（内容产线）、排版推送（wechat_push.py）、数据回捞（analytics.py）都已跑通
2. **缺最后一环**：草稿箱 → 发布。官方 freepublish/submit 接口可全自动，但**需要认证公众号 + 配置 IP 白名单**；个人主体号建议保留"人工点发布"兜底（发布是可逆性最低的动作，人工确认反而安全）
3. **推荐"全自动 + 发布确认点"混合模式**：一切自动化，唯独发布那一下人工点（或 API 发布后立即人工检查），兼顾效率与风控
4. **风险红线**：非官方的"模拟人工发布"（cookie 方案）有封号风险，不推荐

---

## 1. 自动化能力全景（官方 API）

| 环节 | 官方接口 | 现状 | 说明 |
|---|---|---|---|
| 选题 | 市场雷达 cron（已有） | ✅ 已自动化 | 每日 08:30 生成市场脉冲，桥接到选题池 |
| 写稿 | 内容产线（已有） | ✅ 已自动化 | Codex 撰写 → humanizer → QA Gate |
| 封面 | generate_cover.py | ✅ 已自动化 | 参数化生成 |
| 排版 | wechat_push.py | ✅ 已自动化 | markdown → HTML + CSS 内联 → 草稿箱 |
| 推送草稿 | draft/add | ✅ 已自动化 | wechat_push.py 已实现（凭证已持久化） |
| **自动发布** | **freepublish/submit** | ❌ 未实现 | 本方案核心新增点 |
| 数据回捞 | datacube API | ✅ 已自动化 | analytics.py 已实现 |
| 菜单/合集 | 自定义菜单 API | ⚠️ 半自动 | 手动配置，低频不急于自动化 |
| 关键词回复 | 自动回复 API | ⚠️ 半自动 | 手动配置即可 |

**结论：核心内容链路（选→写→排→推→回捞）已自动化，唯一缺口是 freepublish。**

---

## 2. 全自动发布实现（freepublish/submit）

### 2.1 接口说明（已核实，2026-08-09）✅

- 接口: `POST https://api.weixin.qq.com/cgi-bin/freepublish/submit?access_token=ACCESS_TOKEN`
- 功能: 将草稿箱中指定 media_id 的图文草稿提交发布
- 前置条件: **已认证公众号（个人/企业）+ 开启开发者模式 + 配置 IP 白名单** ⚠️
- 流程: draft/add 存草稿 → freepublish/submit 发布 → freepublish/get 查发布状态

### 2.2 公司落地方案

```python
# wechat_publish.py（新增，复用 wechat_push.py 的 token 逻辑）
def publish_draft(media_id):
    """发布草稿箱中的图文"""
    token = get_stable_token()  # 复用现有逻辑
    url = f"https://api.weixin.qq.com/cgi-bin/freepublish/submit?access_token={token}"
    data = json.dumps({"media_id": media_id}).encode()
    resp = json.loads(urllib.request.urlopen(urllib.request.Request(url, data=data)).read())
    # 返回 publish_id，轮询 freepublish/get 确认状态
```

### 2.3 个人主体号的现实约束 ⚠️

- freepublish 官方文档标注需认证公众号；个人主体号不支持微信认证 → **接口可能不可用，需实测**
- 兜底路径（同样全自动）：
  - 方案一：wechat_push.py 已推送草稿箱 → 人工在 mp.weixin.qq.com 点一次发布（10 秒）
  - 方案二：微信官方「手机端一键发布」新功能（个人账号支持，2026 上线）
  - 方案三：认证企业主体后启用 freepublish 全自动（与多号矩阵计划同一条路）

---

## 3. 推荐运营架构（全自动 + 发布确认点）

```
市场雷达 cron (08:30) ──选题信号──▶ 选题池（赛道标签）
                                      │
内容产线 ──Codex 写稿──▶ humanizer ──▶ QA Gate（三关）
                                      │
wechat_push.py ──draft/add──▶ 草稿箱（含封面+CSS 内联）
                                      │
                    ┌─── 人工点发布（10 秒，每日 1 次）◀── 兜底
                    │        或 freepublish API（认证后）
                    ▼
                   发布成功
                    │
analytics.py cron ──datacube──▶ article_performance.db
                    │
                TVCR 每日复盘 ──▶ 反哺选题池
```

### 3.1 新增 cron 任务设计

| 任务 | 时间 | 动作 |
|---|---|---|
| 选题信号 | 08:30（已有） | 市场雷达 → 选题池 |
| 写稿+审核 | 09:00-18:00（已有内容产线触发） | Codex → humanizer → QA |
| 推送草稿 | 推文完成后（已有 wechat_push.py） | draft/add 入草稿箱 |
| **发布提醒** | 19:00（新增，no_agent 脚本） | 检查草稿箱待发布 → 通知"该点发布了" |
| 数据回捞 | 次日 09:00（已有 analytics） | datacube → 性能库 |
| TVCR 复盘 | 周一（已有） | 表现反哺选题 |

### 3.2 半自动替代：每日 1 次人工发布

- 全部内容生成自动化，每天只需登录一次点发布（个人主体号当前最优形态）
- 这 10 秒人工操作保留的好处：发布是不可逆的对外动作，人工确认 = 最后一道安全闸（内容合规、封面、链接全对再发）

---

## 4. 现有自动化资产清单（盘点结果）

| 资产 | 位置 | 状态 |
|---|---|---|
| 市场雷达 | automation/market_radar.py + cron 08:30 | ✅ 运行中（8 轮真实采集/170+ 信号） |
| 内容产线 | operations/content-executor（Codex + humanizer + QA） | ✅ 运行中（c14347e7 等任务已产出） |
| 推送工具 | scripts/wechat_push.py | ✅ 运行中（草稿箱 + 封面） |
| 数据回捞 | projects/wechat-publisher/scripts/analytics.py | ✅ 可用（datacube API） |
| 封面生成 | scripts/generate_cover.py | ✅ 可用 |
| 自动修复 | company-daily-auto-fix cron 04:00 | ✅ 运行中 |
| 每日运营 | company-daily-operator cron 09:00 | ✅ 运行中 |
| 知识桥 | obsidian-swarm-kb-bridge cron 05:00 | ✅ 运行中 |

---

## 5. 自动化不能替代的（诚实边界）

1. **发布确认**：对外不可逆动作，保留人工闸
2. **选题判断的"灵感"部分**：雷达给信号，但"这篇要打什么角度"仍需人/Agent 决策
3. **读者互动**：留言回复可半自动（关键词），但深度互动需人工
4. **合规判断**：AI 写的内容是否合规，QA Gate 已做一关，发布前人工再扫一眼

---

## 6. 风险与合规

| 风险 | 等级 | 缓解 |
|---|---|---|
| freepublish 个人号不可用 | 🟡 | 先实测；不可用则人工点发布兜底 |
| 非官方模拟发布封号 | 🔴 | 一律不用 cookie/模拟方案 |
| 全自动发布出错（内容/封面） | 🟡 | 保留发布确认闸 |
| API 频率限制 | 🟢 | 每日 1-2 次发布远低于限制 |
| IP 白名单配置 | 🟢 | 一次性配置（服务器 IP） |

---

## 7. 依据与局限

- ✅ freepublish/submit 接口：微信开放社区官方文档（2026-08-09 核验）
- ✅ 公司资产盘点：本机 automation/ + projects/wechat-publisher/ + cron jobs.json（2026-08-09）
- ✅ 数据获取限制：个人主体号无 datacube 权限（48001），仅能后台手动导出——import_wx_stats.py 已实现"导出 zip → 自动入库"链路（2026-08-09 实测通过）
- ⚠️ 个人主体号 freepublish 可用性：官方文档标注需认证公众号，个人号未实测，待验证
- ⚠️ 手机端一键发布功能：新闻提及（微信公众平台官方），细节待实测
