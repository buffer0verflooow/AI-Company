---
tags: [operations, business-line, video]
created: 2026-07-05
---

# 🎬 视频创作产线

> 将技术内容转化为视频，通过 B站/小红书/抖音等视频平台分发。

## 当前状态

🟡 **预生产已自动化，渲染环境待激活** — 公司 Router 可自动生成视频脚本、逐镜头分镜和制作计划；Pixelle 的 `uv`、正式 `config.yaml`、媒体模型与 TTS 凭证尚未配置，因此不得声称 MP4 已渲染。

2026-07-15 本地 E2E `video-e2e-20260715` 已生成：

- `video-script.md`
- `storyboard.md`
- `production-plan.md`

该 E2E 未上传任何平台，也未生成 MP4。

> 2026-07-07: 部署 Y2A-Auto（Docker @ localhost:5000），覆盖 B站上传、Whisper 字幕、字幕翻译/烧录、AI 元信息生成。部署路径：`~/workspace/y2a-auto/`。

## 工作流（规划）

```
选题 → 脚本撰写 → 配音 → 画面制作 → 剪辑合成 → 封面 → 分发
```

| 阶段 | 工具/代理 | 产出 |
|------|------|------|
| 选题 | 内容策略 | 选题确认（单篇/系列） |
| 脚本 | `agency_agents_delegate` → content-creator | 分镜脚本 .md |
| 配音 | TTS（text-to-speech） | 音频轨道 |
| 画面 | 录屏 / Manim 动画 / 代码走读 | 视频素材 |
| 剪辑 | FFmpeg / 剪映 | 成品 MP4 |
| 封面 | Python PIL | 视频封面图 |
| 后期增强 | **Y2A-Auto**（字幕生成/翻译/烧录 + AI 标题/简介/标签/分区） | 字幕 .srt / 烧录版 MP4 / 元信息 |
| 分发 | **Y2A-Auto**（B站 QR 扫码上传） | 各平台发布 |

## 视频形态

| 类型 | 时长 | 适用场景 | 制作难度 |
|------|:---:|------|:---:|
| 代码走读 | 15-25 min | ai-edu-series 系列讲解 | ⭐⭐ |
| 概念动画 | 3-8 min | Agent Loop、Attention、反向传播 | ⭐⭐⭐ |
| 短文拆解 | 1-5 min | article-curation 精华版 | ⭐ |
| 直播 Coding | 60-120 min | 实时写代码 + 答疑 | ⭐ |

## 首发建议

从 ai-edu-series 已有文章出发：

| 优先级 | 内容 | 来源文章 | 视频形态 |
|:---:|------|------|------|
| 1 | Agent 的灵魂只有一个不到 200 行的循环 | #01 | 代码走读 + 概念动画 |
| 2 | 工具定义与 Schema——让 Agent 学会使用工具 | #02 | 代码走读 |
| 3 | 函数调用——Agent 拿到地图后怎么开车 | #03 | 代码走读 |

## 目标平台

| 平台 | 优先级 | 内容适配 |
|------|:---:|------|
| **B站** | ⭐⭐⭐⭐⭐ | 长视频主阵地 |
| **小红书** | ⭐⭐⭐ | 1-3 min 精华剪辑 |
| **抖音** | ⭐⭐ | 1 min 以内高光切片 |

## 待解决的基建问题

- [ ] 视频录制工具链（OBS / VS Code 录屏）
- [x] 字幕生成（~~whisper.cpp / 飞书妙记~~ → **Y2A-Auto Whisper API** ✅）
- [x] B站上传 API（~~biliup CLI~~ → **Y2A-Auto bilibili_uploader** ✅，支持 QR 扫码登录）
- [ ] Manim 动画渲染环境
- [ ] 配音方案（TTS 自动 vs 人工录制）
- [x] 字幕翻译与烧录（**Y2A-Auto** ✅，OpenAI API 翻译 + libass 烧录）
- [ ] 视频素材存储与版本管理

## 关联代理（代理池中可用）

| 工位 | 代理 | Division | 能力 |
|------|------|------|------|
| 📝 脚本撰写 | `technical-writer` | engineering | 技术内容转脚本 |
| 🎨 视觉叙事 | `visual-storyteller` | design | 复杂信息可视化 |
| ✂️ 视频剪辑 | `short-video-editing-coach` | marketing | 全流程后期（CapCut/PR/达芬奇） |
| 🎤 语音/字幕 | `voice-ai-integration-engineer` | engineering | Whisper 转录、字幕生成管线 |
| 📊 平台优化 | `video-optimization-specialist` | marketing | YouTube/B站 算法优化 |
| 🏮 B站运营 | `bilibili-content-strategist` | marketing | UP主增长、弹幕文化、分区策略 |
| 📢 内容策划 | `content-creator` | marketing | 跨平台内容策略 |

### ⚠️ 缺工人

| 工位 | 缺失原因 | 替代方案 |
|------|------|------|
| 🎙️ 配音（TTS） | 代理池无 TTS 代理 | 用 Hermes 的 `text_to_speech` 工具 |
| 🎬 动画制作 | 无 Manim 代理 | 通过 codex 代理生成 Manim 脚本 |
| 📺 直播运营 | 无直播代理 | 暂无 |

## Y2A-Auto 工具详情

> 部署：Docker @ `localhost:5000`，配置文件 `~/workspace/y2a-auto/config/config.json`

### 视频产线会用到的能力

| 能力 | 配置项 | 说明 |
|------|------|------|
| B站上传 | 设置页「扫码登录」 | bilibili QR 码登录，无需手动管理 Cookie |
| 字幕生成 (ASR) | `SPEECH_RECOGNITION_ENABLED=true`，`SPEECH_RECOGNITION_PROVIDER=whisper` | 传入 MP4，输出 .srt 字幕 |
| 字幕翻译 | `SUBTITLE_TRANSLATION_ENABLED=true` | OpenAI API 翻译，支持中/英互转 |
| 字幕烧录 | `SUBTITLE_EMBED_IN_VIDEO=true` | 字幕嵌入视频轨道 |
| AI 元信息 | `TRANSLATE_TITLE/GENERATE_TAGS/RECOMMEND_PARTITION=true` | 自动生成 B站标题/标签/分区 |
| 视频转码 | `VIDEO_ENCODER=auto` | CPU/NVIDIA/Intel/AMD 硬件编码 |

### 原创内容使用方式

Y2A-Auto 默认流程是为「搬运」设计的（从 YouTube URL 开始）。用于原创内容时，有两种用法：

1. **作为独立工具调用**：不经过 Web UI 的任务流，直接调用 `modules/` 中的单个模块
   - `bilibili_uploader.py` → 传本地 MP4 + 元信息，上传 B站
   - `speech_recognition.py` → 传音频/视频，返回字幕
   - `subtitle_translator.py` → 传入字幕 + OpenAI key，返回翻译
2. **Web UI 精简流程**：手动审核模式下跳过下载/ASR，只做上传

### 待配置

- [ ] B站扫码登录（进 http://localhost:5000 设置页）
- [ ] OpenAI API Key（用于字幕翻译 + AI 元信息）
- [ ] `UPLOAD_TARGET_DEFAULT` 改为 `bilibili`
