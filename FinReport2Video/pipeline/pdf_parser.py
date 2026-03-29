"""
PDF 解析模块
- 逐页提取文字（保留段落结构）
- 提取页面内嵌图片（图表、K线图等）
- 生成每页高清截图（作为视频背景底图）
- 提取报告元信息（标题、摘要、分析师、日期）
- 使用 LLM 智能识别页面标题
- 表格结构重组（Typora PDF 碎片化修复）
- OCR 提取矢量图表文字
"""
import os
import re
import subprocess
import tempfile
import fitz  # PyMuPDF
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple
from config import PDF_DPI, PDF_MIN_IMAGE_SIZE, TEMP_DIR, LLM_API_KEY, LLM_BASE_URL, LLM_MODEL


@dataclass
class PageData:
    page_num: int            # 页码（从1开始）
    text: str                # 提取的纯文字内容
    screenshot_path: str     # 页面截图路径
    image_paths: List[str] = field(default_factory=list)  # 页内图片路径列表
    title: str = ""          # 页面推断标题（首行文字）
    key_points: List[str] = field(default_factory=list)   # 页面关键信息点（用于左侧展示）


@dataclass
class ReportMeta:
    """PDF 报告元信息"""
    title: str = ""          # 报告标题
    abstract: str = ""       # 摘要/前言
    analyst: str = ""        # 分析师
    date: str = ""           # 日期
    institution: str = ""    # 机构
    data_source: str = ""    # 数据源
    total_pages: int = 0     # 总页数


@dataclass
class ChapterSection:
    """章节结构"""
    title: str = ""                    # 章节标题
    content: str = ""                  # 章节内容
    start_page: int = 1                 # 起始页码
    page_indices: List[int] = field(default_factory=list)  # 包含的页面索引
    image_paths: List[str] = field(default_factory=list)    # 章节内图片路径列表


def parse_pdf(pdf_path: str, pages: Optional[str] = None) -> List[PageData]:
    """
    解析 PDF，返回每页数据列表

    Args:
        pdf_path: PDF 文件路径
        pages: 页面范围字符串，如 "1-5" 或 "2,4,6"，None 表示全部

    Returns:
        List[PageData]
    """
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF 文件不存在: {pdf_path}")

    doc = fitz.open(pdf_path)
    total_pages = len(doc)

    # 解析页面范围
    page_indices = _parse_page_range(pages, total_pages)

    # 创建临时目录
    pdf_name = os.path.splitext(os.path.basename(pdf_path))[0]
    work_dir = os.path.join(TEMP_DIR, pdf_name)
    screenshots_dir = os.path.join(work_dir, "screenshots")
    images_dir = os.path.join(work_dir, "images")
    os.makedirs(screenshots_dir, exist_ok=True)
    os.makedirs(images_dir, exist_ok=True)

    result = []

    for page_idx in page_indices:
        page = doc[page_idx]
        page_num = page_idx + 1

        print(f"  解析第 {page_num}/{total_pages} 页...")

        # 1. 提取文字
        text = _extract_text(page)

        # 2. 生成页面截图
        screenshot_path = _take_screenshot(page, page_num, screenshots_dir)

        # 3. 提取页内嵌入图片
        image_paths = _extract_images(doc, page, page_num, images_dir)

        # 4. 推断页面标题（取第一个较短的文字块）
        title = _infer_title(page)

        # 5. 提取页面关键信息点
        key_points = _extract_key_points(text)

        result.append(PageData(
            page_num=page_num,
            text=text,
            screenshot_path=screenshot_path,
            image_paths=image_paths,
            title=title,
            key_points=key_points,
        ))

    doc.close()
    return result


# CJK 部首补充区（U+2E80-U+2EFF）到标准汉字的映射
# NFKC 无法处理这类字符，需要手动映射
_CJK_RADICAL_MAP = {
    '⺀': '二', '⺁': '亠', '⺂': '人', '⺃': '儿', '⺄': '入',
    '⺅': '亻', '⺆': '冂', '⺇': '几', '⺈': '刀', '⺉': '刂',
    '⺊': '卜', '⺋': '卩', '⺌': '小', '⺍': '小', '⺎': '兀',
    '⺏': '尢', '⺐': '尸', '⺑': '屮', '⺒': '山', '⺓': '巛',
    '⺔': '川', '⺕': '工', '⺖': '忄', '⺗': '心', '⺘': '扌',
    '⺙': '攴', '⺛': '文', '⺜': '斗', '⺝': '月', '⺞': '欠',
    '⺟': '母', '⺠': '民', '⺡': '氵', '⺢': '水', '⺣': '火',
    '⺤': '爪', '⺥': '爻', '⺦': '丬', '⺧': '犬', '⺨': '犭',
    '⺩': '王', '⺪': '礻', '⺫': '目', '⺬': '示', '⺭': '礻',
    '⺮': '竹', '⺯': '米', '⺰': '纟', '⺱': '糸', '⺲': '网',
    '⺳': '网', '⺴': '网', '⺵': '网', '⺶': '羊', '⺷': '羊',
    '⺸': '羊', '⺹': '老', '⺺': '耳', '⺻': '聿', '⺼': '肉',
    '⺽': '月', '⺾': '艹', '⺿': '艹', '⻀': '艹', '⻁': '虎',
    '⻂': '衤', '⻃': '飞', '⻄': '西', '⻅': '见', '⻆': '角',
    '⻇': '贝', '⻈': '走', '⻉': '足', '⻊': '足', '⻋': '车',
    '⻌': '辶', '⻍': '辶', '⻎': '阝', '⻏': '阝', '⻐': '邑',
    '⻑': '长', '⻒': '门', '⻓': '长', '⻔': '门', '⻕': '草',
    '⻖': '阝', '⻗': '雨', '⻘': '青', '⻙': '音', '⻚': '页',
    '⻛': '风', '⻜': '飞', '⻝': '食', '⻞': '食', '⻟': '食',
    '⻠': '首', '⻡': '香',
}


def _normalize_cjk_radicals(text: str) -> str:
    """将 CJK 部首补充区字符替换为对应标准汉字"""
    return ''.join(_CJK_RADICAL_MAP.get(ch, ch) for ch in text)


def _clean_pdf_text(text: str) -> str:
    """
    清洗 PDF 提取的文字：
    - CJK 部首补充区字符 → 标准汉字
    - NFKC 规范化（康熙部首、CJK兼容字等变体汉字 → 标准汉字）
    - 删除 PUA 私用区字符（PDF 字体映射乱码）
    - 压缩多余空格
    """
    import unicodedata
    # 先处理 NFKC 无法覆盖的 CJK 部首补充区字符
    text = _normalize_cjk_radicals(text)
    # NFKC 规范化：将康熙部首、CJK兴趣区等变体字转换为标准 Unicode 字
    text = unicodedata.normalize('NFKC', text)
    # 删除 PUA 私用区字符（如果还有残存）
    text = re.sub(r'[\ue000-\uf8ff]', '', text)
    # 删除 Unicode 替换字符
    text = text.replace('\ufffd', '')
    # 删除控制字符
    text = re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '', text)
    # 删除换行、压缩多余空格
    text = text.replace('\n', ' ').replace('\r', '')
    text = re.sub(r'  +', ' ', text)
    return text.strip()


