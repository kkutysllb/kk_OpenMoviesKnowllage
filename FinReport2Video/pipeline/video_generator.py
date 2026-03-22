"""
背景视频生成模块 v2 - 本地 Ken Burns 动效（零 API 费用）

使用 PDF 页面截图做 Ken Burns 缓动效果：
  - 缓慢平移（左→右 或 右→左 交替）
  - 轻微缩放（1.0x → 1.12x）
  - 色调叠加（深蓝/深红等金融色，增强专业感）

片头页专用动态背景：
  - 期指风格 K 线网格背景（纯本地 NumPy 绘制）
  - 数字流下落动效
  - 深蓝渐变，金融专业感

无网络调用，无 API 费用，生成速度快（< 1s）。
"""
import os
import sys
import hashlib
import random
from typing import Optional

import numpy as np
from PIL import Image, ImageFilter, ImageEnhance, ImageDraw

from moviepy import VideoClip

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import VIDEO_SIZE, VIDEO_FPS

VW, VH = VIDEO_SIZE


def generate_intro_bg_video(
    pdf_name: str,
    duration: float = 10.0,
    temp_dir: str = "temp",
    theme: str = "futures",  # futures / bull / bear
) -> Optional[str]:
    """
    片头页专用动态背景：期指风格 K 线 + 数字流动效（纯本地）
    theme: futures=期指深蓝, bear=空头深红, bull=多头深绿
    """
    save_dir = os.path.join(temp_dir, pdf_name, "bg_videos")
    os.makedirs(save_dir, exist_ok=True)
    save_path = os.path.join(save_dir, "page_000.mp4")

    if os.path.exists(save_path) and os.path.getsize(save_path) > 1000:
        print(f"    背景视频已缓存，跳过生成: {save_path}")
        return save_path

    print(f"    生成期指动态背景（本地）...")
    try:
        clip = _make_futures_intro_clip(duration, theme)
        clip.write_videofile(
            save_path, fps=VIDEO_FPS, codec="libx264",
            preset="fast", logger=None,
        )
        size_kb = os.path.getsize(save_path) / 1024
        print(f"    片头背景已生成: {save_path} ({size_kb:.0f} KB)")
        return save_path
    except Exception as e:
        print(f"    [警告] 片头背景生成失败: {e}")
        return None


