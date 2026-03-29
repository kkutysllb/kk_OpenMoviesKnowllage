"""
背景视频生成模块 v3 - 支持 MiniMax 文生视频 + 本地 Ken Burns 两种模式

优先级：
  1. MiniMax Hailuo 文生视频（开启 MINIMAX_VIDEO_ENABLED=true 时）
  2. 本地 Ken Burns 动效（默认，零 API 费用）

MiniMax 文生视频调用流程（异步）：
  Step1: POST /v1/video_generation → 返回 task_id
  Step2: GET  /v1/query/video_generation?task_id=xxx → 轮询直到 status=Success 得 file_id
  Step3: GET  /v1/files/retrieve?file_id=xxx → 获取 download_url 下载 mp4

本地 Ken Burns 动效：
  - 缓慢平移（左→右 或 右→左 交替）
  - 轻微缩放（1.0x → 1.12x）
  - 色调叠加（深蓝/深红等金融色，增强专业感）
片头页专用动态背景：
  - 期指风格 K 线网格背景（纯本地 NumPy 绘制）
  - 数字流下落动效
  - 深蓝渐变，金融专业感
"""
import os
import sys
import time
import hashlib
import random
from typing import Optional

import requests
import numpy as np
from PIL import Image, ImageFilter, ImageEnhance, ImageDraw

from moviepy import VideoClip

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import VIDEO_SIZE, VIDEO_FPS, BG_VIDEO_FPS
try:
    from config import LLM_API_KEY, MINIMAX_IMAGE_MODEL
except ImportError:
    LLM_API_KEY = ""
    MINIMAX_IMAGE_MODEL = "image-01"

# 是否启用 MiniMax 文生视频（默认关闭，防止意外消耗 Token）
MINIMAX_VIDEO_ENABLED = os.getenv("MINIMAX_VIDEO_ENABLED", "false").lower() == "true"
MINIMAX_VIDEO_BASE_URL = "https://api.minimaxi.com/v1"
MINIMAX_VIDEO_MODEL = "MiniMax-Hailuo-2.3-Fast"  # Token Plan 免费配额模型（768P）

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
            save_path, fps=BG_VIDEO_FPS, codec="libx264",
            preset="ultrafast", logger=None,
        )
        size_kb = os.path.getsize(save_path) / 1024
        print(f"    片头背景已生成: {save_path} ({size_kb:.0f} KB)")
        return save_path
    except Exception as e:
        print(f"    [警告] 片头背景生成失败: {e}")
        return None