# 章节标题常见前缀模式（排除这类内容被误提取为报告标题）
_SECTION_PREFIX_RE = re.compile(
    r'^(第[一二三四五六七八九十\d]+[章节部分]|'
    r'[一二三四五六七八九十]+[、.．]|'
    r'\d{1,2}[、.．]|'
    r'[（(]\d+[）)]|'
    r'附录|摘要|目录|前言|结论|总结)',
    re.UNICODE
)


def extract_report_meta(pdf_path: str) -> ReportMeta:
    """
    提取报告元信息：
    - 标题/摘要：前 2 页（封面内容）
    - 分析师/日期/数据源：全文扫描（包括末页）
    """
    if not os.path.exists(pdf_path):
        return ReportMeta()

    doc = fitz.open(pdf_path)
    meta = ReportMeta(total_pages=len(doc))

    # ─ 前 2 页：提取标题和摘要 ─
    front_text = ""
    # page0_spans: 仅第1页的 span（封面，用于标题提取）
    # page1_spans: 第2页的 span（备用，但降权）
    page0_spans = []
    page1_spans = []
    for i in range(min(2, len(doc))):
        page = doc[i]
        front_text += page.get_text() + "\n"
        try:
            blocks = page.get_text("dict")["blocks"]
            for block in blocks:
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        txt = span["text"].strip()
                        if txt and len(txt) > 1:
                            if i == 0:
                                page0_spans.append((span["size"], txt))
                            else:
                                page1_spans.append((span["size"], txt))
        except Exception:
            pass

    # ─ 全文（包括末页）：匹配分析师/日期/数据源 ─
    all_text = front_text
    tail_start = max(2, len(doc) - 3)  # 扫描最后 3 页
    for i in range(tail_start, len(doc)):
        all_text += doc[i].get_text() + "\n"

    doc.close()

    # 标题：优先从第1页（封面）提取，排除章节标题格式
    # 策略：按字体大小降序，跳过章节前缀 / 过短 / 纯数字的文字块
    def _pick_title_spans(spans):
        """从 spans 列表中挑选适合作为报告标题的文字块"""
        spans_sorted = sorted(spans, key=lambda x: -x[0])
        picked = []
        for size, txt in spans_sorted:
            cleaned = _clean_pdf_text(txt)
            # 跳过过短文字
            if len(cleaned) < 3:
                continue
            # 跳过纯数字/日期
            if re.match(r'^[\d\s./-]+$', cleaned):
                continue
            # 跳过章节标题格式（如 "一、xxx"、"第一章 xxx"）
            if _SECTION_PREFIX_RE.match(cleaned):
                continue
            # 跳过已收录的重复内容
            if cleaned in picked:
                continue
            picked.append(cleaned)
            if len(picked) >= 2:
                break
        return picked

    title_parts = _pick_title_spans(page0_spans)
    # 第1页未能提取到足够内容时，用第2页补充（但第2页内容不单独覆盖第1页结果）
    if not title_parts and page1_spans:
        title_parts = _pick_title_spans(page1_spans)

    if title_parts:
        meta.title = _clean_pdf_text("".join(title_parts[:2]))[:60]

    # 分析师：匹配常见模式
    analyst_patterns = [
        r"分析师[\uff1a:] *([\u4e00-\u9fa5\w\s、，,]{2,20})",
        r"作者[\uff1a:] *([\u4e00-\u9fa5\w\s、，,]{2,20})",
        r"研究员[\uff1a:] *([\u4e00-\u9fa5\w\s、，,]{2,20})",
        r"撰稿[\uff1a:] *([\u4e00-\u9fa5\w\s、，,]{2,20})",
    ]
    for pat in analyst_patterns:
        m = re.search(pat, all_text)
        if m:
            meta.analyst = _clean_pdf_text(m.group(1).strip())[:30]
            break

    # 日期：匹配各种日期格式
    date_patterns = [
        r"(\d{4})年(\d{1,2})月(\d{1,2})日",
        r"(\d{4})-(\d{2})-(\d{2})",
        r"(\d{4})/(\d{2})/(\d{2})",
        r"(\d{4})\.(\d{2})\.(\d{2})",
    ]
    for pat in date_patterns:
        m = re.search(pat, all_text)
        if m:
            meta.date = m.group(0).strip()
            break

    # 数据源：匹配常见模式
    ds_patterns = [
        r"数据来源[\uff1a:] *([^\n]{3,60})",
        r"数据源[\uff1a:] *([^\n]{3,60})",
        r"资料来源[\uff1a:] *([^\n]{3,60})",
        r"Source[:\s] *([^\n]{3,60})",
    ]
    for pat in ds_patterns:
        m = re.search(pat, all_text, re.IGNORECASE)
        if m:
            meta.data_source = _clean_pdf_text(m.group(1).strip())[:60]
            break

    # 摘要：取第一页首段较长文字
    lines = [l.strip() for l in front_text.split("\n") if len(l.strip()) > 15]
    for line in lines:
        if len(line) > 20 and not re.match(r"^\d+[.、]", line):
            meta.abstract = _clean_pdf_text(line)[:80]
            break

    # 对所有字段做最终清洗
    meta.title       = _clean_pdf_text(meta.title)
    meta.analyst     = _clean_pdf_text(meta.analyst)
    meta.abstract    = _clean_pdf_text(meta.abstract)
    meta.data_source = _clean_pdf_text(meta.data_source)

    return meta


def _parse_page_range(pages: Optional[str], total: int) -> List[int]:
    """解析页面范围字符串为索引列表（0-based）"""
    if pages is None:
        return list(range(total))

    indices = set()
    for part in pages.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            s = max(0, int(start.strip()) - 1)
            e = min(total - 1, int(end.strip()) - 1)
            indices.update(range(s, e + 1))
        else:
            idx = int(part.strip()) - 1
            if 0 <= idx < total:
                indices.add(idx)

    return sorted(indices)


def _extract_text(page: fitz.Page) -> str:
    """提取页面文字，按段落整理"""
    blocks = page.get_text("blocks")
    paragraphs = []
    for block in blocks:
        if block[6] == 0:  # 文字块（非图片块）
            text = block[4].strip()
            if text and len(text) > 1:
                paragraphs.append(text)
    return "\n".join(paragraphs)


def _take_screenshot(page: fitz.Page, page_num: int, save_dir: str) -> str:
    """生成页面高清截图"""
    mat = fitz.Matrix(PDF_DPI / 72, PDF_DPI / 72)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    path = os.path.join(save_dir, f"page_{page_num:03d}.png")
    pix.save(path)
    return path


