"""
配图模块
- 优先使用 PDF 中提取的原始图表图片
- 无图或图片不足时调用 MiniMax image-01 API 生成金融主题配图
"""
import os
import requests
from typing import List, Optional
from config import QWEN_IMAGE_API_KEY, QWEN_IMAGE_BASE_URL, TEMP_DIR
try:
    from config import MINIMAX_IMAGE_MODEL
except ImportError:
    MINIMAX_IMAGE_MODEL = "image-01"


def get_images_for_page(
    page_num: int,
    pdf_images: List[str],
    page_title: str = "",
    pdf_name: str = "report",
    generate_if_empty: bool = True,
) -> List[str]:
    """
    获取某页的配图列表

    Args:
        page_num: 页码
        pdf_images: PDF 中提取的图片路径列表
        page_title: 页面标题（用于 AI 生成配图的提示词）
        pdf_name: PDF 文件名（用于临时目录）
        generate_if_empty: 无图时是否调用 AI 生成

    Returns:
        图片路径列表（优先 PDF 原图，不足时 AI 补充）
    """
    if pdf_images:
        return pdf_images

    if not generate_if_empty:
        return []

    # 调用 MiniMax image-01 生成配图
    print(f"    第 {page_num} 页无图，调用 MiniMax image-01 生成配图...")
    ai_image = _generate_finance_image(page_title, page_num, pdf_name)
    if ai_image:
        return [ai_image]

    return []


def _generate_finance_image(
    topic: str,
    page_num: int,
    pdf_name: str,
) -> Optional[str]:
    """
    调用 MiniMax image-01 文生图 API 生成金融主题配图
    接口为同步接口，直接返回 image_urls，无需轮询

    Args:
        topic: 主题关键词（来自页面标题）
        page_num: 页码（用于文件命名）
        pdf_name: PDF名（用于保存路径）

    Returns:
        生成的图片本地路径，失败返回 None
    """
    # 构建金融风格提示词
    if topic:
        prompt = f"金融数据可视化图表，{topic}，专业商务风格，蓝色主色调，科技感，数据图表背景，高清"
    else:
        prompt = "金融市场数据图表，股市行情，专业商务风格，蓝色主色调，科技感，高清壁纸"

    headers = {
        "Authorization": f"Bearer {QWEN_IMAGE_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": MINIMAX_IMAGE_MODEL,
        "prompt": prompt,
        "aspect_ratio": "16:9",   # 1280x720，适配视频横屏布局
        "response_format": "url",
        "n": 1,
        "prompt_optimizer": True,
    }

    try:
        resp = requests.post(QWEN_IMAGE_BASE_URL, json=payload, headers=headers, timeout=60)
        if resp.status_code != 200:
            print(f"    [警告] MiniMax 文生图失败: {resp.status_code} {resp.text[:200]}")
            return None

        data = resp.json()
        # 检查业务状态码
        base_resp = data.get("base_resp", {})
        if base_resp.get("status_code", 0) != 0:
            print(f"    [警告] MiniMax 文生图错误: {base_resp.get('status_msg')}")
            return None

        image_urls = data.get("data", {}).get("image_urls", [])
        if not image_urls:
            print(f"    [警告] MiniMax 未返回图片 URL")
            return None

        print(f"    MiniMax image-01 生成成功")
        return _download_image(image_urls[0], page_num, pdf_name)

    except Exception as e:
        print(f"    [警告] MiniMax 文生图异常: {e}")
        return None


def _download_image(url: str, page_num: int, pdf_name: str) -> Optional[str]:
    """下载图片到临时目录"""
    save_dir = os.path.join(TEMP_DIR, pdf_name, "ai_images")
    os.makedirs(save_dir, exist_ok=True)
    save_path = os.path.join(save_dir, f"ai_page_{page_num:03d}.png")

    try:
        resp = requests.get(url, timeout=30)
        with open(save_path, "wb") as f:
            f.write(resp.content)
        print(f"    AI配图已保存: {save_path}")
        return save_path
    except Exception as e:
        print(f"    [警告] 图片下载失败: {e}")
        return None
