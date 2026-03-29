"""
Markdown 解析模块
- 解析 Markdown 文件，按标题分章节
- 提取文档主标题、封面图、日期等元数据
- 提取表格数据并转换为 DataFrame
- 提取章节内的图表图片链接并复制到临时目录
- 将表格渲染为图片（暗黑主题）
"""
import os
import re
import shutil
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple
import tempfile

# ── 图片基础路径配置 ───────────────────────────────────────────────────────────
# 图片存储根目录（kkStockClaw 项目）
IMAGE_BASE_DIR = "/Users/libing/kk_Claw/kkStockClaw"

# ── 数据结构（兼容旧版 PDF 解析）────────────────────────────────────────────────

@dataclass
class PageData:
    """页面数据结构"""
    page_num: int            # 页码（从1开始）
    text: str                # 提取的纯文字内容
    screenshot_path: str     # 页面截图路径
    image_paths: List[str] = field(default_factory=list)  # 页内图片路径列表
    title: str = ""          # 页面推断标题（首行文字）
    key_points: List[str] = field(default_factory=list)   # 页面关键信息点

# 报告类型中文名到目录名的映射
REPORT_TYPE_MAPPING = {
    "日度市场分析": "DailyMarketReport",
    "日度市场": "DailyMarketReport",
    "ETF分析": "ETFReport",
    "ETF": "ETFReport",
    "期货分析": "FuturesReport",
    "股指期货": "FuturesReport",
    "技术分析": "TechnicalReport",
    "机构研报": "ResearchReport",
    "宏观": "MacroReport",
    "周度市场": "WeeklyMarketReport",
    "财务报告": "FinancialReport",
    "财报分析": "FinancialReport",
    "财报": "FinancialReport",
    "全球市场": "GlobalMarketReport",
    "行业分析": "IndustryReport",
}

try:
    import pandas as pd
    _pandas_available = True
except ImportError:
    _pandas_available = False

try:
    import matplotlib
    matplotlib.use('Agg')  # 无头模式
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    _matplotlib_available = True
except ImportError:
    _matplotlib_available = False


@dataclass
class MarkdownMetadata:
    """Markdown 文档元数据"""
    title: str = ""                    # 文档主标题（# 级别）
    date: str = ""                     # 文档日期
    author: str = ""                   # 作者/分析师
    abstract: str = ""                 # 摘要/简介
    cover_image: str = ""              # 封面图片路径（相对或绝对）
    data_source: str = ""              # 数据来源


@dataclass
class MarkdownSection:
    """Markdown 章节数据结构"""
    title: str = ""                    # 章节标题
    content: str = ""                  # 文本内容（不含表格和图片）
    tables: List[pd.DataFrame] = field(default_factory=list)  # 表格数据
    table_images: List[str] = field(default_factory=list)     # 表格图片路径
    images: List[str] = field(default_factory=list)           # 章节内图表图片路径
    order: int = 0                     # 顺序
    level: int = 2                     # 标题层级（默认##）