def _extract_page_images(page: fitz.Page, pdf_path: str, page_idx: int) -> List[str]:
    """
    从页面提取图片，返回图片路径列表。
    用于 parse_pdf_smart 的章节图片提取。
    """
    import uuid
    
    image_paths = []
    image_list = page.get_images(full=True)
    
    # 确定保存目录
    base_name = os.path.splitext(os.path.basename(pdf_path))[0]
    images_dir = os.path.join(TEMP_DIR, f"{base_name}_images")
    os.makedirs(images_dir, exist_ok=True)
    
    doc = page.parent  # 获取所属的 Document
    
    for img_idx, img_info in enumerate(image_list):
        xref = img_info[0]
        try:
            base_image = doc.extract_image(xref)
            img_bytes = base_image["image"]
            img_ext = base_image["ext"]
            width = base_image["width"]
            height = base_image["height"]
            
            # 过滤小图和 emoji 图标
            if width * height < PDF_MIN_IMAGE_SIZE:
                continue
            
            # 过滤 emoji 类小图标：尺寸过小或接近正方形的小图
            min_dimension = min(width, height)
            max_dimension = max(width, height)
            aspect_ratio = max_dimension / min_dimension if min_dimension > 0 else 1
            
            # 过滤条件：
            # 1. 任意边小于 50 像素的小图标
            # 2. 正方形或接近正方形（宽高比 < 1.2）且面积小于 10000 像素
            if min_dimension < 50:
                continue
            if aspect_ratio < 1.2 and width * height < 10000:
                continue
            
            # 生成唯一文件名
            unique_id = uuid.uuid4().hex[:8]
            img_path = os.path.join(images_dir, f"chapter_p{page_idx+1:03d}_{img_idx:02d}_{unique_id}.{img_ext}")
            
            with open(img_path, "wb") as f:
                f.write(img_bytes)
            
            image_paths.append(img_path)
        except Exception as e:
            print(f"    [警告] 提取图片失败: {e}")
            continue
    
    return image_paths

def _extract_images(doc: fitz.Document, page: fitz.Page, page_num: int, save_dir: str) -> List[str]:
    """提取页面内嵌图片，过滤掉过小的装饰图"""
    image_paths = []
    image_list = page.get_images(full=True)

    for img_idx, img_info in enumerate(image_list):
        xref = img_info[0]
        try:
            base_image = doc.extract_image(xref)
            img_bytes = base_image["image"]
            img_ext = base_image["ext"]
            width = base_image["width"]
            height = base_image["height"]

            # 过滤小图和 emoji 图标（装饰性图标等）
            if width * height < PDF_MIN_IMAGE_SIZE:
                continue
            
            # 过滤 emoji 类小图标：尺寸过小或接近正方形的小图
            min_dimension = min(width, height)
            max_dimension = max(width, height)
            aspect_ratio = max_dimension / min_dimension if min_dimension > 0 else 1
            
            if min_dimension < 50:
                continue
            if aspect_ratio < 1.2 and width * height < 10000:
                continue

            img_path = os.path.join(save_dir, f"page_{page_num:03d}_img_{img_idx:02d}.{img_ext}")
            with open(img_path, "wb") as f:
                f.write(img_bytes)
            image_paths.append(img_path)

        except Exception:
            continue

    return image_paths


def _get_image_bboxes(doc: fitz.Document, page: fitz.Page) -> List[fitz.Rect]:
    """
    获取页面中嵌入图片的位置（bbox），用于区域截图。
    返回在页面坐标系中的矩形列表（已去重，按面积倒序）。
    """
    bboxes = []
    image_list = page.get_images(full=True)

    for img_info in image_list:
        xref = img_info[0]
        try:
            base_image = doc.extract_image(xref)
            width = base_image["width"]
            height = base_image["height"]
            # 过滤装饰小图和 emoji 图标
            if width * height < PDF_MIN_IMAGE_SIZE:
                continue
            
            # 过滤 emoji 类小图标
            min_dimension = min(width, height)
            max_dimension = max(width, height)
            aspect_ratio = max_dimension / min_dimension if min_dimension > 0 else 1
            if min_dimension < 50:
                continue
            if aspect_ratio < 1.2 and width * height < 10000:
                continue
            # 获取图片在页面上的位置（可能有多处引用）
            rects = page.get_image_rects(xref)
            for rect in rects:
                if rect.width > 50 and rect.height > 50:
                    bboxes.append(rect)
        except Exception:
            continue

    # 去重相似区域
    return _merge_rects(bboxes)


def _get_vector_chart_bboxes(page: fitz.Page, min_area: float = 3000.0) -> List[fitz.Rect]:
    """
    通过矢量绘图路径检测图表区域（折线图、K线图、柱状图等）。
    策略：收集所有绘图路径的 bbox，合并密集区域，过滤面积过小的。
    
    优化：降低 min_area 阈值从 8000 到 3000，保留更多小图表。
    """
    try:
        drawings = page.get_drawings()
    except Exception:
        return []

    if not drawings:
        return []

    page_rect = page.rect
    page_area = page_rect.width * page_rect.height

    raw_rects = []
    for d in drawings:
        try:
            r = fitz.Rect(d["rect"])
            area = r.width * r.height
            # 降低阈值：保留更多图表
            if area < min_area:
                continue
            if area > page_area * 0.85:
                continue
            # 降低最小尺寸要求
            if r.width < 20 or r.height < 15:
                continue
            raw_rects.append(r)
        except Exception:
            continue

    if not raw_rects:
        return []

    return _merge_rects(raw_rects, expand=15.0)


def _merge_rects(rects: List[fitz.Rect], expand: float = 5.0) -> List[fitz.Rect]:
    """
    合并重叠或相邻的矩形，返回合并后的列表（按面积倒序）。
    expand：先将每个矩形向外扩展若干点再做重叠判断，使相邻矩形能被合并。
    """
    if not rects:
        return []

    merged = []
    used = [False] * len(rects)

    for i, r in enumerate(rects):
        if used[i]:
            continue
        group = fitz.Rect(r)
        for j in range(i + 1, len(rects)):
            if used[j]:
                continue
            expanded = fitz.Rect(
                rects[j].x0 - expand, rects[j].y0 - expand,
                rects[j].x1 + expand, rects[j].y1 + expand,
            )
            if group.intersects(expanded):
                group = group | rects[j]  # 合并为包围框
                used[j] = True
        merged.append(group)

    # 按面积倒序，最大的图表排在前面
    merged.sort(key=lambda r: r.width * r.height, reverse=True)
    return merged


