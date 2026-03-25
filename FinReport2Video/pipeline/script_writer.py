"""
LLM 讲稿润色模块
- 调用 DeepSeek API 将原文改写为口语化播报稿
- 保留所有数字、百分比、指数名称等关键数据
- 对数字做 TTS 友好预处理
- 本地文件缓存：相同 PDF + 页码的讲稿不重复调用 LLM
"""
import os
import re
import hashlib
from openai import OpenAI
from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, SCRIPT_MAX_CHARS, TEMP_DIR

_client = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)
    return _client


SYSTEM_PROMPT = """你是一位专业的金融分析师播报员，负责将金融报告内容转换为适合视频播报的讲稿。

核心要求：
1. 保留所有关键数据：指数点位、涨跌幅、百分比、市值、ETF名称、股票代码等
2. 用自然流畅的口语化语言，避免机械断句
3. 语气专业、客观，避免主观预测
4. 讲稿长度控制在150-250字之间（对应约30-50秒音频）
5. 输出格式要求（最重要）：
   - 讲稿必须是一段连贯流畅的文字，不要用换行符分段
   - 根据语义自然停顿，不要机械地按字数断句
   - 避免在数据、单位、专有名词中间断句
   - **绝对禁止**使用 Markdown 格式：不要加粗(**)、不要标题(#)、不要列表(-)、不要分割线(---)
   - **只输出纯文本**，不要任何格式符号
   - 直接输出讲稿文字，不需要任何说明或前缀
6. 如果原文内容不足150字，适当补充背景知识，但不构造数据

示例（正确 vs 错误）：
❌ 错误：# 沪深300指数播报讲稿

---

**今日沪深300指数表现强劲**
✅ 正确：今日沪深300指数表现强劲，收盘报3500点，较前一交易日上涨1.5%。
"""


def _extract_final_output(text: str) -> str:
    """
    提取 LLM 输出中的最终结果，过滤掉思考过程。
    处理格式如：<think>...</think> 或 <thinking>...</thinking>
    """
    # 移除 <think> 标签内的内容
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    # 移除 <thinking> 标签内的内容
    text = re.sub(r'<thinking>.*?</thinking>', '', text, flags=re.DOTALL)
    # 如果有多行，取最后一行非空内容（思考过程通常在前面）
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    if lines:
        # 检查是否包含明显的思考关键词
        skip_keywords = ['思考', '分析', '推理', '让我', '首先', '其次', '第一', '第二']
        # 从后往前找第一个不像思考的行
        for line in reversed(lines):
            if not any(kw in line for kw in skip_keywords) and len(line) > 10:
                return line
        # 如果都像思考，返回最后一行
        return lines[-1]
    return text


def _clean_markdown(text: str) -> str:
    """
    清理 Markdown 格式符号，输出纯文本
    """
    import re
    
    # 移除代码块 (```code```)
    text = re.sub(r'```[\s\S]*?```', '', text)
    # 移除行内代码 (`code`)
    text = re.sub(r'`([^`]+)`', r'\1', text)
    # 移除标题符号 (# ## ###)
    text = re.sub(r'^#+\s*', '', text, flags=re.MULTILINE)
    # 移除加粗 (**text** 或 __text__)
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
    text = re.sub(r'__(.*?)__', r'\1', text)
    # 移除斜体 (*text* 或 _text_)
    text = re.sub(r'\*(.*?)\*', r'\1', text)
    text = re.sub(r'_(.*?)_', r'\1', text)
    # 移除链接 [text](url)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    # 移除图片 ![alt](url)
    text = re.sub(r'!\[([^\]]*)\]\([^)]+\)', '', text)
    # 移除 HTML 标签
    text = re.sub(r'<[^>]+>', '', text)
    # 移除分割线 (--- 或 *** 或 ___)
    text = re.sub(r'^[-*_]{3,}\s*$', '', text, flags=re.MULTILINE)
    # 移除列表符号 (- * + )
    text = re.sub(r'^[\s]*[-\*•+]\s+', '', text, flags=re.MULTILINE)
    # 移除有序列表 (1. 2. )
    text = re.sub(r'^[\s]*\d+\.\s+', '', text, flags=re.MULTILINE)
    # 移除引用符号 (>)
    text = re.sub(r'^[\s]*>\s*', '', text, flags=re.MULTILINE)
    # 移除多余空行
    text = re.sub(r'\n{2,}', '\n', text)
    # 移除行首行尾空白
    text = text.strip()
    return text


def _script_cache_path(pdf_name: str, page_num: int) -> str:
    """LLM 讲稿缓存文件路径"""
    cache_dir = os.path.join(TEMP_DIR, pdf_name, "scripts")
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, f"page_{page_num:03d}.txt")