def parse_markdown(md_path: str) -> Tuple[MarkdownMetadata, List[MarkdownSection]]:
    """
    解析 Markdown 文件，提取元数据和章节
    
    Args:
        md_path: Markdown 文件路径
        
    Returns:
        Tuple[MarkdownMetadata, List[MarkdownSection]] 文档元数据和章节列表
    """
    if not os.path.exists(md_path):
        raise FileNotFoundError(f"Markdown 文件不存在: {md_path}")
    
    with open(md_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    md_dir = os.path.dirname(os.path.abspath(md_path))
    
    # ── 提取文档元数据 ────────────────────────────────────────────────────────
    metadata = _extract_metadata(content, md_dir)

    # ── 按 ## 或 ### 标题分割章节 ────────────────────────────────────────────────────
    # 匹配 ## 和 ### 标题行，统一作为章节处理
    pattern = r'(^|\n)(#{2,3})\s+(.+?)(?=\n#{2,3}\s+|\Z)'
    matches = list(re.finditer(pattern, content, re.DOTALL))

    sections = []

    # 处理每个章节
    for match in matches:
        section_text = match.group(0).strip()

        # 提取标题（第一行）
        lines = section_text.split('\n')
        title_line = lines[0]
        heading_level = len(match.group(2))  # ## 或 ###
        title = re.sub(r'^#{2,3}\s*', '', title_line).strip()

        # 清理标题中的 emoji（与主标题一致的处理方式）
        title_clean = re.sub(r'^[\U0001F300-\U0001F9FF]\s*', '', title)

        # 跳过"报告摘要"章节（摘要内容已在 metadata.abstract 中体现，不再作为独立章节）
        if title_clean == "报告摘要":
            continue

        # 剩余内容
        body = '\n'.join(lines[1:]).strip()

        # 提取表格
        tables = _extract_tables(body)

        # 提取图表图片链接
        images = _extract_images(body, md_dir)

        # 清理表格和图片后的正文内容
        content_clean = _remove_tables_and_images(body)

        section = MarkdownSection(
            title=title_clean,
            content=content_clean,
            tables=tables,
            images=images,
            order=len(sections) + 1,
            level=heading_level
        )
        sections.append(section)
    
    # 如果没有 ## 标题，将整个文件作为一个章节
    if not sections:
        tables = _extract_tables(content)
        images = _extract_images(content, md_dir)
        content_clean = _remove_tables_and_images(content)
        sections.append(MarkdownSection(
            title="内容概述",
            content=content_clean,
            tables=tables,
            images=images,
            order=1,
            level=1
        ))
    
    return metadata, sections


def _extract_metadata(content: str, md_dir: str) -> MarkdownMetadata:
    """
    提取 Markdown 文档元数据
    
    提取内容：
    - 主标题（# 级别）
    - 封面图片（![封面](...) 或首个图片）
    - 日期（**分析日期：** 或 **交易日期：**）
    - 摘要（首个引用块 > 内容）
    - 数据来源
    """
    metadata = MarkdownMetadata()
    
    # 提取主标题（# 级别，注意不是 ##）
    title_match = re.search(r'^#\s+(.+?)$', content, re.MULTILINE)
    if title_match:
        # 移除 emoji 前缀（常见 emoji 范围）
        title = title_match.group(1).strip()
        # 移除开头的 emoji（使用字符类匹配）
        title = re.sub(r'^[\U0001F300-\U0001F9FF]\s*', '', title)
        metadata.title = title.strip()
    
    # 提取封面图片（优先找 ![封面] 或 !\[封面\]，否则取第一个图片）
    cover_match = re.search(r'!\[封面\]\(([^)]+)\)', content, re.IGNORECASE)
    if cover_match:
        cover_path = cover_match.group(1)
        metadata.cover_image = _resolve_image_path(cover_path, md_dir)
    else:
        # 取第一个图片作为封面
        first_img_match = re.search(r'!\[.*?\]\(([^)]+)\)', content)
        if first_img_match:
            metadata.cover_image = _resolve_image_path(first_img_match.group(1), md_dir)
    
    # 提取日期
    date_match = re.search(r'\*\*分析日期[：:]\s*([^\*]+)\*\*', content)
    if not date_match:
        date_match = re.search(r'\*\*交易日期[：:]\s*([^\*]+)\*\*', content)
    if not date_match:
        # 尝试匹配 YYYY-MM-DD 格式
        date_match = re.search(r'(\d{4}-\d{2}-\d{2})', content)
    if date_match:
        metadata.date = date_match.group(1).strip()
    
    # 提取摘要（首个引用块）
    quote_match = re.search(r'>\s*(.+?)(?=\n\n|\n##|\n[^>]|\Z)', content, re.DOTALL)
    if quote_match:
        abstract = quote_match.group(1).strip()
        # 清理引用块内的 markdown 符号
        abstract = re.sub(r'\n>\s*', ' ', abstract)
        abstract = re.sub(r'\s+', ' ', abstract)
        # 限制长度
        if len(abstract) > 200:
            abstract = abstract[:200] + '...'
        metadata.abstract = abstract
    
    # 提取数据来源
    source_match = re.search(r'\*\*数据来源[：:]\s*([^\*]+)\*\*', content)
    if source_match:
        metadata.data_source = source_match.group(1).strip()
    
    # 提取作者
    author_match = re.search(r'\*\*报告生成[：:]\s*([^\*]+)\*\*', content)
    if author_match:
        metadata.author = author_match.group(1).strip()
    
    return metadata


def _extract_images(text: str, md_dir: str) -> List[str]:
    """
    从 Markdown 文本中提取图片链接
    
    Args:
        text: Markdown 文本
        md_dir: Markdown 文件所在目录（暂未使用，保留兼容）
        
    Returns:
        图片路径列表（原始路径，后续复制时再解析）
    """
    images = []
    
    # 匹配 ![alt](path) 格式，排除封面图
    pattern = r'!\[(?!封面)[^\]]*\]\(([^)]+)\)'
    matches = re.finditer(pattern, text, re.IGNORECASE)
    
    for match in matches:
        img_path = match.group(1)
        # 不再检查文件是否存在，直接添加路径
        # 实际路径在 convert_to_page_data 中通过 image_source_dir 解析
        images.append(img_path)
    
    return images


def _resolve_image_path(img_path: str, md_dir: str) -> str:
    """
    解析图片路径为绝对路径
    
    Args:
        img_path: 图片路径（可能是相对路径或绝对路径）
        md_dir: Markdown 文件所在目录
        
    Returns:
        图片绝对路径
    """
    # 如果已经是绝对路径
    if os.path.isabs(img_path):
        return img_path
    
    # 相对于 Markdown 文件目录
    abs_path = os.path.join(md_dir, img_path)
    return os.path.normpath(abs_path)


def _remove_tables_and_images(text: str) -> str:
    """移除文本中的表格和图片链接，保留其他内容"""
    # 移除图片链接
    text = re.sub(r'!\[[^\]]*\]\([^)]+\)', '', text)
    # 移除表格块
    table_pattern = r'\n?\|[^\n]+\|\n\|[-:|\s]+\|\n(?:\|[^\n]+\|\n?)+'
    text = re.sub(table_pattern, '\n', text)
    # 清理多余空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _clean_table_cell(text: str) -> str:
    """
    清理表格单元格内容：移除 Markdown 格式和 Emoji
    """
    if not text:
        return ""

    # 移除 Markdown bold (**text** 或 __text__)
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
    text = re.sub(r'__(.*?)__', r'\1', text)

    # 移除 Markdown italic (*text* 或 _text_)
    text = re.sub(r'\*(.*?)\*', r'\1', text)
    text = re.sub(r'_(.*?)_', r'\1', text)

    # 移除 Markdown 删除线 (~~text~~)
    text = re.sub(r'~~(.*?)~~', r'\1', text)

    # 移除行内代码 (`code`)
    text = re.sub(r'`([^`]+)`', r'\1', text)

    # 移除 Emoji（Unicode 范围，不包含 CJK 字符）
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"
        "\U0001F300-\U0001F5FF"
        "\U0001F680-\U0001F6FF"
        "\U0001F1E0-\U0001F1FF"
        "\U0001F900-\U0001F9FF"
        "\U0001FA00-\U0001FA6F"
        "\U0001FA70-\U0001FAFF"
        "\U00002600-\U000026FF"
        "\U00002700-\U000027BF"
        "]+"
    )
    text = emoji_pattern.sub('', text)

    return text.strip()