def _capture_chart_regions(
    doc: fitz.Document,
    page: fitz.Page,
    page_num: int,
    save_dir: str,
    dpi: int = 150,
    max_charts: int = 4,
) -> List[str]:
    """
    提取页面图表/表格区域的精准截图。
    先获取内嵌图片位置，再补充矢量图区域，合并后对每个区域截图。

    Returns:
        截图文件路径列表（可能为空，如纯文字页）
    """
    charts_dir = os.path.join(save_dir, "charts")
    os.makedirs(charts_dir, exist_ok=True)

    page_rect = page.rect
    page_w = page_rect.width
    page_h = page_rect.height

    # 1. 收集内嵌图片 bbox（高优先级）
    img_bboxes = _get_image_bboxes(doc, page)

    # 2. 矢量图表区域（仅在图片数量不足时补充）
    vec_bboxes: List[fitz.Rect] = []
    if len(img_bboxes) < 2:
        vec_bboxes = _get_vector_chart_bboxes(page)

    # 3. 合并两类 bbox，去掉被图片区域覆盖的矢量区域
    all_bboxes: List[fitz.Rect] = list(img_bboxes)
    for vr in vec_bboxes:
        covered = any(
            (ir & vr).get_area() > vr.get_area() * 0.5
            for ir in img_bboxes
        )
        if not covered:
            all_bboxes.append(vr)

    # 取面积最大的 max_charts 个区域
    all_bboxes.sort(key=lambda r: r.width * r.height, reverse=True)
    all_bboxes = all_bboxes[:max_charts]

    if not all_bboxes:
        return []

    # 4. 对每个区域做高清截图
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    result_paths: List[str] = []

    for idx, bbox in enumerate(all_bboxes):
        # 适当扩大截图范围（保留坐标轴/标签）
        pad = 12
        clip = fitz.Rect(
            max(0, bbox.x0 - pad),
            max(0, bbox.y0 - pad),
            min(page_w, bbox.x1 + pad),
            min(page_h, bbox.y1 + pad),
        )
        # 过滤太小的区域（宽高各不足 60pt）
        if clip.width < 60 or clip.height < 60:
            continue

        try:
            pix = page.get_pixmap(matrix=mat, clip=clip, alpha=False)
            out_path = os.path.join(charts_dir, f"page_{page_num:03d}_chart_{idx:02d}.png")
            pix.save(out_path)
            result_paths.append(out_path)
        except Exception as e:
            print(f"    [警告] 图表区域截图失败 (page={page_num}, idx={idx}): {e}")
            continue

    return result_paths


def _extract_key_points(text: str, max_points: int = 6) -> List[str]:
    """
    从页面文字提取关键信息点，用于左侧卡片展示
    策略：取包含数字/百分比/分析词的短句
    """
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    points = []
    for line in lines:
        # 跳过过长或过短的行
        if len(line) < 3 or len(line) > 40:
            continue
        # 优先选含有数字/百分比/关键词的行
        has_number = bool(re.search(r"\d", line))
        has_keyword = any(kw in line for kw in [
            "评分", "涋水", "信号", "趋势", "强", "弱", "多头", "空头",
            "涋升", "涋贴", "强势", "弱势", "超赖", "超卖",
            "指数", "期货", "ETF", "涋瓶", "指标"
        ])
        if has_number or has_keyword:
            points.append(line)
        if len(points) >= max_points:
            break
    # 如果关键点不够，用前 N 行裥位
    if len(points) < 3:
        for line in lines:
            if 3 <= len(line) <= 30 and line not in points:
                points.append(line)
            if len(points) >= max_points:
                break
    return points[:max_points]


def _infer_title(page: fitz.Page) -> str:
    """推断页面标题：取字体最大的文字块"""
    try:
        blocks = page.get_text("dict")["blocks"]
        best_text = ""
        best_size = 0
        for block in blocks:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    if span["size"] > best_size and len(span["text"].strip()) > 2:
                        best_size = span["size"]
                        best_text = span["text"].strip()
        return _clean_pdf_text(best_text)[:80]
    except Exception:
        return ""


def _get_doc_max_font(doc: fitz.Document) -> float:
    """获取文档中最大字体大小（用于大标题判定）"""
    max_size = 0.0
    for i in range(min(len(doc), 20)):  # 只扫前20页
        try:
            blocks = doc[i].get_text("dict")["blocks"]
            for block in blocks:
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        if span["size"] > max_size:
                            max_size = span["size"]
        except Exception:
            pass
    return max_size or 14.0


# ── LLM 标题提取 ────────────────────────────────────────────────────────────────

_LLM_CLIENT = None


def _get_llm_client():
    """获取 LLM 客户端（延迟初始化）"""
    global _LLM_CLIENT
    if _LLM_CLIENT is None and LLM_API_KEY:
        try:
            from openai import OpenAI
            _LLM_CLIENT = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL, timeout=30)
        except Exception:
            pass
    return _LLM_CLIENT


def _is_valid_title(title: str) -> bool:
    """
    判断标题是否有效。
    排除：纯符号、纯数字、过短、分隔线等无效标题。
    """
    if not title or len(title) < 3:
        return False
    
    # 排除纯符号/分隔线（如 -、·、=、_ 等）
    if re.match(r'^[\-·=+_\s\*#·]+$', title):
        return False
    
    # 排除以符号开头且只有符号+少量文字的
    if re.match(r'^[\-·=+_\*#"\'\[\(（]+$', title[0] if title else ''):
        # 但允许 【xxx】 格式
        if not re.match(r'^【[^】]+】$', title):
            return False
    
    # 排除纯数字
    if re.match(r'^\d+\.?\s*$', title):
        return False
    
    # 排除包含大量重复符号的（如 ·······）
    if len(re.sub(r'[·\-_=\s]', '', title)) < 3:
        return False
    
    # 必须包含至少一个汉字或字母
    if not re.search(r'[\u4e00-\u9fa5a-zA-Z]', title):
        return False
    
    return True


