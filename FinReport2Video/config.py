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

# ── LLM (统一配置) ────────────────────────────────────────────────────────────
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")
LLM_MODEL = os.getenv("LLM_MODEL", "")

# ── 通义万相图片生成 ────────────────────────────────────────────────────────────
QWEN_IMAGE_API_KEY = os.getenv("LLM_API_KEY", "")   # MiniMax 与 LLM 共用同一个 Key
QWEN_IMAGE_BASE_URL = "https://api.minimaxi.com/v1/image_generation"
MINIMAX_IMAGE_MODEL = "image-01"

# ── 可灵文生视频（已废弃，改用通义万象）────────────────────────────────────────────
# KLING_API_KEY 已不再使用，改用 QWEN_IMAGE_API_KEY 调用通义万象 wanx2.1-t2v-turbo

# ── TTS ───────────────────────────────────────────────────────────────────────
TTS_VOICE = "zh-CN-XiaoxiaoNeural"   # 女声，清晰专业，适合金融播报
TTS_RATE = "+0%"                      # 语速，可调 +10% 加快
TTS_VOLUME = "+0%"

# ── 目录 ──────────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
TEMP_DIR = os.path.join(BASE_DIR, "temp")
INPUT_DIR = os.path.join(BASE_DIR, "input")

# ── 视频参数 ───────────────────────────────────────────────────────────────────
VIDEO_FPS = 24                        # 最终输出视频帧率
BG_VIDEO_FPS = 15                     # 背景视频帧率（15fps 充足，编码量减少37%）
VIDEO_SIZE = (1920, 1080)             # 1080p

# 统一背景视频配置
DEFAULT_BG_VIDEO = os.path.join(BASE_DIR, "assets", "default_bg.mp4")  # 默认动态背景
TABLE_RENDER_STYLE = "dark"           # 表格渲染风格: dark/light

# 数字人配置
DIGITAL_HUMAN_DIR = os.path.join(BASE_DIR, "assets", "digital_human")  # 数字人素材目录
DIGITAL_HUMAN_ENABLED = False        # 是否启用数字人（需先添加透明背景素材）
DIGITAL_HUMAN_POSITION = "bottom-left"  # 位置: bottom-right, bottom-left, top-right, top-left
DIGITAL_HUMAN_SIZE = 0.25             # 数字人大小（相对于视频宽度的比例）

# 如果默认背景不存在，使用代理函数返回 None
def get_default_bg_video():
    if os.path.exists(DEFAULT_BG_VIDEO):
        return DEFAULT_BG_VIDEO
    return None

def get_digital_human_videos() -> list:
    """获取可用的数字人视频素材列表"""
    if not os.path.exists(DIGITAL_HUMAN_DIR):
        return []
    videos = []
    for f in os.listdir(DIGITAL_HUMAN_DIR):
        if f.lower().endswith(('.mp4', '.mov', '.webm')):
            videos.append(os.path.join(DIGITAL_HUMAN_DIR, f))
    return videos

# ── LLM 讲稿 ─────────────────────────────────────────────────────────────────
SCRIPT_MIN_CHARS = 1000                # 讲稿最少字数（确保内容充实）
SCRIPT_MAX_CHARS = 8000                # 讲稿最多字数（防止截断）

# ── 字幕 ──────────────────────────────────────────────────────────────────────
SUBTITLE_FONT_SIZE = 32
SUBTITLE_CHARS_PER_LINE = 40          # 每行字幕字数
SUBTITLE_MARGIN_BOTTOM = 60           # 字幕距底部像素
