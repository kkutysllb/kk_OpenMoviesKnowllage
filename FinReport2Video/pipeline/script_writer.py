"""
LLM 讲稿润色模块
- 调用 MiniMax M2.7 API 将原文改写为口语化播报稿
- 支持 web_search Tool Calling，自动搜索最新行情、公司资料丰富讲稿
- 保留所有数字、百分比、指数名称等关键数据
- 对数字做 TTS 友好预处理
- 本地文件缓存：相同 PDF + 页码的讲稿不重复调用 LLM
"""
import os
import re
import json
import hashlib
import requests as _http
import pandas as pd
from openai import OpenAI
from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, SCRIPT_MAX_CHARS, TEMP_DIR

# 是否开启网络搜索增强讲稿（默认关，防止每页都搜索拖慢生成）
WEB_SEARCH_ENABLED = os.getenv("WEB_SEARCH_ENABLED", "false").lower() == "true"

_client = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)
    return _client


SYSTEM_PROMPT = """你是分析师小小k，一位专业的金融分析师播报员，负责将金融报告内容转换为适合视频播报的讲稿。

核心要求：
1. 无论原始内容多少（哪怕只有标题或图表），都必须生成不少于150字的完整讲稿
2. 保留所有关键数据：指数点位、涨跌幅、百分比、市值、ETF名称、股票代码等
3. 如果章节含有图表/表格，必须结合图表内容进行讲解，说明趋势、数据含义
4. 用自然流畅的口语化语言，避免机械断句
5. 语气专业、客观，避免主观预测
6. 讲稿长度控制在150-250字之间（对应约30-50秒音频）
7. **重要 - 表格数据处理**：
   - 当看到"表格数据"时，不要朗读原始数据行（如"2020年313.96亿元"）
   - 要用自然语言解读数据趋势，如"该指标六年增长3倍"或"呈现持续增长态势"
   - 重点描述：增长/下降趋势、增幅/降幅、极值（最高/最低）、关键拐点
8. 输出格式要求（最重要）：
   - 讲稿必须是一段连贯流畅的文字，不要用换行符分段
   - 根据语义自然停顿，不要机械地按字数断句
   - 避免在数据、单位、专有名词中间断句
   - **绝对禁止**使用 Markdown 格式：不要加粗(**)、不要标题(#)、不要列表(-)、不要分割线(---)
   - **只输出纯文本**，不要任何格式符号
   - 直接输出讲稿文字，不需要任何说明或前缀
9. 如果原文内容极少（纯图表页、目录页等），则围绕章节标题展开背景介绍，但不构造虚假数据

示例（正确 vs 错误）：
❌ 错误：# 沪深300指数播报讲稿

---
**今日沪深300指数表现强劲**
✅ 正确：今日沪深300指数表现强劲，收盘报3500点，较前一交易日上涨1.5%。
❌ 错误（表格）：2020年313.96亿元，2021年433.55亿元，2022年791.07亿元
✅ 正确（表格）：该财务指标近六年持续增长，从2020年的314亿元增至2022年的791亿元，增幅超过150%。
"""

# web_search Tool 定义（遵循 MiniMax Function Calling 格式）
_WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "根据关键词进行网络搜索，获取最新财经资讯、公司动态、行情数据，用于丰富讲稿内容。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词，如：'贵州茅台2024年报业绩' 或 '沪深300最新行情'"
                }
            },
            "required": ["query"]
        }
    }
}


def _do_web_search(query: str) -> str:
    """
    执行真实网络搜索，优先使用 Tavily，降级返回提示信息
    """
    tavily_key = os.getenv("TAVILY_API_KEY", "")
    if tavily_key:
        try:
            resp = _http.post(
                "https://api.tavily.com/search",
                json={"api_key": tavily_key, "query": query, "max_results": 5,
                      "include_answer": True, "search_depth": "basic"},
                timeout=15,
            )
            data = resp.json()
            # 优先用 answer 摘要，再拼接各条结果的 content
            parts = []
            if data.get("answer"):
                parts.append(f"摘要：{data['answer']}")
            for r in data.get("results", [])[:4]:
                parts.append(f"- {r.get('title', '')}：{r.get('content', '')[:300]}")
            result = "\n".join(parts)
            print(f"    [web_search] Tavily 搜索成功: {query[:40]}")
            return result or "未找到相关结果"
        except Exception as e:
            print(f"    [web_search] Tavily 调用失败: {e}")

    # 没有 Tavily Key 时返回提示（模型会根据自身知识生成）
    return f"（网络搜索未配置或失败，请根据已知知识回答关于 '{query}' 的内容）"