def _extract_title_with_llm(page_text: str, page_num: int) -> str:
    """
    智能提取页面标题。
    优先使用简单规则识别常见标题格式，失败时调用 LLM。
    
    Args:
        page_text: 页面提取的原始文本
        page_num: 页码
    
    Returns:
        提取的标题，如果不是新章节开头则返回空字符串
    """
    if not page_text or not page_text.strip():
        return ""
    
    # 取前 200 字符分析
    sample_text = page_text[:200].strip()
    
    # ── 第一步：简单规则匹配常见标题格式 ─────────────────────────────────────
    # 匹配：一、xxx 或 二、xxx 等中文数字章节
    match = re.match(r'^([一二三四五六七八九十]+[、.．]\s*[^\n]{2,25})', sample_text)
    if match and _is_valid_title(match.group(1)):
        return match.group(1).strip()
    
    # 匹配：1. xxx 或 1、xxx 等数字章节
    match = re.match(r'^(\d{1,2}[、.．]\s*[^\n]{2,25})', sample_text)
    if match and _is_valid_title(match.group(1)):
        return match.group(1).strip()
    
    # 匹配：第X章/第X节
    match = re.match(r'^(第[一二三四五六七八九十\d]+[章节][^\n]{0,20})', sample_text)
    if match and _is_valid_title(match.group(1)):
        return match.group(1).strip()
    
    # 匹配：【xxx】或 [xxx] 格式的标题
    match = re.match(r'^[【\[]([^】\]]{2,30})[】\]]', sample_text)
    if match and _is_valid_title(match.group(1)):
        return match.group(1).strip()
    
    # ── 第二步：调用 LLM 判断是否是新章节 ─────────────────────────────────────
    client = _get_llm_client()
    if not client:
        return ""
    
    try:
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "判断这个页面是否是新章节开头。如果是，输出章节标题（不超过20字）。如果不是，输出NONE。只输出结果，不要解释。"
                },
                {
                    "role": "user",
                    "content": f"第{page_num}页开头内容：\n{sample_text[:150]}"
                }
            ],
            max_tokens=30,
            temperature=1,
        )
        # 处理 GLM 等模型的 reasoning_content 情况
        message = response.choices[0].message
        if message.content:
            result = message.content.strip()
        elif hasattr(message, 'reasoning_content') and message.reasoning_content:
            result = message.reasoning_content.strip()
        else:
            result = ""
        
        # 移除可能的 <think> 标签内容
        result = re.sub(r'<think>.*?</think>', '', result, flags=re.DOTALL)
        result = re.sub(r'<thinking>.*?</thinking>', '', result, flags=re.DOTALL)
        
        # 如果 LLM 返回 NONE，表示不是新章节
        if "NONE" in result.upper() or not result:
            return ""
        
        # 处理可能包含思考过程的情况：只取最后一行非空内容
        lines = [l.strip() for l in result.split('\n') if l.strip()]
        if lines:
            result = lines[-1]  # 取最后一行作为标题
        
        # 排除包含无效关键词的输出
        invalid_keywords = ['第', '页开头', '内容', '判断', '分析', '思考', '让我']
        if any(kw in result for kw in invalid_keywords) and len(result) > 25:
            return ""
        
        # 清理标题
        result = re.sub(r'^[【\[（(]+', '', result)
        result = re.sub(r'[】\]）)]+$', '', result)
        result = re.sub(r'^["\'\s]+', '', result)  # 清理开头引号
        result = re.sub(r'["\'\s]+$', '', result)  # 清理结尾引号
        
        # 最终验证
        if not _is_valid_title(result):
            return ""
        
        return result[:80] if result else ""
    except Exception as e:
        print(f"    [LLM标题提取失败] {e}")
        return ""


def _is_section_heading(page: fitz.Page, max_font: float) -> str:
    """
    判断页面第一行是否是大标题（章节标题）。
    条件：字体 >= max_font * 0.72 且文字长度 <= 40 且不是纯数字。
    返回标题文字，否则返回空字符串。
    修复：同一行的所有 span 合并为完整标题，支持中英混合和 CJK 兼容字符。
    """
    threshold = max_font * 0.72
    try:
        blocks = page.get_text("dict")["blocks"]
        for block in blocks:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                # 收集该行所有有效文字的 spans，按原始顺序
                line_spans = []
                for span in line.get("spans", []):
                    txt = span["text"].strip()
                    if not txt:
                        continue
                    # 跳过纯空白/换行
                    if not txt or txt in ['\n', '\r', ' ']:
                        continue
                    # 跳过单独的标点符号
                    if re.match(r'^[\s\-\-\_•●○■□]+$', txt):
                        continue
                    line_spans.append((span["size"], txt, span.get("flags", 0)))

                if not line_spans:
                    continue

                # 判断是否为标题：主要字体 >= 阈值
                max_span_size = max(s[0] for s in line_spans)
                if max_span_size >= threshold:
                    # 合并该行所有文字作为完整标题
                    full_title = "".join(s[1] for s in line_spans)
                    # 清理后验检：至少包含一个汉字或字母
                    cleaned = _clean_pdf_text(full_title)
                    # 检查是否包含有效字符（汉字、字母、数字）
                    has_content = bool(re.search(r'[\u4e00-\u9fa5a-zA-Z0-9]', cleaned))
                    if 2 <= len(cleaned) <= 80 and has_content:
                        return cleaned[:80]
    except Exception:
        pass
    return ""


def parse_pdf_by_sections(pdf_path: str, pages: Optional[str] = None) -> List[PageData]:
    """
    按大标题（章节）解析 PDF，同一章节内所有 PDF 页合并为一个 PageData。

    标题提取优先级：
    1. LLM 智能提取（如果 LLM_API_KEY 已配置）
    2. 字体大小判断（回退方案）

    Args:
        pdf_path: PDF 文件路径
        pages: 页面范围字符串（作用于最终章节序号），None 表示全部

    Returns:
        List[PageData]，每项对应一个章节
    """
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF 文件不存在: {pdf_path}")

    doc = fitz.open(pdf_path)
    total_pages = len(doc)

    pdf_name = os.path.splitext(os.path.basename(pdf_path))[0]
    work_dir = os.path.join(TEMP_DIR, pdf_name)
    screenshots_dir = os.path.join(work_dir, "screenshots")
    images_dir = os.path.join(work_dir, "images")
    os.makedirs(screenshots_dir, exist_ok=True)
    os.makedirs(images_dir, exist_ok=True)

    max_font = _get_doc_max_font(doc)
    use_llm = bool(LLM_API_KEY and LLM_BASE_URL and LLM_MODEL)
    print(f"  文档最大字体: {max_font:.1f}pt，标题提取: {'LLM智能' if use_llm else '字体判断'}")

    # ── 第一遍：将所有 PDF 页按大标题归组 ──────────────────────────────────────
    sections: List[dict] = []  # [{start_page, title, page_indices}]
    current_section: Optional[dict] = None

    for page_idx in range(total_pages):
        page = doc[page_idx]
        page_num = page_idx + 1
        
        # 优先使用 LLM 提取标题
        heading = ""
        if use_llm:
            page_text = _extract_text(page)
            heading = _extract_title_with_llm(page_text, page_num)
        
        # LLM 失败时回退到字体判断
        if not heading:
            heading = _is_section_heading(page, max_font)
        
        # 第一页始终需要标题
        if page_idx == 0 and not heading:
            heading = _infer_title(page) or f"第 {page_num} 页"

        if heading:
            # 新章节
            if current_section is not None:
                sections.append(current_section)
            current_section = {
                "start_page": page_num,
                "title": heading,
                "page_indices": [page_idx],
            }
        else:
            if current_section is None:
                # 文档开头没有大标题，建一个默认章节
                current_section = {
                    "start_page": page_idx + 1,
                    "title": _infer_title(page) or f"第 {page_idx + 1} 页",
                    "page_indices": [page_idx],
                }
            else:
                current_section["page_indices"].append(page_idx)

    if current_section is not None:
        sections.append(current_section)

    print(f"  共识别 {len(sections)} 个章节（原 {total_pages} 页）")

    # ── 应用页面范围过滤（按章节序号）─────────────────────────────────────────
    section_indices = _parse_page_range(pages, len(sections))
    selected_sections = [sections[i] for i in section_indices]

    # ── 第二遍：为每个章节生成 PageData ────────────────────────────────────────
    result: List[PageData] = []
    for sec_idx, sec in enumerate(selected_sections, 1):
        print(f"  处理章节 {sec_idx}/{len(selected_sections)}: [{sec['title'][:50]}] ({len(sec['page_indices'])} 页)")

        # 合并所有页的文字
        texts = []
        all_images: List[str] = []  # 图表/表格精准截图
        screenshot_path = ""

        for arr_idx, page_idx in enumerate(sec["page_indices"]):
            page = doc[page_idx]
            page_num = page_idx + 1

            # 使用增强版文字提取（表格重组 + OCR）
            extracted_text = _extract_text_enhanced(page)
            
            # 如果是矢量图表页，尝试 OCR 提取图表文字
            if _is_likely_chart_page(page):
                ocr_text = _get_text_in_vector_regions(page)
                if ocr_text:
                    extracted_text = extracted_text + "\n" + ocr_text
            
            texts.append(extracted_text)

            # 截图取章节第一页（左侧缩略图用）
            if arr_idx == 0:
                screenshot_path = _take_screenshot(page, sec["start_page"], screenshots_dir)

            # 提取图表/表格精准截图（优先截图区域，替代原始嵌入图片字节）
            chart_shots = _capture_chart_regions(doc, page, page_num, images_dir)
            if chart_shots:
                all_images.extend(chart_shots)
            else:
                # 降级：直接提取嵌入图片字节（无法检测到矢量图时的兜底）
                all_images.extend(_extract_images(doc, page, page_num, images_dir))

        merged_text = "\n".join(t for t in texts if t)
        key_points = _extract_key_points(merged_text)

        result.append(PageData(
            page_num=sec["start_page"],
            text=merged_text,
            screenshot_path=screenshot_path,
            image_paths=all_images,
            title=sec["title"],
            key_points=key_points,
        ))

    doc.close()
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 增强功能：Typora PDF 表格重组 + OCR 矢量图表文字提取
# ═══════════════════════════════════════════════════════════════════════════════