def _extract_tables(text: str) -> List[pd.DataFrame]:
    """
    从 Markdown 文本中提取表格

    支持格式:
    | 列1 | 列2 | 列3 |
    |-----|-----|-----|
    | a   | b   | c   |
    """
    if not _pandas_available:
        return []

    tables = []

    # 匹配表格块：找到所有 |...| 行组成的多行块，前面有分隔行 |---|---|
    table_pattern = r'(\|[^\n]+\|(?:\n\|[-:|\s|]+\|)+(?:\n\|[^\n]+\|)+)'
    matches = re.finditer(table_pattern, text)

    for match in matches:
        table_text = match.group(1)
        try:
            # 按行分割
            lines = table_text.strip().split('\n')
            if len(lines) < 2:
                continue

            # 逐行解析，处理单元格内的转义管道符 \|
            def split_row(line: str) -> List[str]:
                """按 | 分割表格行，正确处理转义的 |"""
                cells = []
                current = ""
                i = 0
                while i < len(line):
                    if line[i] == '\\' and i + 1 < len(line) and line[i + 1] == '|':
                        # 转义管道：保留为普通字符
                        current += '|'
                        i += 2
                    elif line[i] == '|':
                        # 管道分隔符
                        cells.append(current.strip())
                        current = ""
                        i += 1
                    else:
                        current += line[i]
                        i += 1
                # 最后一个单元格
                if current.strip() or len(cells) > 0:
                    cells.append(current.strip())
                # 去掉首尾空单元格（首尾 | 产生的空串）
                if cells and cells[0] == '':
                    cells = cells[1:]
                if cells and cells[-1] == '':
                    cells = cells[:-1]
                return cells

            headers = split_row(lines[0])
            # 清理表头中的 Markdown
            headers = [_clean_table_cell(h) for h in headers]

            # 跳过分隔行（第二行通常是 |---|---|
            data_start = 1
            for i, line in enumerate(lines[1:], 1):
                if re.match(r'^\|[\s\-:|]+\|$', line):
                    continue  # 跳过分隔行
                data_start = i
                break

            # 解析数据行
            data = []
            for line in lines[data_start:]:
                cells = split_row(line)
                # 清理每个单元格中的 Markdown 和 Emoji
                cells = [_clean_table_cell(c) for c in cells]
                # 跳过全空行
                if any(c for c in cells):
                    data.append(cells)

            if headers and data:
                # 对齐列数：取最大列数
                max_cols = max(len(headers), max(len(row) for row in data) if data else 0)
                headers = headers + [''] * (max_cols - len(headers))
                df = pd.DataFrame(data, columns=headers[:max_cols])
                tables.append(df)
        except Exception:
            continue

    return tables


