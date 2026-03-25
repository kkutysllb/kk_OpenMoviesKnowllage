"""
Markdown 解析模块
- 解析 Markdown 文件，按标题分章节
- 提取表格数据并转换为 DataFrame
- 将表格渲染为图片（暗黑主题）
"""
import os
import re
from dataclasses import dataclass, field
from typing import List, Optional, Dict
import tempfile

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
class MarkdownSection:
    """Markdown 章节数据结构"""
    title: str = ""                    # 章节标题
    content: str = ""                  # 文本内容（不含表格）
    tables: List[pd.DataFrame] = field(default_factory=list)  # 表格数据
    table_images: List[str] = field(default_factory=list)     # 表格图片路径
    order: int = 0                     # 顺序
    level: int = 2                     # 标题层级（默认##）


def parse_markdown(md_path: str) -> List[MarkdownSection]:
    """
    解析 Markdown 文件，按 ## 标题分章节
    
    Args:
        md_path: Markdown 文件路径
        
    Returns:
        List[MarkdownSection] 章节列表
    """
    if not os.path.exists(md_path):
        raise FileNotFoundError(f"Markdown 文件不存在: {md_path}")
    
    with open(md_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 按 ## 标题分割（保留标题）
    # 匹配 ## 标题行
    pattern = r'(^|\n)##\s+(.+?)(?=\n##\s+|\Z)'
    matches = list(re.finditer(pattern, content, re.DOTALL))
    
    sections = []
    
    # 处理每个章节
    for i, match in enumerate(matches, 1):
        section_text = match.group(0).strip()
        
        # 提取标题（第一行）
        lines = section_text.split('\n')
        title_line = lines[0]
        title = re.sub(r'^##\s*', '', title_line).strip()
        
        # 剩余内容
        body = '\n'.join(lines[1:]).strip()
        
        # 提取表格
        tables = _extract_tables(body)
        
        # 清理表格后的正文内容
        content_clean = _remove_tables(body)
        
        section = MarkdownSection(
            title=title,
            content=content_clean,
            tables=tables,
            order=i,
            level=2
        )
        sections.append(section)
    
    # 如果没有 ## 标题，将整个文件作为一个章节
    if not sections:
        tables = _extract_tables(content)
        content_clean = _remove_tables(content)
        sections.append(MarkdownSection(
            title="内容概述",
            content=content_clean,
            tables=tables,
            order=1,
            level=1
        ))
    
    return sections


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
    
    # 匹配表格块
    # 查找所有表格行
    table_pattern = r'(\|[^\n]+\|\n\|[-:|\s]+\|\n(?:\|[^\n]+\|\n?)+)'
    matches = re.finditer(table_pattern, text)
    
    for match in matches:
        table_text = match.group(1)
        try:
            # 解析表格
            lines = [line.strip() for line in table_text.strip().split('\n') if line.strip()]
            if len(lines) < 2:
                continue
            
            # 第一行是表头
            headers = [cell.strip() for cell in lines[0].split('|')[1:-1]]
            
            # 跳过第二行（分隔线）
            # 第三行开始是数据
            data = []
            for line in lines[2:]:
                cells = [cell.strip() for cell in line.split('|')[1:-1]]
                if cells:
                    data.append(cells)
            
            if data:
                df = pd.DataFrame(data, columns=headers)
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
                          style: str = "dark", width: int = 800, height: int = None) -> str:
    """
    将 DataFrame 渲染为图片
    
    Args:
        df: 表格数据
        output_path: 输出图片路径
        style: 样式风格 (dark/light)
        width: 图片宽度
        height: 图片高度（None则自动计算）
        
    Returns:
        输出图片路径
    """
    if not _matplotlib_available or not _pandas_available:
        raise ImportError("需要安装 matplotlib 和 pandas")
    
    # 设置中文字体
    _setup_chinese_font()
    
    # 计算高度
    row_height = 40
    header_height = 50
    padding = 40
    if height is None:
        height = min(600, header_height + len(df) * row_height + padding * 2)
    
    # 创建图形
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
    
    # 准备数据
    cell_text = df.values.tolist()
    col_labels = df.columns.tolist()
    
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
    table.set_fontsize(11)
    table.scale(1, 2)
    
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
    """设置中文字体"""
    font_paths = [
        '/System/Library/Fonts/PingFang.ttc',
        '/System/Library/Fonts/STHeiti Medium.ttc',
        '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
        '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
    ]
    
    for font_path in font_paths:
        if os.path.exists(font_path):
            try:
                font_manager.fontManager.addfont(font_path)
                plt.rcParams['font.family'] = font_manager.FontProperties(fname=font_path).get_name()
                plt.rcParams['axes.unicode_minus'] = False
                return
            except Exception:
                continue
    
    # 回退到默认
    plt.rcParams['font.sans-serif'] = ['DejaVu Sans']


def render_section_tables(section: MarkdownSection, temp_dir: str) -> List[str]:
    """
    渲染章节中所有表格为图片
    
    Args:
        section: MarkdownSection
        temp_dir: 临时目录
        
    Returns:
        图片路径列表
    """
    image_paths = []
    
    for i, df in enumerate(section.tables):
        output_path = os.path.join(temp_dir, f"table_{section.order}_{i}.png")
        try:
            render_table_to_image(df, output_path)
            image_paths.append(output_path)
        except Exception as e:
            print(f"    [警告] 表格渲染失败: {e}")
    
    return image_paths


# 兼容 PDF 解析的数据结构
def convert_to_page_data(sections: List[MarkdownSection], temp_dir: str) -> List:
    """
    将 MarkdownSection 转换为兼容 PageData 的结构
    
    Args:
        sections: Markdown 章节列表
        temp_dir: 临时目录（用于保存表格图片）
        
    Returns:
        兼容 PageData 的列表
    """
    from pipeline.pdf_parser import PageData
    
    pages_data = []
    for section in sections:
        # 渲染表格为图片
        table_images = render_section_tables(section, temp_dir)
        
        page = PageData(
            page_num=section.order,
            text=section.content,
            screenshot_path="",  # Markdown 没有截图
            image_paths=table_images,  # 表格图片作为 image_paths
            title=section.title,
            key_points=[]
        )
        # 动态添加原始表格数据（用于LLM生成讲稿）
        page.tables = section.tables
        pages_data.append(page)
    
    return pages_data