def _extract_text_enhanced(page: fitz.Page) -> str:
    """
    增强版文字提取：智能选择最佳提取方式。
    策略：
    1. 如果页面是纯文字页，使用普通提取
    2. 如果页面有表格特征，先尝试表格重组，如果效果不好则回退
    3. 表格重组应该保留更多原始文字，而不是过度合并
    """
    blocks = page.get_text("blocks")
    
    # 检查是否有大量碎片化文字（表格特征）
    short_blocks = 0
    total_blocks = 0
    
    for block in blocks:
        if block[6] == 0:  # 文字块
            total_blocks += 1
            text = block[4].strip()
            # 1-3个字符的块是碎片化特征
            if 1 <= len(text) <= 3:
                short_blocks += 1
    
    # 如果短块占比超过 40%，尝试表格重组
    if total_blocks > 0 and short_blocks / total_blocks > 0.4:
        reconstructed = _reconstruct_table_text(page)
        if reconstructed:
            # 对比原字符数，保留更多字符的版本
            normal_text = _extract_text(page)
            if len(reconstructed) >= len(normal_text) * 0.7:
                print(f"    [表格重组] 原{len(normal_text)}字 → 重组后{len(reconstructed)}字")
                return reconstructed
    
    # 否则用普通提取
    return _extract_text(page)


def _reconstruct_table_text(page: fitz.Page) -> str:
    """
    表格结构重组：将碎片化的文字块按位置重新组合成完整单元格。
    适用于 Typora/Markdown 转 PDF 产生的碎片化表格。
    
    策略：
    1. 收集所有文字片段及其坐标
    2. 分析 X 坐标分布，识别列边界
    3. 同一列的文字按 Y 坐标合并为单元格
    4. 同行单元格用空格连接
    """
    try:
        blocks = page.get_text("dict")["blocks"]
        
        # 收集所有文字片段
        fragments: List[Tuple[float, float, str]] = []  # (x0, y0, text)
        
        for block in blocks:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span["text"].strip()
                    if not text:
                        continue
                    # 跳过纯符号块
                    if re.match(r'^[█░▓▒■□▪▫~]+$', text):
                        continue
                    bbox = span.get("bbox", [])
                    if len(bbox) == 4:
                        fragments.append((bbox[0], bbox[1], text))
        
        if not fragments:
            return ""
        
        # 分析 X 坐标分布，找列边界
        x_coords = [f[0] for f in fragments]
        # 简单的列检测：找 x 坐标的跳跃点
        x_sorted = sorted(set(x_coords))
        col_boundaries = []
        if x_sorted:
            prev_x = x_sorted[0]
            for x in x_sorted[1:]:
                gap = x - prev_x
                if gap > 20:  # 20pt 以上的间隔认为是列边界
                    col_boundaries.append((prev_x, x))
                prev_x = x
        
        # 按 Y 分组（行）
        y_tolerance = 5  # 5pt 容差
        rows: Dict[int, List[Tuple[float, str]]] = {}
        for x, y, text in fragments:
            row_key = int(y / y_tolerance)
            if row_key not in rows:
                rows[row_key] = []
            rows[row_key].append((x, text))
        
        # 重建表格
        result_lines = []
        for y_key in sorted(rows.keys()):
            row_fragments = sorted(rows[y_key], key=lambda t: t[0])
            # 同行片段直接拼接（Typora 的问题是每个字都被拆成独立块）
            merged = "".join(t[1] for t in row_fragments)
            # 清理重复的换行
            merged = re.sub(r'\s+', '', merged)
            if merged and not re.match(r'^[\d\s.,%\-+]+$', merged):
                result_lines.append(merged)
            elif merged and len(merged) > 2:
                result_lines.append(merged)
        
        # 智能合并：检测并连接被断开的单元格
        final_lines = []
        i = 0
        while i < len(result_lines):
            line = result_lines[i]
            # 如果当前行很短且下一行也很短，尝试合并
            if i + 1 < len(result_lines) and len(line) <= 5 and len(result_lines[i+1]) <= 10:
                merged = line + result_lines[i+1]
                final_lines.append(merged)
                i += 2
            else:
                final_lines.append(line)
                i += 1
        
        return "\n".join(final_lines)
    except Exception as e:
        print(f"    [表格重组失败] {e}")
        return ""