def _remove_tables(text: str) -> str:
    """移除文本中的表格，保留其他内容"""
    # 移除表格块
    table_pattern = r'\n?\|[^\n]+\|\n\|[-:|\s]+\|\n(?:\|[^\n]+\|\n?)+'
    text = re.sub(table_pattern, '\n', text)
    # 清理多余空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def render_table_to_image(df: pd.DataFrame, output_path: str,
                          style: str = "dark", width: int = 900, height: int = None) -> str:
    """
    将 DataFrame 渲染为图片

    Args:
        df: 表格数据
        output_path: 输出图片路径
        style: 样式风格 (dark/light)
        width: 图片宽度（默认900，适配右侧表格区）
        height: 图片高度（None则自动计算）

    Returns:
        输出图片路径
    """
    if not _matplotlib_available or not _pandas_available:
        raise ImportError("需要安装 matplotlib 和 pandas")

    # 设置中文字体
    _setup_chinese_font()

    # 计算高度
    row_height = 42
    header_height = 52
    padding = 40
    if height is None:
        height = min(700, header_height + len(df) * row_height + padding * 2)

    # 创建图形（增大宽度以适应多列）
    fig, ax = plt.subplots(figsize=(width/100, height/100), dpi=100)

    # 暗黑主题配色
    if style == "dark":
        bg_color = '#0a102c'
        header_color = '#c8500f'
        text_color = '#ffffff'
        grid_color = '#2a3050'
        row_colors = ['#12183a', '#0d1228']
    else:
        bg_color = '#ffffff'
        header_color = '#4472C4'
        text_color = '#333333'
        grid_color = '#cccccc'
        row_colors = ['#f8f9fa', '#ffffff']

    fig.patch.set_facecolor(bg_color)
    ax.set_facecolor(bg_color)

    # 隐藏坐标轴
    ax.axis('off')
    ax.axis('tight')

    # 准备数据：将所有值转为字符串
    cell_text = [[str(v) if v is not None else '' for v in row] for row in df.values.tolist()]
    col_labels = [str(c) if c is not None else '' for c in df.columns.tolist()]

    # 创建表格
    table = ax.table(
        cellText=cell_text,
        colLabels=col_labels,
        loc='center',
        cellLoc='center',
        colColours=[header_color] * len(col_labels),
    )

    # 设置样式
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1, 1.8)

    # 设置单元格样式
    for i, key in enumerate(table.get_celld().keys()):
        row, col = key
        cell = table.get_celld()[key]

        # 文字颜色
        cell.set_text_props(color=text_color, fontweight='bold' if row == 0 else 'normal')

        # 背景色
        if row == 0:
            cell.set_facecolor(header_color)
        else:
            cell.set_facecolor(row_colors[row % 2])

        # 边框
        cell.set_edgecolor(grid_color)
        cell.set_linewidth(0.5)

    plt.tight_layout(pad=0.5)
    plt.savefig(output_path, dpi=150, bbox_inches='tight',
                facecolor=bg_color, edgecolor='none')
    plt.close()
    
    return output_path


