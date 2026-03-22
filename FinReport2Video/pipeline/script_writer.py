"""
LLM 讲稿润色模块
- 调用 DeepSeek API 将原文改写为口语化播报稿
- 保留所有数字、百分比、指数名称等关键数据
- 对数字做 TTS 友好预处理
"""
import re
from openai import OpenAI
from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, SCRIPT_MAX_CHARS

_client = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    return _client


SYSTEM_PROMPT = """你是一位专业的金融分析师播报员，负责将金融报告内容转换为适合视频播报的讲稿。

要求：
1. 保留所有关键数据：指数点位、涨跌幅、百分比、市値、ETF名称、股票代码等
2. 用自然的口语化语言扩展解释，让听众能听懂
3. 语气专业、客观，避免主观预测
4. 讲稿长度控制在150-250字之间（对应约30-50秒音频）
5. 输出格式要求（最重要）：
   - 每一个字幕行一个自然语义单元（按口语停顿节奏分割），之间用换行符 \n 隔开
   - 每行内容不超过 28 个字，且必须是一个完整语义组合（不拖断短语）
   - 不包含标题符号、列表符号、Markdown符号
   - 不包含多余空格、特殊字符
   - 直接输出讲稿文字，不需要任何说明或前缀
6. 如果原文内容不足150字，适当补充背景知识，但不虚构数据
"""


def write_script(page_text: str, page_title: str = "", skip_llm: bool = False) -> str:
    """
    将页面文字生成播报讲稿

    Args:
        page_text: PDF 提取的原始文字
        page_title: 页面标题（用于提示 LLM 上下文）
        skip_llm: 为 True 时直接使用原文（快速模式）

    Returns:
        处理后的讲稿文字
    """
    if not page_text.strip():
        return "本页暂无文字内容。"

    # 快速模式：直接使用原文，只做数字处理
    if skip_llm:
        script = page_text[:SCRIPT_MAX_CHARS]
        return normalize_numbers_for_tts(script)

    # LLM 润色模式
    user_content = f"页面主题：{page_title}\n\n原文内容：\n{page_text[:2000]}"

    try:
        client = _get_client()
        response = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=0.7,
            max_tokens=600,
        )
        script = response.choices[0].message.content.strip()
    except Exception as e:
        print(f"    [警告] LLM 调用失败，使用原文: {e}")
        script = page_text[:SCRIPT_MAX_CHARS]

    return normalize_numbers_for_tts(script)


def normalize_numbers_for_tts(text: str) -> str:
    """
    将文本中的数字格式转换为 TTS 友好的读法

    Examples:
        3.5% → 百分之三点五
        3500点 → 三千五百点
        -2.3% → 负百分之二点三
        1.2万亿 → 一点二万亿
    """
    # 百分比：-3.5% → 负百分之三点五
    def replace_percent(m):
        sign = "负" if m.group(1) == "-" else ""
        num = m.group(2)
        return f"{sign}百分之{num}"

    text = re.sub(r"(-?)(\d+(?:\.\d+)?)%", replace_percent, text)

    # 保留万亿、亿、万等单位，不做额外处理（TTS 能正常读）

    # 清理多余空白
    text = re.sub(r"\s+", " ", text).strip()

    return text