def _ocr_page_region(page: fitz.Page, bbox: fitz.Rect, dpi: int = 200) -> str:
    """
    对页面指定区域进行 OCR 文字识别。
    用于提取矢量图表中的文字。
    
    Args:
        page: fitz.Page 对象
        bbox: 要 OCR 的区域 (fitz.Rect)
        dpi: OCR 分辨率
    
    Returns:
        识别出的文字
    """
    try:
        import pytesseract
        from PIL import Image
        import io
        
        # 确保区域有效且有最小尺寸
        min_size = 50
        if bbox.width < min_size or bbox.height < min_size:
            return ""
        
        # 渲染指定区域为图片
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        
        # 添加一些 padding
        pad = 10
        clip = fitz.Rect(
            max(0, bbox.x0 - pad),
            max(0, bbox.y0 - pad),
            min(page.rect.width, bbox.x1 + pad),
            min(page.rect.height, bbox.y1 + pad)
        )
        
        # 确保 clip 有效
        if clip.width < min_size or clip.height < min_size:
            return ""
        
        pix = page.get_pixmap(matrix=mat, clip=clip, alpha=False)
        
        # 转换为 PIL Image
        img_data = pix.tobytes("png")
        img = Image.open(io.BytesIO(img_data))
        
        # 确保图片尺寸有效
        if img.width < 20 or img.height < 20:
            return ""
        
        # OCR 识别
        text = pytesseract.image_to_string(img, lang='chi_sim+eng', config='--psm 6')
        
        # 清理结果
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        return '\n'.join(lines)
    except ImportError:
        return ""
    except Exception as e:
        # 静默处理 OCR 错误
        return ""


def _get_text_in_vector_regions(page: fitz.Page) -> str:
    """
    提取矢量图表区域内的文字。
    检测页面上的矢量绘图区域（K线图、柱状图等），
    对这些区域进行 OCR 提取文字。
    """
    try:
        drawings = page.get_drawings()
        if not drawings:
            return ""
        
        page_area = page.rect.width * page.rect.height
        
        # 收集所有绘图路径的包围框
        all_rects = []
        for d in drawings:
            try:
                r = fitz.Rect(d["rect"])
                if r.width > 50 and r.height > 30:
                    all_rects.append(r)
            except:
                continue
        
        if not all_rects:
            return ""
        
        # 合并相邻区域
        merged = _merge_rects(all_rects, expand=10.0)
        
        # 取最大的几个区域
        merged.sort(key=lambda r: r.width * r.height, reverse=True)
        chart_regions = merged[:3]
        
        # 对每个图表区域进行 OCR
        ocr_texts = []
        for region in chart_regions:
            text = _ocr_page_region(page, region, dpi=200)
            if text and len(text) > 5:
                ocr_texts.append(text)
        
        if ocr_texts:
            result = "\n".join(ocr_texts)
            print(f"    [OCR矢量图表] 提取 {len(result)} 字符")
            return result
        
        return ""
    except Exception as e:
        print(f"    [矢量图表OCR失败] {e}")
        return ""


def _is_likely_chart_page(page: fitz.Page) -> bool:
    """
    判断页面是否可能包含矢量图表（K线图、柱状图等）。
    """
    drawings = page.get_drawings()
    images = page.get_images()
    
    # 大量矢量路径 + 少量嵌入图片 = 矢量图表
    if len(drawings) > 100 and len(images) < 2:
        return True
    
    # 检查是否有进度条字符
    text = page.get_text()
    if '█' in text or '░' in text:
        return True
    
    return False


# ═══════════════════════════════════════════════════════════════════════════════
# 智能章节解析功能
# ═══════════════════════════════════════════════════════════════════════════════


def _is_chapter_heading(text: str, font_size: float, max_font: float) -> bool:
    """
    判断文字是否是章节标题。
    
    规则：
    1. 以章节编号开头（一、二、三、1、2、3 等）
    2. 字体较大（>= max_font * 0.5）
    3. 长度适中（3-30字符）
    4. 不是纯数字或纯符号
    5. 排除表格中的数字（如 26.2、4505.60 等）
    6. 排除数字开头的总结项（如 "1. 沪深300期货"）
    """
    # 排除纯数字（可能是表格数据）
    if re.match(r'^[\d.,%/]+$', text):
        return False
    
    # 排除太短的数字组合
    if len(text) <= 4 and re.match(r'^[\d.]+$', text):
        return False
    
    # 章节标题模式
    chapter_patterns = [
        r'^[一二三四五六七八九十]+[、.．]',  # 一、二、三、
        r'^[（(][0-9]+[）)]',               # (1) (2)
        r'^【[^】]+】',                      # 【标题】
        r'^第[一二三四五六七八九十0-9]+[章节节部分]',  # 第一章
    ]
    
    for pattern in chapter_patterns:
        if re.match(pattern, text):
            return True
    
    # 排除数字开头的总结项（这些不是章节，而是列表项）
    # 例如："1. 沪深300期货"、"2. 中证500期货" 等
    if re.match(r'^[0-9]+[.．]', text):
        # 检查是否包含期货品种关键字，如果是则不是章节
        summary_keywords = ['期货', '持仓', '基差', '评分', '综合']
        for keyword in summary_keywords:
            if keyword in text:
                return False
        # 数字开头但没有品种关键字，可能是小节标题，也排除以避免碎片化
        if len(text) > 30:  # 太长，通常是列表项而非标题
            return False
    
    # 常见章节关键词
    chapter_keywords = [
        '市场概览', '详细分析', '综合研判', '总结',
        '贴升贴水', '持仓分析', '成交量', '行情回顾',
        '技术分析', '基本面', '操作建议', '风险提示',
        '宏观分析', '行业分析', '个股分析',
        '数据解读', '市场情绪', '资金流向',
        '投资建议', '操作策略', '行情研判',
    ]
    
    # 字体较大且包含章节关键词，且不是纯数字
    if font_size >= max_font * 0.5 and len(text) >= 3 and len(text) <= 30:
        # 排除以数字开头的
        if not re.match(r'^[\d]', text):
            for keyword in chapter_keywords:
                if keyword in text:
                    return True
    
    return False


def _merge_adjacent_spans(line: dict) -> str:
    """
    合并一行中相邻的 span，保留所有文字。
    用于处理章节标题被拆分成多个 span 的情况。
    """
    spans = line.get('spans', [])
    if not spans:
        return ""
    
    # 按 x0 坐标排序
    sorted_spans = sorted(spans, key=lambda s: s.get('bbox', [0])[0])
    
    # 合并文字
    merged = ''.join(span['text'] for span in sorted_spans)
    return merged.strip()