def _setup_chinese_font():
    """设置中文字体（macOS/Windows/Linux）"""
    import platform

    font_paths = []
    system = platform.system()

    if system == 'Darwin':  # macOS
        font_paths = [
            '/System/Library/Fonts/PingFang.ttc',
            '/System/Library/Fonts/STHeiti Medium.ttc',
            '/System/Library/Fonts/Helvetica.ttc',
            '/Library/Fonts/Arial Unicode.ttf',
        ]
    elif system == 'Windows':
        font_paths = [
            'C:/Windows/Fonts/msyh.ttc',   # 微软雅黑
            'C:/Windows/Fonts/simhei.ttf', # 黑体
            'C:/Windows/Fonts/simsun.ttc', # 宋体
        ]
    else:  # Linux
        font_paths = [
            '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
            '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
            '/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf',
        ]

    for font_path in font_paths:
        if os.path.exists(font_path):
            try:
                # 添加字体到 matplotlib
                font_manager.fontManager.addfont(font_path)
                prop = font_manager.FontProperties(fname=font_path)
                font_name = prop.get_name()
                plt.rcParams['font.family'] = font_name
                plt.rcParams['axes.unicode_minus'] = False
                return
            except Exception:
                continue

    # 回退到默认（可能无法显示中文）
    plt.rcParams['font.sans-serif'] = ['DejaVu Sans']


def render_section_tables(section: MarkdownSection, output_dir: str) -> List[str]:
    """
    渲染章节中所有表格为图片
    
    Args:
        section: MarkdownSection
        output_dir: 输出目录（如 temp/{md_name}/tables/）
        
    Returns:
        图片路径列表
    """
    # 确保输出目录存在
    os.makedirs(output_dir, exist_ok=True)
    
    image_paths = []
    
    for i, df in enumerate(section.tables):
        output_path = os.path.join(output_dir, f"table_{section.order:03d}_{i}.png")
        try:
            render_table_to_image(df, output_path)
            image_paths.append(output_path)
        except Exception as e:
            print(f"    [警告] 表格渲染失败: {e}")
    
    return image_paths


def _extract_report_type(md_path: str) -> Tuple[str, str]:
    """
    从 Markdown 文件名提取报告类型

    文件名格式示例:
    - 1774617335_日度市场分析_20260327_2026-03-27_2017.md
    - 比亚迪_002594_SZ_财报分析报告_2026-03-29.md
    - 期货分析_螺纹钢_20260327.md

    Returns:
        (报告类型中文名, 报告目录名)
    """
    filename = os.path.basename(md_path)
    # 移除扩展名
    name_no_ext = os.path.splitext(filename)[0]

    # 优先从文件名中搜索已知的报告类型关键词
    for cn_name, en_name in REPORT_TYPE_MAPPING.items():
        if cn_name in name_no_ext:
            return cn_name, en_name

    # 回退：从下划线分隔的部分中找匹配
    parts = name_no_ext.split('_')
    for part in parts:
        for cn_name, en_name in REPORT_TYPE_MAPPING.items():
            if cn_name in part or part in cn_name:
                return cn_name, en_name

    # 默认返回 DailyMarketReport
    return "日度市场分析", "DailyMarketReport"