def write_script(page_text: str, page_title: str = "", skip_llm: bool = False,
                 pdf_name: str = "", page_num: int = 0, tables: list = None) -> str:
    """
    将页面文字和表格数据生成播报讲稿

    Args:
        page_text: PDF/Markdown 提取的原始文字
        page_title: 页面标题（用于提示 LLM 上下文）
        skip_llm: 为 True 时直接使用原文（快速模式）
        pdf_name:  PDF/Markdown 文件名（用于缓存目录）
        page_num:  页码（用于缓存文件名）
        tables: 表格数据列表（DataFrame），用于生成讲解

    Returns:
        处理后的讲稿文字
    """
    has_content = page_text.strip() or tables
    if not has_content:
        return "本页暂无内容。"

    # 构建完整内容（文字 + 表格）
    content_parts = []
    if page_text.strip():
        content_parts.append(f"正文内容：\n{page_text[:1500]}")
    if tables:
        content_parts.append("\n表格数据：")
        for i, df in enumerate(tables[:3]):  # 最多3个表格
            content_parts.append(f"\n表格{i+1}（{df.shape[0]}行x{df.shape[1]}列）:\n{df.to_string(index=False)}")
    
    full_content = "\n\n".join(content_parts)

    # 快速模式：直接使用原文，只做数字处理
    if skip_llm:
        script = full_content[:SCRIPT_MAX_CHARS]
        return normalize_numbers_for_tts(script)

    # LLM 润色模式：先查本地缓存
    if pdf_name and page_num > 0:
        cache_path = _script_cache_path(pdf_name, page_num)
        if os.path.exists(cache_path):
            cached = open(cache_path, encoding="utf-8").read().strip()
            if cached:
                print(f"    讲稿已命中缓存，跳过 LLM: page_{page_num:03d}.txt")
                return cached
    else:
        cache_path = None

    user_content = f"页面主题：{page_title}\n\n{full_content}"

    try:
        client = _get_client()
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=1,
            max_tokens=600,
        )
        # 处理 GLM 等模型的 reasoning_content 情况
        message = response.choices[0].message
        if message.content:
            script = message.content.strip()
        elif hasattr(message, 'reasoning_content') and message.reasoning_content:
            script = message.reasoning_content.strip()
        else:
            script = ""
        
        # 过滤掉可能的思考过程（如果有）
        script = _extract_final_output(script)
        script = _clean_markdown(script)
    except Exception as e:
        print(f"    [警告] LLM 调用失败，使用原文: {e}")
        script = page_text[:SCRIPT_MAX_CHARS]

    script = normalize_numbers_for_tts(script)

    # 写入缓存（下次直接读取，跳过 LLM）
    if cache_path:
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                f.write(script)
        except Exception:
            pass

    return script


def extract_key_points(page_text: str, page_title: str = "", tables: list = None) -> list:
    """
    使用 LLM 提取页面的关键要点（用于左侧信息卡展示）
    
    Args:
        page_text: 页面文字内容
        page_title: 页面标题
        tables: 表格数据列表（DataFrame 的字符串表示）
        
    Returns:
        关键要点列表（3-5条）
    """
    if not page_text.strip() and not tables:
        return []
    
    # 构建提示内容
    content_parts = []
    if page_title:
        content_parts.append(f"章节标题：{page_title}")
    if page_text.strip():
        content_parts.append(f"正文内容：\n{page_text[:1500]}")
    if tables:
        content_parts.append("表格数据：")
        for i, table in enumerate(tables[:3]):  # 最多3个表格
            content_parts.append(f"\n表格{i+1}:\n{table}")
    
    user_content = "\n\n".join(content_parts)
    
    system_prompt = """你是一位专业的金融分析师，负责从报告内容中提取关键要点。

要求：
1. 提取3-5条最关键的数据或结论
2. 每条要点简洁明了，不超过20字
3. 优先包含：涨跌幅、关键指数、重要数据、核心结论
4. 只输出要点列表，不要其他说明
5. 格式：每行一条，前面加"•"符号

示例输出：
• 上证指数下跌3.63%，失守3900点
• 主力资金净流出794.8亿
• 上涨家数仅5.6%，跌停131家
• 股指期货全面贴水，空头主导"""

    try:
        client = _get_client()
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=1,
            max_tokens=300,
        )
        # 处理 GLM 等模型的 reasoning_content 情况
        message = response.choices[0].message
        if message.content:
            result = message.content.strip()
        elif hasattr(message, 'reasoning_content') and message.reasoning_content:
            result = message.reasoning_content.strip()
        else:
            result = ""
        
        result = _extract_final_output(result)
        # 清理 Markdown 格式
        result = _clean_markdown(result)
        
        # 解析要点
        points = []
        for line in result.split('\n'):
            line = line.strip()
            # 清理后可能还有残留的列表符号
            if line.startswith('•') or line.startswith('-') or line.startswith('*'):
                point = line[1:].strip()
            else:
                point = line
            
            if point and len(point) > 5 and len(points) < 5:
                points.append(point)
        
        return points  # 最多5条（由循环控制）
    except Exception as e:
        print(f"    [警告] 提取关键要点失败: {e}")
        return []


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