def _make_futures_intro_clip(duration: float, theme: str) -> VideoClip:
    """
    现代金融风格动态背景：
    - 深色科技渐变底色（深蓝/深灰 + 紫色调）
    - 细网格线动效（缓慢向上移动）
    - 发光粒子效果
    - 现代感波浪线（代替传统K线）
    - 数字流带发光效果
    """
    # 主题配色
    if theme == "bear":
        bg_top    = (40,  10,  20)
        bg_bot    = (15,  5,  30)
        grid_col  = (120, 30, 60, 20)
        num_col   = (255, 100, 120)
        wave_col  = (200, 80, 100)
        glow_col  = (255, 80, 100)
    elif theme == "bull":
        bg_top    = (10, 50, 30)
        bg_bot    = (5,  20, 40)
        grid_col  = (40, 180, 100, 20)
        num_col   = (100, 255, 150)
        wave_col  = (80, 220, 120)
        glow_col  = (80, 255, 150)
    else:  # 默认现代科技风格
        bg_top    = (15, 25, 55)
        bg_bot    = (8,  12, 35)
        grid_col  = (60, 100, 200, 15)
        num_col   = (100, 180, 255)
        wave_col  = (80, 160, 255)
        glow_col  = (80, 140, 255)

    rng = random.Random(42)

    # 预生成随机数字流列（带发光效果）
    NUM_STREAMS = 20
    streams = []
    for _ in range(NUM_STREAMS):
        x = rng.randint(0, VW - 1)
        y_start = rng.randint(-VH, 0)
        speed = rng.uniform(30, 80)
        digits = [rng.choice(["+", "-"]) +
                  f"{rng.uniform(0.1, 9.9):.2f}%"
                  for _ in range(25)]
        streams.append({
            "x": x, "y": y_start, "speed": speed,
            "digits": digits,
            "alpha": rng.uniform(0.3, 0.6),
            "glow": rng.uniform(0.15, 0.3)
        })

    # 预生成波浪线控制点
    NUM_WAVES = 5
    waves = []
    for i in range(NUM_WAVES):
        waves.append({
            "x_start": rng.randint(0, VW // 2),
            "y_base": rng.randint(VH // 3, VH - 200),
            "amplitude": rng.randint(30, 80),
            "frequency": rng.uniform(0.005, 0.015),
            "phase": rng.uniform(0, 2 * np.pi),
            "width": rng.randint(2, 4),
            "alpha": rng.uniform(0.2, 0.4),
        })

    # 预生成发光粒子
    NUM_PARTICLES = 40
    particles = []
    for _ in range(NUM_PARTICLES):
        particles.append({
            "x": rng.randint(0, VW),
            "y": rng.randint(0, VH),
            "size": rng.uniform(1, 3),
            "speed_x": rng.uniform(-5, 5),
            "speed_y": rng.uniform(-15, -5),
            "alpha": rng.uniform(0.2, 0.5),
        })

    from PIL import Image as PILImage, ImageDraw as PILDraw, ImageFont as PILFont

    try:
        font_num = PILFont.truetype("/System/Library/Fonts/Helvetica.ttc", 14)
    except Exception:
        font_num = PILFont.load_default()

    GRID_ROWS, GRID_COLS = 16, 28
    GRID_SPEED = 12  # px/s 网格向上滚动速度

    def make_frame(t: float) -> np.ndarray:
        # ─ 1. 渐变底色（带紫色调） ─
        arr = np.zeros((VH, VW, 3), dtype=np.uint8)
        for row in range(VH):
            ratio = row / VH
            for ch in range(3):
                val = int(bg_top[ch] * (1 - ratio) + bg_bot[ch] * ratio)
                arr[row, :, ch] = min(255, max(0, val))

        img = PILImage.fromarray(arr, "RGB").convert("RGBA")
        draw = PILDraw.Draw(img)

        # ─ 2. 细网格线（向上滚动）─
        offset_y = int(t * GRID_SPEED) % (VH // GRID_ROWS)
        col_gap = VW // GRID_COLS
        row_gap = VH // GRID_ROWS

        for ci in range(GRID_COLS + 1):
            x = ci * col_gap
            draw.line([(x, 0), (x, VH)], fill=grid_col, width=1)
        for ri in range(GRID_ROWS + 2):
            y = ri * row_gap - offset_y
            draw.line([(0, y), (VW, y)], fill=grid_col, width=1)

        # ─ 3. 发光粒子 ─
        for p in particles:
            px = int((p["x"] + t * p["speed_x"]) % VW)
            py = int((p["y"] + t * p["speed_y"]) % VH)
            alpha = int(255 * p["alpha"] * (0.5 + 0.5 * np.sin(t * 2 + p["x"])))
            # 绘制发光圆点
            draw.ellipse([px - int(p["size"]), py - int(p["size"]),
                         px + int(p["size"]), py + int(p["size"])],
                        fill=(*glow_col, alpha))

        # ─ 4. 现代波浪线（代替传统K线）─
        for w in waves:
            points = []
            for x in range(w["x_start"], min(w["x_start"] + 400, VW), 2):
                y = w["y_base"] + int(w["amplitude"] * np.sin(
                    w["frequency"] * x + w["phase"] + t * 0.5))
                if 0 <= y < VH:
                    points.append((x, y))
            if len(points) > 1:
                draw.line(points, fill=(*wave_col, int(255 * w["alpha"])), width=w["width"])

        # ─ 5. 数字流（带发光效果）─
        for s in streams:
            y_pos = (s["y"] + t * s["speed"]) % (VH + 300) - 200
            alpha = int(255 * s["alpha"])
            glow_alpha = int(255 * s["glow"])

            for di, digit in enumerate(s["digits"]):
                dy = y_pos + di * 20
                if -20 < dy < VH + 20:
                    x = s["x"]
                    # 发光层（模糊效果通过多层叠加模拟）
                    try:
                        # 外发光
                        draw.text((x, int(dy)), digit, fill=(*glow_col, glow_alpha), font=font_num)
                        # 主文字
                        draw.text((x, int(dy)), digit, fill=(*num_col, alpha), font=font_num)
                    except Exception:
                        pass

        # ─ 6. 底部渐变光效 ─
        for i in range(50):
            alpha = int(255 * (1 - i / 50) * 0.3)
            y = VH - i - 1
            draw.rectangle([0, y, VW, y + 1], fill=(*glow_col, alpha))

        # ─ 7. 右侧垂直光带装饰 ─
        light_x = int(VW * 0.85)
        for i in range(VH):
            alpha = int(255 * 0.05 * np.sin(i / VH * np.pi + t) * 0.5)
            draw.point((light_x, i), fill=(*num_col, alpha))
        draw.line([(light_x, 0), (light_x, VH)], fill=(*num_col, 30), width=2)

        return np.array(img)[:, :, :3]


def _generate_minimax_video(
    prompt: str,
    page_num: int,
    pdf_name: str,
    temp_dir: str,
    duration: int = 6,
) -> Optional[str]:
    """
    调用 MiniMax Hailuo 文生视频 API 生成背景视频
    异步三步驟：提交任务 → 轮询状态 → 获取下载地址
    """
    if not LLM_API_KEY:
        print("    [警告] MiniMax 文生视频：LLM_API_KEY 未配置")
        return None

    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json",
    }

    # Step1: 提交文生视频任务
    payload = {
        "model": MINIMAX_VIDEO_MODEL,
        "prompt": prompt,
        "duration": 6 if duration <= 6 else 10,   # Hailuo-2.3 仅支持 6s 或 10s
        "resolution": "768P",
        "prompt_optimizer": True,
    }
    try:
        resp = requests.post(
            f"{MINIMAX_VIDEO_BASE_URL}/video_generation",
            json=payload,
            headers=headers,
            timeout=30,
        )
        if resp.status_code != 200:
            print(f"    [警告] MiniMax 文生视频提交失败: {resp.status_code} {resp.text[:200]}")
            return None

        data = resp.json()
        if data.get("base_resp", {}).get("status_code", 0) != 0:
            print(f"    [警告] MiniMax 文生视频错误: {data['base_resp'].get('status_msg')}")
            return None

        task_id = data.get("task_id")
        if not task_id:
            print("    [警告] MiniMax 文生视频：未返回 task_id")
            return None
        print(f"    MiniMax 文生视频任务已提交: task_id={task_id}")

    except Exception as e:
        print(f"    [警告] MiniMax 文生视频提交异常: {e}")
        return None

    # Step2: 轮询任务状态（最多等 5 分钟）
    file_id = None
    for attempt in range(60):
        time.sleep(5)
        try:
            r = requests.get(
                f"{MINIMAX_VIDEO_BASE_URL}/query/video_generation",
                params={"task_id": task_id},
                headers=headers,
                timeout=15,
            )
            qdata = r.json()
            status = qdata.get("status", "")
            if status == "Success":
                file_id = qdata.get("file_id")
                print(f"    MiniMax 文生视频生成成功，file_id={file_id}")
                break
            elif status == "Fail":
                print(f"    [警告] MiniMax 文生视频任务失败")
                return None
            # Preparing / Queueing / Processing 继续等待
            if attempt % 6 == 0:
                print(f"    MiniMax 文生视频生成中... ({attempt * 5}s, status={status})")
        except Exception:
            continue

    if not file_id:
        print("    [警告] MiniMax 文生视频超时")
        return None

    # Step3: 获取下载地址
    try:
        r = requests.get(
            f"{MINIMAX_VIDEO_BASE_URL}/files/retrieve",
            params={"file_id": file_id},
            headers=headers,
            timeout=15,
        )
        fdata = r.json()
        download_url = fdata.get("file", {}).get("download_url")
        if not download_url:
            print("    [警告] MiniMax 未返回 download_url")
            return None
    except Exception as e:
        print(f"    [警告] MiniMax 获取下载地址失败: {e}")
        return None

    # Step4: 下载 mp4 到本地
    save_dir = os.path.join(temp_dir, pdf_name, "bg_videos")
    os.makedirs(save_dir, exist_ok=True)
    save_path = os.path.join(save_dir, f"page_{page_num:03d}.mp4")
    try:
        r = requests.get(download_url, timeout=120)
        with open(save_path, "wb") as f:
            f.write(r.content)
        size_kb = os.path.getsize(save_path) / 1024
        print(f"    MiniMax 文生视频已保存: {save_path} ({size_kb:.0f} KB)")
        return save_path
    except Exception as e:
        print(f"    [警告] MiniMax 视频下载失败: {e}")
        return None


def generate_bg_video(
    prompt: str,
    page_num: int,
    pdf_name: str,
    temp_dir: str = "temp",
    duration: int = 6,
    screenshot_path: Optional[str] = None,
) -> Optional[str]:
    """
    生成背景视频。带缓存：已有则直接返回。

    优先级：
      1. MiniMax Hailuo 文生视频（MINIMAX_VIDEO_ENABLED=true 时）
      2. 本地 Ken Burns 动效（默认）

    Args:
        prompt:          描述文字（用于 MiniMax 提示词或本地色调判断）
        page_num:        页码（缓存文件名）
        pdf_name:        PDF 名称（缓存目录）
        temp_dir:        临时目录
        duration:        视频时长（秒）
        screenshot_path: PDF 截图路径（本地 Ken Burns 的背景图源）

    Returns:
        本地 mp4 文件路径，若失败返回 None
    """
    save_dir = os.path.join(temp_dir, pdf_name, "bg_videos")
    os.makedirs(save_dir, exist_ok=True)
    save_path = os.path.join(save_dir, f"page_{page_num:03d}.mp4")

    if os.path.exists(save_path) and os.path.getsize(save_path) > 1000:
        print(f"    背景视频已缓存，跳过生成: {save_path}")
        return save_path

    # 方式 1：MiniMax 文生视频
    if MINIMAX_VIDEO_ENABLED and LLM_API_KEY:
        print(f"    调用 MiniMax Hailuo 文生视频...")
        result = _generate_minimax_video(prompt, page_num, pdf_name, temp_dir, duration)
        if result:
            return result
        print(f"    MiniMax 文生视频失败，降级至本地 Ken Burns")

    # 方式 2：本地 Ken Burns 动效
    print(f"    本地生成背景视频（Ken Burns）...")

    try:
        # 1. 加载背景图（截图 or 渐变色兜底）
        base_img = _load_background_image(screenshot_path, prompt, page_num)

        # 2. 生成 Ken Burns 视频
        clip = _make_ken_burns_clip(base_img, duration, page_num, prompt)

        # 3. 导出 mp4
        clip.write_videofile(
            save_path,
            fps=BG_VIDEO_FPS,
            codec="libx264",
            preset="ultrafast",
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
