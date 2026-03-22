"""
配图模块
- 优先使用 PDF 中提取的原始图表图片
- 无图或图片不足时调用通义万相 API 生成金融主题配图
"""
import os
import time
import requests
from typing import List, Optional
from config import QWEN_IMAGE_API_KEY, QWEN_IMAGE_BASE_URL, TEMP_DIR


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

    # 调用通义万相生成配图
    print(f"    第 {page_num} 页无图，调用通义万相生成配图...")
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
    调用通义万相文生图 API 生成金融主题配图

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
        "X-DashScope-Async": "enable",
    }

    payload = {
        "model": "wanx2.1-t2i-turbo",
        "input": {"prompt": prompt},
        "parameters": {
            "size": "1024*576",
            "n": 1,
            "style": "<photography>",
        },
    }

    try:
        # 提交异步任务
        resp = requests.post(QWEN_IMAGE_BASE_URL, json=payload, headers=headers, timeout=30)
        if resp.status_code != 200:
            print(f"    [警告] 通义万相提交失败: {resp.status_code} {resp.text[:200]}")
            return None

        task_id = resp.json().get("output", {}).get("task_id")
        if not task_id:
            print(f"    [警告] 未获取到 task_id")
            return None

        # 轮询等待任务完成
        image_url = _poll_task(task_id)
        if not image_url:
            return None

        # 下载图片到本地
        return _download_image(image_url, page_num, pdf_name)

    except Exception as e:
        print(f"    [警告] 通义万相生成失败: {e}")
        return None


def _poll_task(task_id: str, max_wait: int = 60) -> Optional[str]:
    """轮询等待通义万相异步任务完成"""
    poll_url = f"https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"
    headers = {"Authorization": f"Bearer {QWEN_IMAGE_API_KEY}"}

    for _ in range(max_wait // 3):
        time.sleep(3)
        try:
            resp = requests.get(poll_url, headers=headers, timeout=15)
            data = resp.json()
            status = data.get("output", {}).get("task_status", "")

            if status == "SUCCEEDED":
                results = data.get("output", {}).get("results", [])
                if results:
                    return results[0].get("url")
                return None

            elif status in ("FAILED", "CANCELED"):
                print(f"    [警告] 图片生成任务失败: {status}")
                return None

        except Exception:
            continue

    print(f"    [警告] 图片生成超时")
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