def _resolve_image_source_dir(md_path: str, md_dir: str) -> str:
    """
    解析图片源目录
    
    Args:
        md_path: Markdown 文件路径（上传后的临时路径）
        md_dir: Markdown 文件所在目录
        
    Returns:
        图片源目录的绝对路径
    """
    # 从文件名提取报告类型
    _, report_dir = _extract_report_type(md_path)
    
    # 构建图片源目录
    source_images_dir = os.path.join(IMAGE_BASE_DIR, report_dir, "images")
    
    if os.path.exists(source_images_dir):
        return source_images_dir
    
    # 如果不存在，返回 md_dir/images 作为备选
    fallback = os.path.join(md_dir, "images")
    return fallback if os.path.exists(fallback) else ""


def _copy_chart_images(image_paths: List[str], output_dir: str, md_dir: str, image_source_dir: str = "") -> List[str]:
    """
    复制图表图片到临时目录
    
    Args:
        image_paths: 原始图片路径列表（Markdown 中的相对路径如 images/xxx.png）
        output_dir: 输出目录（如 temp/{md_name}/images/）
        md_dir: Markdown 文件所在目录
        image_source_dir: 图片源目录（kkStockClaw 下的 images 目录）
        
    Returns:
        复制后的图片路径列表
    """
    # 确保输出目录存在
    os.makedirs(output_dir, exist_ok=True)
    
    copied_paths = []
    
    for i, img_path in enumerate(image_paths):
        # 图片路径通常是 "images/xxx.png" 格式
        # 需要去掉 "images/" 前缀，然后在源目录中查找
        img_filename = os.path.basename(img_path)
        
        # 优先从 kkStockClaw 目录查找
        if image_source_dir:
            abs_path = os.path.join(image_source_dir, img_filename)
            if os.path.exists(abs_path):
                # 复制到输出目录
                ext = os.path.splitext(abs_path)[1] or '.png'
                output_path = os.path.join(output_dir, f"chart_{i:03d}{ext}")
                try:
                    shutil.copy2(abs_path, output_path)
                    copied_paths.append(output_path)
                    continue
                except Exception as e:
                    print(f"    [警告] 图片复制失败: {e}")
        
        # 备选：从 md_dir 下查找
        if not os.path.isabs(img_path):
            abs_path = os.path.normpath(os.path.join(md_dir, img_path))
        else:
            abs_path = img_path
        
        if os.path.exists(abs_path):
            ext = os.path.splitext(abs_path)[1] or '.png'
            output_path = os.path.join(output_dir, f"chart_{i:03d}{ext}")
            try:
                shutil.copy2(abs_path, output_path)
                copied_paths.append(output_path)
            except Exception as e:
                print(f"    [警告] 图片复制失败: {e}")
        else:
            print(f"    [警告] 图片不存在: {img_path} (源目录: {image_source_dir})")
    
    return copied_paths