def _call_llm_with_search(client: OpenAI, messages: list, max_search_rounds: int = 2) -> str:
    """
    带 web_search Tool Calling 的 LLM 调用
    支持最多 max_search_rounds 轮搜索-回答循环

    Returns:
        模型最终生成的纯文本内容
    """
    tools = [_WEB_SEARCH_TOOL]
    current_messages = list(messages)

    for round_i in range(max_search_rounds + 1):
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=current_messages,
            tools=tools,
            tool_choice="auto",
            temperature=1,
            max_tokens=800,
        )
        msg = response.choices[0].message

        # 检查是否有 tool_calls
        tool_calls = getattr(msg, "tool_calls", None)
        if not tool_calls or round_i == max_search_rounds:
            # 无工具调用或已达最大轮次，直接返回内容
            content = msg.content or ""
            return content.strip()

        # 处理所有 tool_calls（通常只有 web_search）
        # 把模型的 tool_calls 消息加入对话历史
        current_messages.append(msg)

        for tc in tool_calls:
            fn_name = tc.function.name
            try:
                fn_args = json.loads(tc.function.arguments)
            except Exception:
                fn_args = {}

            if fn_name == "web_search":
                query = fn_args.get("query", "")
                print(f"    [web_search] 模型发起搜索: {query}")
                search_result = _do_web_search(query)
            else:
                search_result = f"未知工具: {fn_name}"

            # 把搜索结果作为 tool 消息回传
            current_messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": search_result,
            })

    # 兜底：不应走到这里
    return ""


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
    清理 Markdown 格式符号和 Emoji，输出纯文本（用于 TTS 语音合成）
    """
    import re

    # ── Emoji 移除 ─────────────────────────────────────────────────────────────
    # 匹配常见 Emoji：表情类、符号类、旗标类等（不包含 CJK 字符）
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"  # 表情符号 (Emoticons)
        "\U0001F300-\U0001F5FF"  # 杂项符号和象形图 (Misc Symbols and Pictographs)
        "\U0001F680-\U0001F6FF"  # 交通和地图符号 (Transport and Map)
        "\U0001F1E0-\U0001F1FF"  # 旗标 (Flags)
        "\U0001F900-\U0001F9FF"  # 补充表情 (Supplemental Arrows-C etc)
        "\U0001FA00-\U0001FA6F"  # 棋牌表情 (Chess Symbols etc)
        "\U0001FA70-\U0001FAFF"  # 更多补充
        "\U00002600-\U000026FF"  # 杂项符号 A (Misc Symbols A)
        "\U00002700-\U000027BF"  # 装饰符号 (Dingbats)
        "]+"
    )
    text = emoji_pattern.sub('', text)

    # ── Markdown 清理 ──────────────────────────────────────────────────────────
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
    # 移除删除线 (~~text~~)
    text = re.sub(r'~~(.*?)~~', r'\1', text)
    # 移除链接 [text](url)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    # 移除图片 ![alt](url)
    text = re.sub(r'!\[([^\]]*)\]\([^)]+\)', '', text)
    # 移除 HTML 标签
    text = re.sub(r'<[^>]+>', '', text)
    # 移除分割线 (--- 或 *** 或 ___)
    text = re.sub(r'^[-*_]{3,}\s*$', '', text, flags=re.MULTILINE)
    # 移除列表符号 (- * + )，但保留列表后的文字内容
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


def _smart_truncate(text: str, max_chars: int = 5000) -> str:
    """
    智能截断文本：在 max_chars 以内，优先在段落/句子边界处截断

    截断优先级：
    1. 如果文本长度 <= max_chars，直接返回
    2. 尝试在双换行（段落）处截断
    3. 尝试在单换行处截断
    4. 尝试在句号（。）处截断
    5. 最后才硬截断

    Args:
        text: 原始文本
        max_chars: 最大字符数

    Returns:
        截断后的文本
    """
    if len(text) <= max_chars:
        return text

    # 尝试在段落边界截断（双换行）
    paragraphs = text.split('\n\n')
    if len(paragraphs) > 1:
        truncated = ""
        for para in paragraphs:
            if len(truncated) + len(para) + 2 <= max_chars:
                truncated = truncated + para + "\n\n"
            else:
                break
        if truncated:
            return truncated.rstrip()

    # 尝试在单换行处截断
    lines = text.split('\n')
    truncated = ""
    for line in lines:
        if len(truncated) + len(line) + 1 <= max_chars:
            truncated = truncated + line + "\n"
        else:
            break
    if truncated:
        return truncated.rstrip()

    # 尝试在句号处截断
    sentences = re.split(r'(?<=[。！？；\n])', text)
    truncated = ""
    for sent in sentences:
        if len(truncated) + len(sent) <= max_chars:
            truncated = truncated + sent
        else:
            break
    if truncated:
        return truncated.rstrip()

    # 最后硬截断
    return text[:max_chars].rstrip()


def _script_cache_path(pdf_name: str, page_num: int) -> str:
    """LLM 讲稿缓存文件路径"""
    cache_dir = os.path.join(TEMP_DIR, pdf_name, "scripts")
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, f"page_{page_num:03d}.txt")


def write_script(page_text: str, page_title: str = "", skip_llm: bool = False,
                 pdf_name: str = "", page_num: int = 0, tables: list = None,
                 section_index: int = 1) -> str:
    """
    将页面文字和表格数据生成播报讲稿

    Args:
        page_text: PDF/Markdown 提取的原始文字
        page_title: 页面标题（用于提示 LLM 上下文）
        skip_llm: 为 True 时直接使用原文（快速模式）
        pdf_name:  PDF/Markdown 文件名（用于缓存目录）
        page_num:  页码（用于缓存文件名）
        tables: 表格数据列表（DataFrame），用于生成讲解
        section_index: 章节序号（从1开始，用于生成过渡语）

    Returns:
        处理后的讲稿文字
    """
    # 注意：即使文字为空，只要有标题或图片路径信息，也必须通过 LLM 强制生成讲稿
    # 不再用 "本页暂无内容" 短路——这是导致某些章节无声音的根本原因
    has_content = page_text.strip() or tables or page_title.strip()
    if not has_content:
        return f"本章节暂无可解析内容。"

    # 智能截断文本：在 5000 字以内，优先在段落/换行处截断
    truncated_text = _smart_truncate(page_text, max_chars=5000)

    # 构建完整内容（标题 + 文字 + 表格）
    content_parts = []
    if page_title.strip():
        content_parts.append(f"章节标题：{page_title}")
    if truncated_text.strip():
        content_parts.append(f"正文内容：\n{truncated_text}")
    else:
        content_parts.append("（本章节正文为空，可能是纯图表页或目录页，请根据章节标题展开讲解）")
    if tables:
        content_parts.append("\n表格数据（请结合表格内容，用自然语言解读趋势和数据含义，生成流畅的分析性讲稿，不要逐字朗读原始数据）：")
        for i, df in enumerate(tables[:3]):  # 最多3个表格
            # 生成表格描述，引导 LLM 生成润色过的分析性讲稿，而不是朗读原始字符
            table_desc = _describe_table_for_narration(df, page_title)
            content_parts.append(f"\n表格{i+1}：{table_desc}")
    
    full_content = "\n\n".join(content_parts)

    # 添加章节过渡语（非第一个章节时）
    if section_index > 1 and page_title.strip():
        # 提取标题中的实际内容（去掉章节编号如"一、"）
        import re
        clean_title = re.sub(r'^[一二三四五六七八九十\d]+[、.．\s]+', '', page_title).strip()
        transitions = [
            f"接下来，让我们进入{clean_title}。",
            f"接下来为大家介绍{clean_title}。",
            f"下面我们来看{clean_title}的内容。",
        ]
        import random
        transition = random.choice(transitions)
        full_content = f"{transition}\n\n{full_content}"

    # 快速模式：直接使用原文，只做数字处理
    if skip_llm:
        script = full_content[:SCRIPT_MAX_CHARS]
        script = _clean_markdown(script)
        script = normalize_numbers_for_tts(script)
        return script

    # LLM 润色模式：先查本地缓存
    if pdf_name and page_num > 0:
        cache_path = _script_cache_path(pdf_name, page_num)
        # 开启网络搜索时，旧缓存可能是未增强的版本，删除旧缓存强制重新生成
        if WEB_SEARCH_ENABLED and os.path.exists(cache_path):
            os.remove(cache_path)
            print(f"    [web_search模式] 删除旧缓存，重新生成: page_{page_num:03d}.txt")
        if os.path.exists(cache_path):
            cached = open(cache_path, encoding="utf-8").read().strip()
            if cached:
                print(f"    讲稿已命中缓存，跳过 LLM: page_{page_num:03d}.txt")
                cached = _clean_markdown(cached)
                return cached
    else:
        cache_path = None

    user_content = f"页面主题：{page_title}\n\n{full_content}"

    try:
        client = _get_client()
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        if WEB_SEARCH_ENABLED:
            # 开启网络搜索增强模式，允许模型主动搜索最新资讯
            print(f"    开启 web_search 增强讲稿生成...")
            script = _call_llm_with_search(client, messages, max_search_rounds=2)
        else:
            # 常规模式
            response = client.chat.completions.create(
                model=LLM_MODEL,
                messages=messages,
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
        script = page_text[:SCRIPT_MAX_CHARS] if page_text.strip() else ""

    # LLM 返回空内容时强制兜底：用标题生成最低限度的讲稿
    if not script.strip():
        print(f"    [警告] LLM 返回空内容，使用标题兜底生成讲稿")
        if page_title.strip():
            script = f"本章节主题为{page_title}。" + (page_text[:200] if page_text.strip() else "请关注相关图表内容。")
        elif page_text.strip():
            script = page_text[:SCRIPT_MAX_CHARS]
        else:
            script = "本章节内容请参阅对应图表。"

    # 统一清理 Markdown 和 Emoji（所有路径最终都会走到这里）
    # 注意：这里必须与 generate_audio 中的 _clean_tts_text 保持一致，
    # 否则显示的字幕与 TTS 时间戳不匹配，导致字符重叠/乱码
    script = _clean_markdown(script)
    # 追加 _clean_tts_text 的清洗逻辑，确保与 TTS 输入一致
    from pipeline.tts_generator import _clean_tts_text
    script = _clean_tts_text(script)
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


def _describe_table_for_narration(df, page_title: str = "") -> str:
    """
    将 DataFrame 表格转换为自然语言描述，引导 LLM 生成润色过的分析性讲稿，
    而不是直接朗读原始表格字符。
    """
    import re
    try:
        # 移除千位分隔符的辅助函数
        def parse_num(s):
            if pd.isna(s):
                return None
            s = str(s).replace(',', '').replace('，', '')
            # 提取数字部分
            m = re.search(r'[-+]?\d+\.?\d*', s)
            if m:
                return float(m.group())
            return None

        rows, cols = df.shape
        # 尝试识别主要数值列（假设第二列或最后一列是主要数值）
        value_col = min(1, cols - 1) if cols > 1 else 0
        label_col = 0

        # 提取列名作为标签
        col_names = [str(c) for c in df.columns]

        # 生成表格概述
        parts = []

        # 尝试提取数值数据进行分析
        values = []
        labels = []
        for idx, row in df.iterrows():
            if cols >= 2:
                label = str(row.iloc[label_col])
                val = parse_num(row.iloc[value_col]) if value_col < cols else None
                if val is not None:
                    values.append(val)
                    labels.append(label)

        if len(values) >= 2:
            # 计算趋势
            first_val, last_val = values[0], values[-1]
            growth = (last_val - first_val) / first_val * 100 if first_val != 0 else 0

            # 找最大值和最小值
            max_val, min_val = max(values), min(values)
            max_idx, min_idx = values.index(max_val), values.index(min_val)

            # 生成描述
            parts.append(f"该表格包含{rows}行{cols}列数据。")

            # 数据范围描述
            if first_val < last_val:
                parts.append(f"数据显示从{labels[0]}到{labels[-1]}呈增长趋势。")
                if growth > 0:
                    parts.append(f"从{_format_num(first_val)}增长到{_format_num(last_val)}，增幅约{_format_pct(growth)}。")
            elif first_val > last_val:
                parts.append(f"数据显示从{labels[0]}到{labels[-1]}呈下降趋势。")
                parts.append(f"从{_format_num(first_val)}下降到{_format_num(last_val)}，降幅约{_format_pct(abs(growth))}。")
            else:
                parts.append(f"数据基本持平，维持在{_format_num(first_val)}左右。")

            # 极值描述
            if max_idx != min_idx and max_idx != 0 and min_idx != len(values) - 1:
                parts.append(f"其中{labels[max_idx]}为最高值{_format_num(max_val)}，{labels[min_idx]}为最低值{_format_num(min_val)}。")
            elif max_idx == 0:
                parts.append(f"{labels[max_idx]}为最高值{_format_num(max_val)}。")
            elif min_idx == 0:
                parts.append(f"{labels[min_idx]}为最低值{_format_num(min_val)}。")

            # 如果有中间数据且趋势明显，描述中间趋势
            if len(values) >= 3:
                mid_growth = (values[-1] - values[0]) / (len(values) - 1)
                if abs(mid_growth) > abs(growth) * 0.3:
                    parts.append("整体呈现持续稳定增长态势。")

        else:
            # 数据点太少或无法解析，提供基本信息
            parts.append(f"该表格包含{rows}行{cols}列，关键数据包括：")
            sample_rows = min(3, rows)
            for idx in range(sample_rows):
                row_data = "、".join([f"{col_names[j]}={df.iloc[idx, j]}" for j in range(min(3, cols))])
                parts.append(f"{df.iloc[idx, 0]}：{df.iloc[idx, value_col] if value_col < cols else ''}")

        # 补充原始数据摘要（少量关键数据，供 LLM 参考而不直接朗读）
        if len(values) >= 2:
            parts.append(f"参考数据：起始值{_format_num(first_val)}，末期值{_format_num(last_val)}，最大值{_format_num(max_val)}，最小值{_format_num(min_val)}。")

        return " ".join(parts)

    except Exception as e:
        # 解析失败时返回基本信息
        return f"表格数据共{rows}行{cols}列，请结合标题「{page_title}」进行解读。"


def _format_num(n: float) -> str:
    """格式化数字用于描述"""
    if abs(n) >= 10000:
        return f"{n/10000:.1f}万"
    elif abs(n) >= 1000:
        return f"{n/1000:.1f}千"
    elif n == int(n):
        return f"{int(n)}"
    else:
        return f"{n:.2f}"


def _format_pct(p: float) -> str:
    """格式化百分比用于描述"""
    return f"{abs(p):.1f}%"


def normalize_numbers_for_tts(text: str) -> str:
    """
    将文本中的数字格式转换为 TTS 友好的读法

    Examples:
        3.5% → 百分之三点五
        3500点 → 三千五百点
        -2.3% → 负百分之二点三
        1.2万亿 → 一点二万亿
        50,867.4亿 → 50867.4亿元（移除千位分隔符）
    """
    # 移除数字中的千位分隔符（逗号），避免 TTS 读成 "50 867"
    # 使用全局替换，处理任意多个逗号如 1,000,000 → 1000000
    text = re.sub(r'(\d),', r'\1', text)

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