def _make_futures_intro_clip(duration: float, theme: str) -> VideoClip:
    """
    期指动态背景：
    - 深色渐变底色（深蓝→深红戚或纯深蓝）
    - 谷线水平网格线动效（缓慢向上移动）
    - 纯数字流列（随机 +/- 百分比向下流动）
    - 右下角隐约 K 线骨架
    """
    # 主题配色
    if theme == "bear":
        bg_top    = (45,  5,  5)
        bg_bot    = (15,  3, 20)
        grid_col  = (160, 20, 20, 35)
        num_col   = (255, 80, 80)
        kline_up  = (80, 160, 80)
        kline_dn  = (220, 50, 50)
    elif theme == "bull":
        bg_top    = (5, 40, 15)
        bg_bot    = (3, 15, 30)
        grid_col  = (20, 160, 60, 35)
        num_col   = (80, 255, 120)
        kline_up  = (60, 220, 80)
        kline_dn  = (200, 60, 60)
    else:  # futures期指默认
        bg_top    = (5, 12, 50)
        bg_bot    = (3,  6, 28)
        grid_col  = (40, 80, 200, 30)
        num_col   = (100, 160, 255)
        kline_up  = (80, 200, 100)
        kline_dn  = (220, 60, 60)

    # 预生成随机数字流列
    rng = random.Random(42)
    NUM_STREAMS = 28
    streams = []
    for _ in range(NUM_STREAMS):
        x = rng.randint(0, VW - 1)
        y_start = rng.randint(-VH, 0)
        speed = rng.uniform(40, 120)   # px/s
        digits = [rng.choice(["+", "-"]) +
                  f"{rng.uniform(0.1, 9.9):.2f}%"
                  for _ in range(20)]
        streams.append({"x": x, "y": y_start, "speed": speed,
                        "digits": digits, "alpha": rng.uniform(0.25, 0.7)})

    # 预生随机 K 线列表（右下角装饰）
    NUM_KLINES = 18
    klines = []
    bar_w = 28
    spacing = 36
    kline_x_start = VW - (NUM_KLINES * spacing) - 60
    kline_y_mid   = VH - 120
    for i in range(NUM_KLINES):
        h = rng.randint(30, 150)
        wick = rng.randint(5, 30)
        is_up = rng.random() > 0.45
        klines.append({
            "x": kline_x_start + i * spacing,
            "y_open":  kline_y_mid + rng.randint(-60, 60),
            "height": h, "wick": wick, "is_up": is_up
        })

    from PIL import Image as PILImage, ImageDraw as PILDraw, ImageFont as PILFont

    try:
        font_num = PILFont.truetype("/System/Library/Fonts/Helvetica.ttc", 16)
    except Exception:
        font_num = PILFont.load_default()

    GRID_ROWS, GRID_COLS = 14, 24
    GRID_SPEED = 18  # px/s 网格向上滖动速度

    def make_frame(t: float) -> np.ndarray:
        # ─ 1. 渐变底色 ─
        arr = np.zeros((VH, VW, 3), dtype=np.uint8)
        for row in range(VH):
            ratio = row / VH
            for ch in range(3):
                arr[row, :, ch] = int(bg_top[ch] * (1 - ratio) + bg_bot[ch] * ratio)

        # ─ 2. 网格线（缓慢向上滖动）─
        img = PILImage.fromarray(arr, "RGB").convert("RGBA")
        draw = PILDraw.Draw(img)

        offset_y = int(t * GRID_SPEED) % (VH // GRID_ROWS)
        col_gap  = VW // GRID_COLS
        row_gap  = VH // GRID_ROWS

        for ci in range(GRID_COLS + 1):
            x = ci * col_gap
            draw.line([(x, 0), (x, VH)], fill=grid_col, width=1)
        for ri in range(GRID_ROWS + 2):
            y = ri * row_gap - offset_y
            draw.line([(0, y), (VW, y)], fill=grid_col, width=1)

        # ─ 3. 数字流 ─
        for s in streams:
            y_pos = (s["y"] + t * s["speed"]) % (VH + 200) - 200
            alpha = int(255 * s["alpha"])
            for di, digit in enumerate(s["digits"]):
                dy = y_pos + di * 22
                if -22 < dy < VH + 22:
                    col = (*num_col, alpha)
                    try:
                        draw.text((s["x"], int(dy)), digit, fill=col, font=font_num)
                    except Exception:
                        pass

        # ─ 4. K 线骨架（右下角）─
        for kl in klines:
            color = kline_up if kl["is_up"] else kline_dn
            x   = kl["x"]
            y0  = kl["y_open"]
            h   = kl["height"]
            wk  = kl["wick"]
            # 影线
            draw.line([(x + bar_w // 2, y0 - wk),
                       (x + bar_w // 2, y0 + h + wk)],
                      fill=(*color, 160), width=2)
            # 实体
            draw.rectangle([x, y0, x + bar_w, y0 + h],
                           fill=(*color, 120))

        # ─ 5. 全局轻微魅光渐变（周期性）─
        pulse = 0.88 + 0.12 * np.sin(t * 0.8)
        arr2 = np.array(img)[:, :, :3].astype(np.float32)
        arr2 = np.clip(arr2 * pulse, 0, 255).astype(np.uint8)
        return arr2

    return VideoClip(make_frame, duration=duration)


def generate_bg_video(
    prompt: str,
    page_num: int,
    pdf_name: str,
    temp_dir: str = "temp",
    duration: int = 5,
    screenshot_path: Optional[str] = None,
) -> Optional[str]:
    """
    本地生成背景视频（Ken Burns 动效）。带缓存：已有则直接返回。

    Args:
        prompt:          描述文字（用于决定色调，不调用 API）
        page_num:        页码（用于缓存文件名和方向交替）
        pdf_name:        PDF 名称（缓存目录）
        temp_dir:        临时目录
        duration:        视频时长（秒）
        screenshot_path: PDF 截图路径（作为背景图源）

    Returns:
        本地 mp4 文件路径，若失败返回 None
    """
    save_dir = os.path.join(temp_dir, pdf_name, "bg_videos")
    os.makedirs(save_dir, exist_ok=True)
    save_path = os.path.join(save_dir, f"page_{page_num:03d}.mp4")

    if os.path.exists(save_path) and os.path.getsize(save_path) > 1000:
        print(f"    背景视频已缓存，跳过生成: {save_path}")
        return save_path

    print(f"    本地生成背景视频（Ken Burns）...")

    try:
        # 1. 加载背景图（截图 or 渐变色兜底）
        base_img = _load_background_image(screenshot_path, prompt, page_num)

        # 2. 生成 Ken Burns 视频
        clip = _make_ken_burns_clip(base_img, duration, page_num, prompt)

        # 3. 导出 mp4
        clip.write_videofile(
            save_path,
            fps=VIDEO_FPS,
            codec="libx264",
            preset="fast",
            logger=None,
        )
        size_kb = os.path.getsize(save_path) / 1024
        print(f"    背景视频已生成: {save_path} ({size_kb:.0f} KB)")
        return save_path

    except Exception as e:
        print(f"    [警告] 本地背景生成失败: {e}")
        return None


# ── 背景图加载 ─────────────────────────────────────────────────────────────────

def _load_background_image(
    screenshot_path: Optional[str],
    prompt: str,
    page_num: int,
) -> Image.Image:
    """
    加载背景图：
    1. 优先用 PDF 截图（模糊处理，作为视频背景）
    2. 截图不存在则生成渐变色背景
    """
    if screenshot_path and os.path.exists(screenshot_path):
        img = Image.open(screenshot_path).convert("RGB")
        # 适当模糊，避免文字与叠加内容冲突
        img = img.filter(ImageFilter.GaussianBlur(radius=3))
        # 降低亮度，让上层文字更清晰
        img = ImageEnhance.Brightness(img).enhance(0.55)
        # 叠加色调（根据 prompt 关键词决定颜色）
        img = _apply_color_tint(img, prompt, page_num)
        # 放大到 1.2x（Ken Burns 需要额外空间平移）
        w, h = img.size
        img = img.resize((int(w * 1.2), int(h * 1.2)), Image.LANCZOS)
    else:
        img = _make_gradient_bg(prompt, page_num)

    # 确保足够大覆盖 1920x1080
    min_w = int(VW * 1.2)
    min_h = int(VH * 1.2)
    w, h = img.size
    if w < min_w or h < min_h:
        scale = max(min_w / w, min_h / h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    return img


def _apply_color_tint(img: Image.Image, prompt: str, page_num: int) -> Image.Image:
    """根据内容关键词叠加色调，增强专业感"""
    p = prompt.lower()
    if any(k in p for k in ["空头", "下跌", "弱势", "跌", "亏"]):
        tint = (180, 20, 20)      # 深红（下跌/空头）
        alpha = 0.30
    elif any(k in p for k in ["多头", "上涨", "强势", "涨", "盈"]):
        tint = (20, 140, 60)      # 深绿（上涨/多头）
        alpha = 0.25
    elif any(k in p for k in ["债券", "利率", "国债"]):
        tint = (20, 60, 140)      # 深蓝（固收）
        alpha = 0.30
    elif any(k in p for k in ["科技", "半导体", "芯片"]):
        tint = (60, 20, 160)      # 深紫（科技）
        alpha = 0.28
    else:
        # 默认：深海蓝（金融通用）
        tint = (10, 30, 80)
        alpha = 0.35

    overlay = Image.new("RGB", img.size, tint)
    return Image.blend(img, overlay, alpha)


def _make_gradient_bg(prompt: str, page_num: int) -> Image.Image:
    """无截图时生成渐变色背景"""
    p = prompt.lower()
    if any(k in p for k in ["空头", "下跌", "弱势"]):
        c1, c2 = (60, 5, 5), (25, 10, 20)
    elif any(k in p for k in ["多头", "上涨", "强势"]):
        c1, c2 = (5, 50, 20), (10, 25, 40)
    else:
        c1, c2 = (8, 18, 55), (3, 8, 30)

    w, h = int(VW * 1.25), int(VH * 1.25)
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    for y in range(h):
        t = y / h
        for ch in range(3):
            arr[y, :, ch] = int(c1[ch] * (1 - t) + c2[ch] * t)

    # 添加噪点，增加质感
    noise = np.random.randint(0, 18, (h, w, 3), dtype=np.uint8)
    arr = np.clip(arr.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    return Image.fromarray(arr)


# ── Ken Burns 动效 ────────────────────────────────────────────────────────────

def _make_ken_burns_clip(
    img: Image.Image,
    duration: float,
    page_num: int,
    prompt: str,
) -> VideoClip:
    """
    生成 Ken Burns 效果视频：
    - 缓慢平移（奇偶页交替左→右 / 右→左）
    - 轻微缩放（1.0x → 1.12x）
    - easing：ease-in-out（smooth S曲线）
    """
    img_arr = np.array(img)
    ih, iw = img_arr.shape[:2]

    # 缩放范围：从 scale_start 到 scale_end
    scale_start = 1.0
    scale_end   = 1.12

    # 平移方向：奇偶页交替
    left_to_right = (page_num % 2 == 0)

    def _ease(t: float) -> float:
        """平滑 S 曲线 ease-in-out"""
        return t * t * (3 - 2 * t)

    def make_frame(t: float) -> np.ndarray:
        progress = _ease(t / duration) if duration > 0 else 0.0

        # 当前缩放倍数
        scale = scale_start + (scale_end - scale_start) * progress

        # 裁剪区域大小（目标 1920x1080 / scale）
        crop_w = int(VW / scale)
        crop_h = int(VH / scale)
        crop_w = min(crop_w, iw)
        crop_h = min(crop_h, ih)

        # 平移范围（可滑动的最大偏移量）
        max_dx = iw - crop_w
        max_dy = ih - crop_h

        if left_to_right:
            x0 = int(max_dx * progress)
        else:
            x0 = int(max_dx * (1 - progress))
        y0 = int(max_dy * 0.5)  # 垂直居中（仅水平平移）

        x0 = max(0, min(x0, iw - crop_w))
        y0 = max(0, min(y0, ih - crop_h))

        # 裁剪
        crop = img_arr[y0:y0 + crop_h, x0:x0 + crop_w]

        # 缩放到目标尺寸
        frame_img = Image.fromarray(crop).resize((VW, VH), Image.LANCZOS)
        return np.array(frame_img)

    return VideoClip(make_frame, duration=duration)