def _merge_line_spans(blocks: List[dict]) -> List[Tuple[float, str, float]]:
    """
    合并页面中所有行的文字和字体，返回 (y0, merged_text, max_font_size) 列表。
    按 y 坐标排序，同一行只返回合并后的文字。
    使用行中最大字体作为判断依据。
    """
    lines_data = []
    
    for block in blocks:
        if block.get('type') != 0:
            continue
        for line in block.get('lines', []):
            spans = line.get('spans', [])
            if not spans:
                continue
            
            # 获取这一行所有 span 的字体，取最大值
            font_sizes = [span.get('size', 0) for span in spans]
            max_font_size = max(font_sizes) if font_sizes else 0
            
            # 合并所有 span 的文字
            merged = _merge_adjacent_spans(line)
            
            if merged:
                y0 = line.get('bbox', [0, 0, 0, 0])[1]
                lines_data.append((y0, merged, max_font_size))
    
    # 按 y 坐标排序
    lines_data.sort(key=lambda x: x[0])
    return lines_data


def _extract_report_title_from_first_page(page: fitz.Page) -> str:
    """
    从第一页提取报告标题。
    策略：取最大字体的第一行文字。
    """
    try:
        blocks = page.get_text("dict")["blocks"]
        max_size = 0
        title = ""
        
        for block in blocks:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span["text"].strip()
                    size = span.get("size", 0)
                    if size > max_size and len(text) >= 2:
                        # 排除非标题内容
                        if not any([text.startswith(x) for x in ['http', 'www', '分析日期', '发布日期']]):
                            max_size = size
                            title = text
        
        return _clean_pdf_text(title)[:60]
    except Exception:
        return ""


def _extract_date_from_text(text: str) -> str:
    """
    从文本中提取日期。
    """
    date_patterns = [
        r'(\d{4})年(\d{1,2})月(\d{1,2})日',
        r'(\d{4})-(\d{2})-(\d{2})',
        r'(\d{4})/(\d{2})/(\d{2})',
        r'(\d{4})\.(\d{2})\.(\d{2})',
    ]
    
    for pattern in date_patterns:
        m = re.search(pattern, text)
        if m:
            return m.group(0)
    
    return ""


def _extract_abstract_from_first_pages(doc: fitz.Document, max_chars: int = 300) -> str:
    """
    从前几页提取摘要内容。
    策略：取 "报告摘要" 之后、正文之前的文字。
    """
    try:
        # 扫描前3页
        abstract_parts = []
        found_abstract = False
        
        for page_idx in range(min(3, len(doc))):
            page = doc[page_idx]
            text = page.get_text()
            
            # 查找 "报告摘要" 标记
            if '报告摘要' in text or '摘要' in text:
                found_abstract = True
            
            if found_abstract:
                # 提取摘要部分（直到遇到第一个章节标题）
                lines = text.split('\n')
                for line in lines:
                    line = line.strip()
                    # 遇到章节标题停止
                    if re.match(r'^[一二三四]+[、]', line) or re.match(r'^[0-9]+[.．]', line):
                        break
                    if line and len(line) > 10:
                        abstract_parts.append(line)
        
        abstract = ' '.join(abstract_parts)[:max_chars]
        return _clean_pdf_text(abstract)
    except Exception:
        return ""


def parse_pdf_smart(pdf_path: str, pages: Optional[str] = None) -> Tuple[ReportMeta, List[ChapterSection]]:
    """
    智能解析 PDF，识别报告结构。
    
    Returns:
        Tuple[ReportMeta, List[ChapterSection]]: 元信息和章节列表
    """
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF 文件不存在: {pdf_path}")
    
    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    
    # 获取最大字体
    max_font = _get_doc_max_font(doc)
    
    # 1. 提取报告元信息
    meta = ReportMeta(total_pages=total_pages)
    
    # 报告标题（第一页最大字体）
    first_page = doc[0]
    meta.title = _extract_report_title_from_first_page(first_page)
    
    # 日期（从全文提取）
    full_text = '\n'.join(doc[i].get_text() for i in range(min(3, total_pages)))
    meta.date = _extract_date_from_text(full_text)
    
    # 摘要
    meta.abstract = _extract_abstract_from_first_pages(doc)
    
    # 2. 识别章节边界
    sections: List[Dict] = []
    current_section: Optional[Dict] = None
    
    for page_idx in range(total_pages):
        page = doc[page_idx]
        blocks = page.get_text("dict")["blocks"]
        
        # 使用合并后的行文字来识别章节标题
        lines_data = _merge_line_spans(blocks)
        
        # 收集这一页所有章节标题
        page_chapters = []
        for y0, merged_text, font_size in lines_data:
            text = merged_text.strip()
            
            if _is_chapter_heading(text, font_size, max_font):
                page_chapters.append((y0, text, font_size))
        
        if page_chapters:
            # 结束当前章节（如果有）
            if current_section is not None:
                sections.append(current_section)
            
            # 第一个章节作为当前章节
            current_section = {
                "title": _clean_pdf_text(page_chapters[0][1]),
                "start_page": page_idx + 1,
                "page_indices": [page_idx],
            }
            
            # 同一页的其他章节标题，各自成为独立的章节
            for i in range(1, len(page_chapters)):
                sections.append(current_section)
                current_section = {
                    "title": _clean_pdf_text(page_chapters[i][1]),
                    "start_page": page_idx + 1,
                    "page_indices": [page_idx],
                }
        else:
            # 没有章节标题，继续当前章节（如果有的话）
            if current_section is not None:
                current_section["page_indices"].append(page_idx)
            else:
                # 没有任何章节，创建一个默认章节
                current_section = {
                    "title": "报告正文",
                    "start_page": page_idx + 1,
                    "page_indices": [page_idx],
                }
    
    if current_section is not None:
        sections.append(current_section)
    
    # 3. 合并重复的章节标题（只保留第一个）
    seen_titles = set()
    merged_sections: List[Dict] = []
    for sec in sections:
        title = sec["title"]
        if title in seen_titles:
            # 合并到之前的章节
            if merged_sections:
                merged_sections[-1]["page_indices"].extend(sec["page_indices"])
        else:
            seen_titles.add(title)
            merged_sections.append(sec)
    
    sections = merged_sections
    
    # 4. 提取每个章节的内容和图片
    chapter_list: List[ChapterSection] = []
    
    for sec in sections:
        chapter = ChapterSection(
            title=sec["title"],
            start_page=sec["start_page"],
            page_indices=sec["page_indices"],
        )
        
        # 提取内容和图片
        content_parts = []
        all_image_paths = []
        
        for page_idx in sec["page_indices"]:
            page = doc[page_idx]
            extracted_text = _extract_text_enhanced(page)
            
            # 如果是图表页，添加 OCR 内容
            if _is_likely_chart_page(page):
                ocr_text = _get_text_in_vector_regions(page)
                if ocr_text:
                    extracted_text = extracted_text + "\n" + ocr_text
            
            # 提取页面图片
            image_paths = _extract_page_images(page, pdf_path, page_idx)
            all_image_paths.extend(image_paths)
            
            content_parts.append(extracted_text)
        
        chapter.content = "\n\n".join(content_parts)
        chapter.image_paths = all_image_paths
        chapter_list.append(chapter)
    
    doc.close()
    
    return meta, chapter_list
