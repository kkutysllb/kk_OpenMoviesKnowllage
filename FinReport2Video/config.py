"""
FinReport2Video 配置文件
从项目根目录的 .env 文件加载敏感配置（API Key 等）。
"""
import os
from pathlib import Path

# ── 加载 .env 文件（优先级低于系统环境变量）─────────────────────────────────────
def _load_dotenv():
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            # 系统环境变量优先，.env 不覆盖
            if key and key not in os.environ:
                os.environ[key] = value

_load_dotenv()

# ── LLM (DeepSeek) ────────────────────────────────────────────────────────────
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# ── 通义万相图片生成 ────────────────────────────────────────────────────────────
QWEN_IMAGE_API_KEY = os.getenv("QWEN_IMAGE_API_KEY", "")
QWEN_IMAGE_BASE_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis"

# ── 可灵文生视频（已废弃，改用通义万象）────────────────────────────────────────────
# KLING_API_KEY 已不再使用，改用 QWEN_IMAGE_API_KEY 调用通义万象 wanx2.1-t2v-turbo

# ── TTS ───────────────────────────────────────────────────────────────────────
TTS_VOICE = "zh-CN-XiaoxiaoNeural"   # 女声，清晰专业，适合金融播报
TTS_RATE = "+0%"                      # 语速，可调 +10% 加快
TTS_VOLUME = "+0%"

# ── 视频参数 ───────────────────────────────────────────────────────────────────
VIDEO_FPS = 24
VIDEO_SIZE = (1920, 1080)             # 1080p

# ── 目录 ──────────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
TEMP_DIR = os.path.join(BASE_DIR, "temp")
INPUT_DIR = os.path.join(BASE_DIR, "input")

# ── PDF 解析 ──────────────────────────────────────────────────────────────────
PDF_DPI = 150                          # 页面截图分辨率
PDF_MIN_IMAGE_SIZE = 5000              # 忽略小于此像素数的图片（去除噪点小图）

# ── LLM 讲稿 ─────────────────────────────────────────────────────────────────
SCRIPT_MIN_CHARS = 100                 # 讲稿最少字数
SCRIPT_MAX_CHARS = 300                 # 讲稿最多字数

# ── 字幕 ──────────────────────────────────────────────────────────────────────
SUBTITLE_FONT_SIZE = 32
SUBTITLE_CHARS_PER_LINE = 40          # 每行字幕字数
SUBTITLE_MARGIN_BOTTOM = 60           # 字幕距底部像素
