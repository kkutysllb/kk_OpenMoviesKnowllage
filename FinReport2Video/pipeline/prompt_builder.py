"""
视频背景 Prompt 生成模块

根据页面讲稿内容，调用 DeepSeek LLM 生成适合可灵文生视频的中文场景描述。
若 LLM 失败，自动根据关键词匹配预设模板兜底。
"""
import os
import sys
import re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL

try:
    from openai import OpenAI
    _openai_available = True
except ImportError:
    _openai_available = False

# ── 预设 Prompt 模板（关键词 → 场景）────────────────────────────────────────

_FALLBACK_PROMPTS = [
    # (关键词列表, prompt)
    (["股指", "期指", "IF", "沪深300", "期货"],
     "夜晚城市金融中心航拍，高楼大厦灯光闪烁，股市数据流在玻璃幕墙上滚动，橙色光晕，专业感，动感镜头缓慢下沉"),
    (["etf", "基金", "指数"],
     "现代交易所大厅内，多个巨型LED屏幕显示K线图和数字，蓝色数据光束交错，科技感强，镜头缓慢推进"),
    (["债券", "利率", "国债"],
     "宏伟的金融大楼外观，城市金融街鸟瞰，蓝色调，数字光流在建筑间穿梭，稳健专业氛围"),
    (["外汇", "汇率", "美元", "人民币"],
     "全球金融网络可视化，地球上方漂浮的货币符号和数据流，金色光点在全球各大城市间连线，宏观视角"),
    (["技术", "科技", "半导体", "芯片"],
     "科技感十足的数字空间，蓝紫色粒子流汇聚成芯片电路图案，微观到宏观的镜头切换，未来感"),
    (["能源", "石油", "煤炭", "电力"],
     "工业能源基地鸟瞰，风力发电机阵列和太阳能板在橙色日落中旋转，能量光线流动，大气磅礴"),
    (["消费", "零售", "电商"],
     "繁华都市商业区夜景，霓虹灯光闪烁，人流穿梭，消费数据以光流形式在空中流动，现代活力"),
    (["房地产", "地产", "建筑"],
     "现代都市建筑群延时摄影，玻璃幕墙反射云层变化，建筑拔地而起的动感，专业商务气息"),
    (["宏观", "GDP", "政策", "经济"],
     "俯瞰中国经济地图，数据光柱从各大城市拔起，连线交织成网络，金色光晕，国家宏观视角"),
]

_DEFAULT_PROMPT = (
    "现代金融数据中心，蓝色调，大量数字和图表在黑色背景上流动，"
    "镜头缓慢推进，科技感强，专业金融氛围，无人脸，无文字"
)

# ── LLM Prompt 系统词 ──────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """你是专业的金融视频 prompt 工程师。
根据用户提供的金融报告页面内容，生成 1 句适合文生视频模型（可灵）的中文场景描述。

要求：
1. 30~50 字，描述一个动态视觉场景
2. 内容贴合金融/数据/城市主题（股市大屏、数据可视化、城市金融中心、抽象数字流等）
3. 不包含任何人脸、具体文字、品牌标志
4. 动感、专业、视觉冲击力强
5. 只输出 prompt 内容本身，不加任何解释、标点前缀"""


def build_video_prompt(script_text: str, page_title: str = "") -> str:
    """
    根据页面讲稿生成可灵文生视频 prompt。
    优先用 LLM，失败时用关键词匹配模板兜底。
    """
    combined = f"{page_title} {script_text}"

    # 先尝试 LLM
    if _openai_available and LLM_API_KEY:
        try:
            prompt = _build_with_llm(combined)
            if prompt and 10 <= len(prompt) <= 150:
                print(f"    [Prompt] LLM生成: {prompt[:60]}...")
                return prompt
        except Exception as e:
            print(f"    [Prompt] LLM失败，使用模板兜底: {e}")

    # 关键词匹配兜底
    prompt = _build_with_template(combined)
    print(f"    [Prompt] 模板匹配: {prompt[:60]}...")
    return prompt


def _build_with_llm(content: str) -> str:
    """调用 LLM 生成 prompt"""
    client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)
    # 截取前 300 字作为输入，避免 token 浪费
    user_input = content[:300].strip()
    resp = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": f"页面内容：{user_input}"},
        ],
        max_tokens=100,
        temperature=0.7,
    )
    return resp.choices[0].message.content.strip()


def _build_with_template(content: str) -> str:
    """按关键词匹配预设场景模板"""
    content_lower = content.lower()
    for keywords, prompt in _FALLBACK_PROMPTS:
        for kw in keywords:
            if kw.lower() in content_lower:
                return prompt
    return _DEFAULT_PROMPT
