# kk_OpenMoviesKnowllage

本仓库包含两个子项目：

- **FinReport2Video** — 自研金融报告转视频工具
- **OpenMAIC** — 开源 AI 课堂框架（基于原仓库二次定制）

---

## FinReport2Video

### 项目简介

将金融研报 PDF 全自动转换为带语音讲解的 1080p MP4 视频。核心流程：PDF 解析 → LLM 润色讲稿 → edge-tts 语音合成 → 本地动效背景生成 → moviepy 合成输出。

```
FinReport2Video/
├── main.py              # CLI 入口
├── api_server.py        # FastAPI 后端（Web UI 服务）
├── config.py            # 配置（从 .env 加载敏感 Key）
├── requirements.txt
├── start_web.sh         # 一键启动脚本
├── .env                 # 敏感配置（不提交 Git）
├── input/               # Web 上传 PDF 存放目录
├── output/              # 生成视频输出目录
├── temp/                # 中间文件缓存
├── web/                 # Next.js 前端
│   ├── app/page.tsx     # 主页面（拖拽上传 + 任务卡片 + 播放器）
│   └── app/api/py/      # 反向代理（→ FastAPI:8765）
└── pipeline/
    ├── pdf_parser.py    # PDF 解析、章节分页、元信息提取
    ├── script_writer.py # LLM 讲稿润色
    ├── tts_generator.py # 语音合成（edge-tts 主 / Qwen TTS 备用）
    ├── video_generator.py   # 本地背景生成（Ken Burns 动效 + 片头专用）
    ├── video_composer.py    # 视频合成（字幕、信息卡片、交叉淡化转场）
    ├── image_fetcher.py     # PDF 原图提取 / AI 配图
    └── prompt_builder.py    # 视频 prompt 构建
```

### 核心功能

| 功能 | 说明 |
|------|------|
| PDF 解析 | PyMuPDF 按大标题自动分章节，提取文字、图片、截图 |
| 元信息提取 | 扫描前 2 页 + 末 3 页，提取标题、摘要、分析师、日期、数据来源 |
| LLM 润色 | DeepSeek 将原文改写为适合播报的讲稿（可 `--skip-llm` 跳过）|
| 语音合成 | edge-tts（免费）主力，Qwen TTS 备用；支持逐字时间戳驱动字幕 |
| 背景视频 | 本地生成 Ken Burns 镜头推拉 + 片头专用期指 K 线动效（零 API 费用）|
| 视频合成 | 1080p，顶部章节标题栏、关键信息浮动卡片、底部字幕、0.5s 交叉淡化转场 |
| 片头页 | 报告标题 + 摘要 + 分析师 + 日期 + 数据来源 + 动态背景，时长跟随音频 |
| Web UI | 深色金融风界面，拖拽上传 PDF，实时查看进度日志，内嵌播放 / 下载 |

### 快速开始

**环境要求：** Python 3.10+，Node.js 18+

```bash
cd FinReport2Video

# 安装 Python 依赖
pip install -r requirements.txt

# 复制并填写配置
cp .env.example .env   # 编辑 .env，写入 API Key

# CLI 模式
python main.py --input /path/to/report.pdf

# Web UI 模式（浏览器访问 http://localhost:3000）
bash start_web.sh
```

**CLI 常用参数：**

```bash
python main.py --input report.pdf              # 默认：LLM润色 + AI配图
python main.py --input report.pdf --skip-llm  # 快速模式：跳过 LLM
python main.py --input report.pdf --pages 1-5  # 只处理前5页
python main.py --list-voices                   # 查看可用音色
```

### 配置说明（.env）

```ini
# LLM（讲稿润色）
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-chat

# 通义万相（AI 配图，可选）
QWEN_IMAGE_API_KEY=sk-xxx
```

> 背景视频和语音使用免费方案，不配置 Key 也能生成视频（AI 配图功能不可用）。

---

## OpenMAIC

### 项目简介

[OpenMAIC](https://github.com/THU-MAIC/OpenMAIC) 是清华大学开源的多智能体 AI 课堂框架，将任意话题或文档生成交互式课堂内容（PPT、测验、模拟、白板讲解等）。本仓库在原版基础上做了以下本地化定制。

### 相对原版的修改内容

#### 1. 国内 LLM 切换

- 将默认模型从 `google:gemini-2.0-flash` 切换为 `deepseek:deepseek-chat`
- 配置文件：`.env.local` 中 `DEFAULT_MODEL=deepseek:deepseek-chat`
- 原因：Google Gemini 在国内网络不可直接访问

#### 2. PDF 解析修复

- 禁用 MinerU 解析器（API 404 失效），清空 `PDF_MINERU_API_KEY` / `PDF_MINERU_BASE_URL`
- 系统自动降级使用内置 `unpdf` 解析器
- 修改文件：`.env.local`

#### 3. 多模态图片数量上限提升

- 将 `lib/constants/generation.ts` 中 `MAX_VISION_IMAGES` 从 `20` 提升至 `50`
- 允许 LLM 在解析 PDF 时接收更多页面原图，改善长文档理解效果

#### 4. API Key 配置（.env.local）

所有已配置的第三方服务：

| 服务类别 | Provider | 说明 |
|----------|----------|------|
| LLM | DeepSeek | 主力 LLM，国内稳定 |
| LLM | Kimi (Moonshot) | 备用 LLM |
| TTS | Qwen (通义千问) | 语音合成 |
| ASR | Qwen (通义千问) | 语音识别，`qwen3-asr-flash` |
| 图片生成 | Qwen Image (通义万相) | AI 配图 |
| 视频生成 | Kling (可灵) | 文生视频 |
| 网页搜索 | Tavily | 联网搜索增强 |

### 本地启动

```bash
cd OpenMAIC
npm install -g pnpm   # 若未安装
pnpm install
pnpm dev              # 访问 http://localhost:3000
```

---

## 目录结构

```
kk_OpenMoviesKnowllage/
├── FinReport2Video/    # 自研：金融报告转视频工具
├── OpenMAIC/           # 开源框架：多智能体 AI 课堂（本地定制版）
└── README.md
```