def _copy_cover_image(cover_path: str, output_dir: str, md_dir: str, image_source_dir: str = "") -> str:
    """
    复制封面图片到临时目录
    
    Args:
        cover_path: 原始封面图片路径（Markdown 中的相对路径）
        output_dir: 输出目录
        md_dir: Markdown 文件所在目录
        image_source_dir: 图片源目录
        
    Returns:
        复制后的图片路径，失败返回空字符串
    """
    if not cover_path:
        return ""
    
    # 提取文件名
    cover_filename = os.path.basename(cover_path)
    
    # 确保输出目录存在
    os.makedirs(output_dir, exist_ok=True)
    
    # 优先从 kkStockClaw 目录查找
    if image_source_dir:
        abs_path = os.path.join(image_source_dir, cover_filename)
        if os.path.exists(abs_path):
            ext = os.path.splitext(abs_path)[1] or '.png'
            output_path = os.path.join(output_dir, f"cover{ext}")
            try:
                shutil.copy2(abs_path, output_path)
                return output_path
            except Exception as e:
                print(f"    [警告] 封面图片复制失败: {e}")
    
    # 备选：解析相对路径
    if not os.path.isabs(cover_path):
        abs_path = os.path.normpath(os.path.join(md_dir, cover_path))
    else:
        abs_path = cover_path
    
    if os.path.exists(abs_path):
        ext = os.path.splitext(abs_path)[1] or '.png'
        output_path = os.path.join(output_dir, f"cover{ext}")
        try:
            shutil.copy2(abs_path, output_path)
            return output_path
        except Exception as e:
            print(f"    [警告] 封面图片复制失败: {e}")
    
    print(f"    [警告] 封面图片不存在: {cover_path}")
    return ""


# 页面数据结构转换
def convert_to_page_data(
    sections: List[MarkdownSection],
    temp_dir: str,
    md_name: str,
    md_path: str = "",
    cover_image: str = "",
    md_dir: str = ""
) -> List[PageData]:
    """
    将 MarkdownSection 转换为 PageData 结构

    Args:
        sections: Markdown 章节列表
        temp_dir: 临时目录根目录（如 ./temp/）
        md_name: Markdown 文件名（用于创建子目录）
        md_path: Markdown 文件完整路径（用于提取报告类型）
        cover_image: 封面图片路径（用于首个章节左侧展示）
        md_dir: Markdown 文件所在目录（用于解析相对图片路径）
        
    Returns:
        兼容 PageData 的列表
    """

    # 创建文档专属子目录
    doc_temp_dir = os.path.join(temp_dir, md_name)
    tables_dir = os.path.join(doc_temp_dir, "tables")
    images_dir = os.path.join(doc_temp_dir, "images")
    os.makedirs(tables_dir, exist_ok=True)
    os.makedirs(images_dir, exist_ok=True)
    
    # 解析图片源目录（从 kkStockClaw 项目）
    image_source_dir = ""
    if md_path:
        image_source_dir = _resolve_image_source_dir(md_path, md_dir)
        if image_source_dir:
            print(f"    图片源目录: {image_source_dir}")
    
    # 复制封面图片到临时目录
    copied_cover = ""
    if cover_image:
        copied_cover = _copy_cover_image(cover_image, images_dir, md_dir, image_source_dir)
        if copied_cover:
            print(f"    封面图片已复制: {os.path.basename(copied_cover)}")
    
    pages_data = []
    for i, section in enumerate(sections):
        # 渲染表格为图片（保存到 tables 子目录）
        table_images = render_section_tables(section, tables_dir)
        
        # 复制章节内的图表图片到 images 子目录
        chart_images = []
        if section.images:
            # 为每个章节创建子目录，避免图片名冲突
            section_images_dir = os.path.join(images_dir, f"section_{section.order:03d}")
            chart_images = _copy_chart_images(section.images, section_images_dir, md_dir, image_source_dir)
        
        # 首个章节使用封面图片作为左侧展示
        screenshot_path = ""
        if i == 0 and copied_cover and os.path.exists(copied_cover):
            screenshot_path = copied_cover
        
        page = PageData(
            page_num=section.order,
            text=section.content,
            screenshot_path=screenshot_path,  # 首个章节使用封面图
            image_paths=chart_images,         # 图表图片（用于右侧）
            title=section.title,
            key_points=[]
        )
        # 动态添加原始表格数据（用于LLM生成讲稿）
        page.tables = section.tables
        # 添加表格图片（用于右侧展示）
        page.table_images = table_images
        pages_data.append(page)
    
    return pages_data
