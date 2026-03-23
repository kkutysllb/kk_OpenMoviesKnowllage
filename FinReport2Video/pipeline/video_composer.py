"""
视频合成模块 v5 - 重构布局版

布局设计：
  背景：通义万象生成的5s动态视频循环
  左侧（40%）：页面关键信息卡片（标题 + 要点列表），静态展示
  右侧（52%）：图表从右侧滑入，多图轮播
  底部（字幕区）：逐字打字效果字幕，无背景框，当前字高亮
  最底部：进度条

片头页：
  全屏背景视频 + 标题 + 摘要 + 分析师/日期信息
"""
import os
import subprocess
import tempfile
from typing import List, Optional, Dict

import numpy as np
from PIL import Image, ImageFilter, ImageDraw, ImageFont
from moviepy import (
    VideoClip,
    VideoFileClip,
    AudioFileClip,
    CompositeVideoClip,
    concatenate_videoclips,
    vfx,
)
from config import VIDEO_SIZE, VIDEO_FPS

VW, VH = VIDEO_SIZE  # 1920 x 1080

# ── 布局常量 ──────────────────────────────────────────────────────────────────
PAD = 48

# 左侧信息卡片区
LEFT_W = int(VW * 0.40)   # 左侧宽度 = 768px
LEFT_PAD = 56

# 右侧图表区
RIGHT_X = int(VW * 0.46)  # 右侧起始 X = 883px
RIGHT_W = VW - RIGHT_X - PAD  # = 989px

# 底部字幕区
SUB_AREA_H = 140          # 字幕区高度
SUB_Y0 = VH - SUB_AREA_H - 30  # 字幕区起始Y
SUB_FONT_SIZE = 38        # 字幕字体大小
SUB_LINE_CHARS = 28       # 每行字幕字符数（中文）

# 颜色
COLOR_SUB_ACTIVE = (255, 225, 60)   # 当前字：亮黄
COLOR_SUB_DONE   = (255, 255, 255)  # 已读字：白
COLOR_SUB_FUTURE = (180, 190, 210)  # 未读字：浅灰蓝
COLOR_CARD_BG    = (10, 16, 45, 200)  # 左侧卡片背景（深蓝半透明）
COLOR_TITLE_BAR  = (200, 80, 15, 240) # 标题栏橙色
COLOR_ACCENT     = (255, 160, 30)     # 强调色

FONT_PATHS = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Helvetica.ttc",
]


def _get_font(size: int) -> ImageFont.FreeTypeFont:
    for fp in FONT_PATHS:
        try:
            return ImageFont.truetype(fp, size)
        except Exception:
            continue
    return ImageFont.load_default()


# ── 公开接口 ──────────────────────────────────────────────────────────────────

def compose_page_clip(
    bg_video_path: Optional[str],
    audio_path: str,
    image_paths: List[str],
    script_text: str,
    page_num: int,
    page_title: str = "",
    key_points: Optional[List[str]] = None,
    word_timestamps: Optional[List[Dict]] = None,
    screenshot_path: Optional[str] = None,  # PDF第一页截图，显示在左侧上方
) -> CompositeVideoClip:
    audio = AudioFileClip(audio_path)
    duration = audio.duration
    layers = []

    # 1. 背景视频循环
    if bg_video_path and os.path.exists(bg_video_path):
        layers.append(_make_bg_from_video(bg_video_path, duration))
    else:
        layers.append(_make_fallback_bg(duration))

    # 2/3/5. 静态层合并：标题栏 + 左侧信息卡 + 底部字幕轻遇罩合并为一张 RGBA 底图
    # 避免 3 个独立层在合成时逐帧做 alpha 混合
    title_bar_arr = _make_title_bar(page_title)
    card_arr      = _make_info_card(page_num, page_title, key_points or [], screenshot_path)
    static_base   = Image.new("RGBA", (VW, VH), (0, 0, 0, 0))
    # 底部字幕轻遇罩
    sub_overlay_img = Image.new("RGBA", (VW, VH), (0, 0, 0, 0))
    sub_overlay_img.paste((0, 0, 0, 55),
                          box=(0, SUB_Y0 - 20, VW, VH))
    static_base = Image.alpha_composite(static_base, sub_overlay_img)
    # 标题栏和信息卡叠加
    static_base = Image.alpha_composite(static_base, Image.fromarray(title_bar_arr))
    static_base = Image.alpha_composite(static_base, Image.fromarray(card_arr))
    static_arr  = np.array(static_base)
    layers.append(VideoClip(lambda t, a=static_arr: a, duration=duration, is_mask=False))

    # 4. 右侧图表（滑入 + 轮播）
    if image_paths:
        chart_clip = _make_chart_clip(image_paths, duration)
        if chart_clip:
            layers.append(chart_clip)

    # 6. 底部打字字幕（核心动效）
    sub_clip = _make_subtitle_clip(script_text, duration, word_timestamps or [])
    layers.append(sub_clip)

    # 7. 进度条
    layers.append(_make_progress_bar(duration))

    video = CompositeVideoClip(layers, size=VIDEO_SIZE)
    video = video.with_audio(audio)
    return video


def compose_intro_clip(
    bg_video_path: Optional[str],
    report_title: str,
    report_abstract: str,
    analyst: str,
    date: str,
    total_pages: int,
    duration: float = 8.0,
    audio_path: Optional[str] = None,
    data_source: str = "",
) -> CompositeVideoClip:
    """
    生成片头页：报告标题 + 摘要 + 分析师 + 日期 + 数据源，可选音频
    """
    layers = []

    # 1. 背景视频
    if bg_video_path and os.path.exists(bg_video_path):
        layers.append(_make_bg_from_video(bg_video_path, duration))
    else:
        layers.append(_make_fallback_bg(duration))

    # 2. 全屏深色遮罩（降低透明度，让背景视频可见）
    overlay = np.zeros((VH, VW, 4), dtype=np.uint8)
    overlay[:, :] = [5, 10, 30, 130]
    layers.append(VideoClip(lambda t, a=overlay: a, duration=duration, is_mask=False))

    # 3. 片头内容（带渐入动画）
    intro_clip = _make_intro_content(
        report_title, report_abstract, analyst, date, total_pages, duration, data_source
    )
    layers.append(intro_clip)

    # 4. 底部分割线（品牌感）
    bar_arr = np.zeros((VH, VW, 4), dtype=np.uint8)
    bar_arr[VH-6:VH, :] = [*COLOR_ACCENT, 230]
    layers.append(VideoClip(lambda t, a=bar_arr: a, duration=duration, is_mask=False))

    video = CompositeVideoClip(layers, size=VIDEO_SIZE)

    # 绑定音频（若有）
    if audio_path and os.path.exists(audio_path):
        audio = AudioFileClip(audio_path)
        # 音频时长如果超过视频，裁剪；如果短于视频，保持视频时长
        if audio.duration < duration:
            video = video.with_audio(audio)
        else:
            video = video.with_audio(audio.subclipped(0, duration))

    return video


def _concatenate_with_ffmpeg(clips: list, output_path: str) -> str:
    """
    使用 FFmpeg 直接拼接视频片段，不重新编码。
    要求所有片段格式完全一致（分辨率、编码、帧率）。
    返回输出文件路径。
    """
    # 创建临时目录存储片段文件列表
    with tempfile.TemporaryDirectory() as tmpdir:
        # 保存所有片段为临时文件
        clip_paths = []
        for i, clip in enumerate(clips):
            clip_path = os.path.join(tmpdir, f"clip_{i:03d}.mp4")
            # 如果 clip 已经是文件路径，直接使用
            if isinstance(clip, str) and os.path.exists(clip):
                clip_paths.append(clip)
            else:
                # 否则写入临时文件
                clip.write_videofile(
                    clip_path,
                    fps=VIDEO_FPS,
                    codec="libx264",
                    audio_codec="aac",
                    preset="ultrafast",
                    logger=None,
                )
                clip_paths.append(clip_path)

        # 创建 FFmpeg concat 文件列表
        list_file = os.path.join(tmpdir, "filelist.txt")
        with open(list_file, "w", encoding="utf-8") as f:
            for path in clip_paths:
                # 转义单引号
                escaped = path.replace("'", "'\\''")
                f.write(f"file '{escaped}'\n")

        # 调用 FFmpeg 拼接（不重新编码）
        cmd = [
            "ffmpeg",
            "-y",  # 覆盖输出
            "-f", "concat",
            "-safe", "0",
            "-i", list_file,
            "-c", "copy",  # 复制流，不重新编码
            "-movflags", "+faststart",  # 优化网络播放
            output_path,
        ]

        print(f"  FFmpeg 拼接 {len(clips)} 个片段...")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg 拼接失败: {result.stderr}")

    return output_path


def compose_final_video(clips: list, output_path: str, crossfade: float = 0.5):
    """
    合并所有片段。
    默认使用 FFmpeg 直接拼接（最快），不支持转场。
    如需转场效果，设置 crossfade > 0 使用 moviepy 渲染。
    """
    print(f"\n合并 {len(clips)} 个片段，输出到: {output_path}")

    # 如果不需要转场，使用 FFmpeg 快速拼接
    if crossfade <= 0 or len(clips) <= 1:
        try:
            _concatenate_with_ffmpeg(clips, output_path)
            print(f"视频已保存: {output_path}")
            return
        except Exception as e:
            print(f"  FFmpeg 拼接失败，回退到 moviepy: {e}")

    # 使用 moviepy 渲染（支持转场，但更慢）
    if len(clips) <= 1:
        final = concatenate_videoclips(clips, method="compose")
    else:
        faded = [clips[0].with_effects([vfx.CrossFadeOut(crossfade)])]
        for clip in clips[1:-1]:
            faded.append(
                clip.with_effects([vfx.CrossFadeIn(crossfade), vfx.CrossFadeOut(crossfade)])
            )
        if len(clips) > 1:
            faded.append(clips[-1].with_effects([vfx.CrossFadeIn(crossfade)]))
        final = concatenate_videoclips(faded, method="compose", padding=-crossfade)

    final.write_videofile(
        output_path,
        fps=VIDEO_FPS,
        codec="libx264",
        audio_codec="aac",
        temp_audiofile=output_path + ".temp_audio.m4a",
        remove_temp=True,
        threads=4,
        preset="ultrafast",
        logger=None,
    )
    print(f"视频已保存: {output_path}")


# ── 背景 ─────────────────────────────────────────────────────────────────────

def _make_bg_from_video(bg_video_path: str, duration: float) -> VideoClip:
    """通义万象5s视频循环拉伸至 duration 秒"""
    clip = VideoFileClip(bg_video_path).without_audio()
    if clip.duration <= 0:
        clip.close()
        return _make_fallback_bg(duration)
    loops = int(duration / clip.duration) + 2
    looped = concatenate_videoclips([clip] * loops)
    result = looped.subclipped(0, duration)
    if list(result.size) != list(VIDEO_SIZE):
        result = result.resized(VIDEO_SIZE)
    return result


def _make_fallback_bg(duration: float) -> VideoClip:
    """备用背景：深藏蓝渐变"""
    def make_frame(t):
        p = t / duration if duration > 0 else 0
        canvas = np.zeros((VH, VW, 3), dtype=np.uint8)
        canvas[:, :] = [int(5 + p * 8), int(10 + p * 12), int(35 + p * 15)]
        return canvas
    return VideoClip(make_frame, duration=duration)


# ── 左侧信息卡片（静态）────────────────────────────────────────────────────────

# ── 顶部章节标题栏 ───────────────────────────────────────────────────────────

TITLE_BAR_H = 72   # 顶部标题栏高度
TITLE_BAR_Y = 12   # 距顶部间距


def _make_title_bar(title: str) -> np.ndarray:
    """
    顶部居中章节标题（融合风格）：
    - 轻微半透明深色背景条
    - 标题文字居中，白色 + 描边 + 阴影
    """
    canvas = Image.new("RGBA", (VW, VH), (0, 0, 0, 0))
    if not title:
        return np.array(canvas)
    draw = ImageDraw.Draw(canvas)

    font_title = _get_font(38)
    title_display = title[:32] + ("…" if len(title) > 32 else "")

    # 计算文字宽度
    try:
        bbox = draw.textbbox((0, 0), title_display, font=font_title)
        tw = bbox[2] - bbox[0]
    except Exception:
        tw = len(title_display) * 38

    bar_w = tw + 80
    bar_x = (VW - bar_w) // 2
    bar_y = TITLE_BAR_Y

    # 半透明深色背景胶囊
    draw.rounded_rectangle(
        [bar_x, bar_y, bar_x + bar_w, bar_y + TITLE_BAR_H],
        radius=14, fill=(8, 12, 40, 185)
    )
    # 左侧橙色强调边
    draw.rounded_rectangle(
        [bar_x, bar_y, bar_x + 6, bar_y + TITLE_BAR_H],
        radius=4, fill=(*COLOR_ACCENT[:3], 240)
    )

    tx = (VW - tw) // 2
    ty = bar_y + (TITLE_BAR_H - 38) // 2

    # 描边
    for dx, dy in [(-2, 0), (2, 0), (0, -2), (0, 2)]:
        draw.text((tx + dx, ty + dy), title_display,
                  fill=(0, 0, 0, 180), font=font_title)
    # 正文
    draw.text((tx, ty), title_display,
              fill=(255, 248, 220, 255), font=font_title)

    return np.array(canvas)


def _make_info_card(page_num: int, title: str, key_points: List[str],
                   screenshot_path: Optional[str] = None) -> np.ndarray:
    """
    左侧信息展示（融合风格）：
    - 上方：PDF截图缩略图（表格/图像），带轻微圆角卡片边框
    - 下方：要点列表，白色 + 阴影，前缀橙色圆点
    - 无页码标签
    """
    canvas = Image.new("RGBA", (VW, VH), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    card_x0 = LEFT_PAD - 10
    # 顶部标题栏占掉 TITLE_BAR_H + TITLE_BAR_Y + 间距
    y = TITLE_BAR_Y + TITLE_BAR_H + 20

    # ── 左侧上方：PDF截图缩略图 ──────────────────────────────
    THUMB_MAX_W = LEFT_W - card_x0 - 20
    THUMB_MAX_H = 280   # 缩略图最大高度

    if screenshot_path and os.path.exists(screenshot_path):
        try:
            thumb = Image.open(screenshot_path).convert("RGBA")
            thumb.thumbnail((THUMB_MAX_W, THUMB_MAX_H), Image.LANCZOS)
            tw, th = thumb.size
            tx = card_x0 + (THUMB_MAX_W - tw) // 2

            # 轻微半透明白色背景卡片（不遮挡视频，仅提升图片可读性）
            pad = 8
            draw.rounded_rectangle(
                [tx - pad, y - pad, tx + tw + pad, y + th + pad],
                radius=10, fill=(255, 255, 255, 30)
            )
            # 橙色细边框
            draw.rounded_rectangle(
                [tx - pad, y - pad, tx + tw + pad, y + th + pad],
                radius=10, outline=(*COLOR_ACCENT[:3], 160), width=2
            )
            canvas.paste(thumb, (tx, y), thumb)

            # 「重点」标签
            lbl_font = _get_font(18)
            draw.rounded_rectangle(
                [tx, y + th - 28, tx + 64, y + th],
                radius=6, fill=(200, 60, 10, 210)
            )
            draw.text((tx + 8, y + th - 24), "重 点",
                      fill=(255, 255, 255, 255), font=lbl_font)

            y += th + pad * 2 + 18
        except Exception as e:
            print(f"    [警告] 截图缩略图加载失败: {e}")

    # ── 左侧橙色竖线 ─────────────────────────────────────────
    line_x = card_x0 + 14
    line_y0 = y + 4
    line_y1 = min(y + len(key_points[:6]) * 50 + 20, SUB_Y0 - 30)
    draw.line([(line_x, line_y0), (line_x, line_y1)],
              fill=(*COLOR_ACCENT[:3], 180), width=3)

    # ── 关键信息要点（融合风格：白色 + 阴影）─────────────────
    font_point = _get_font(28)
    for pt in key_points[:6]:
        if y + 46 > SUB_Y0 - 20:
            break
        pt_text = pt[:22] + ("…" if len(pt) > 22 else "")
        # 阴影
        for dx, dy in [(2, 2), (-1, 1)]:
            draw.text((card_x0 + 30 + dx, y + dy), pt_text,
                      fill=(0, 0, 0, 150), font=font_point)
        # 描边
        for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            draw.text((card_x0 + 30 + dx, y + dy), pt_text,
                      fill=(10, 20, 50, 160), font=font_point)
        # 橙色圆点
        draw.ellipse([card_x0 + 20, y + 10, card_x0 + 28, y + 18],
                     fill=(*COLOR_ACCENT[:3], 230))
        # 正文
        draw.text((card_x0 + 30, y), pt_text,
                  fill=(240, 248, 255, 245), font=font_point)
        y += 46

    return np.array(canvas)


# ── 底部打字字幕 ────────────────────────────────────────────────────────────────

def _build_subtitle_lines(words_ts: List[Dict], script_text: str, duration: float):
    """
    将时间戳词列表排列为字幕行。
    LLM 已将断行位置用 \\n 标记，直接按 \\n token 分组即可。
    如果无时间戳（skip_llm 模式），则按字符均分配时间，仍以 \\n 断行。
    返回 lines: List[List[Dict]]  每个 Dict = {word, start, end}
    """
    if words_ts:
        raw = words_ts
    else:
        chars = [c for c in script_text if c.strip()]
        t_per = duration / max(len(chars), 1)
        raw = [{"word": c, "start": i * t_per, "end": (i + 1) * t_per}
               for i, c in enumerate(chars)]

    lines: List[List[Dict]] = []
    cur_line: List[Dict] = []

    for w in raw:
        if w["word"] == "\n":
            # 遇到 LLM 嵌入的换行符 → 切分一行
            if cur_line:
                lines.append(cur_line)
                cur_line = []
        else:
            cur_line.append(w)

    if cur_line:
        lines.append(cur_line)

    # 安全兔採：如果 LLM 没有输出任何 \\n，按 SUB_LINE_CHARS 硬截断
    if len(lines) <= 1 and sum(len(w["word"]) for w in raw if w["word"] != "\n") > SUB_LINE_CHARS:
        lines = []
        cur_line = []
        cur_len = 0
        for w in raw:
            if w["word"] == "\n":
                continue
            cur_line.append(w)
            cur_len += len(w["word"])
            if cur_len >= SUB_LINE_CHARS:
                lines.append(cur_line)
                cur_line = []
                cur_len = 0
        if cur_line:
            lines.append(cur_line)

    return lines


def _make_subtitle_clip(script_text: str, duration: float, words_ts: List[Dict]) -> VideoClip:
    """
    底部打字字幕：
    - 当前行居中显示，当前字高亮（亮黄色）
    - 上一行淡出，作为已读记录
    - 无背景框，纯文字 + 阴影
    """
    font = _get_font(SUB_FONT_SIZE)
    font_prev = _get_font(SUB_FONT_SIZE - 6)   # 上一行字体，提前缓存，避免每帧调用

    lines = _build_subtitle_lines(words_ts, script_text, duration)
    if not lines:
        return VideoClip(lambda t: np.zeros((VH, VW, 4), dtype=np.uint8),
                         duration=duration, is_mask=False)

    # 预计算每行每个字的宽度（字和字体不变，无需每帧重算）
    # 需要临时 draw 对象来测量文字宽度
    _tmp_img = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    _tmp_draw = ImageDraw.Draw(_tmp_img)

    def _calc_widths(line_words, fnt):
        ws = []
        for w in line_words:
            try:
                bbox = _tmp_draw.textbbox((0, 0), w["word"], font=fnt)
                ws.append(bbox[2] - bbox[0] + 4)
            except Exception:
                ws.append(SUB_FONT_SIZE + 4)
        return ws

    line_widths      = [_calc_widths(l, font)      for l in lines]   # 当前行字宽
    line_widths_prev = [_calc_widths(l, font_prev) for l in lines]   # 上一行字宽

    # 预计算每行的时间范围
    def line_range(line):
        if not line:
            return 0, duration
        return line[0]["start"], line[-1]["end"]

    line_ranges = [line_range(l) for l in lines]

    def make_frame(t):
        canvas = Image.new("RGBA", (VW, VH), (0, 0, 0, 0))
        draw = ImageDraw.Draw(canvas)

        # 找当前行
        cur_idx = 0
        for li, (ls, le) in enumerate(line_ranges):
            if t >= ls:
                cur_idx = li

        # 渲染当前行（居中）
        _draw_subtitle_line(draw, font, lines[cur_idx], t,
                            y=SUB_Y0 + 55, center=True, show_all=True,
                            precomputed_widths=line_widths[cur_idx])

        # 渲染上一行（已读，白色，稍小，稍暗）
        if cur_idx > 0:
            prev_line = lines[cur_idx - 1]
            prev_fade = max(0.0, 1.0 - (t - line_ranges[cur_idx - 1][1]) / 0.8)
            if prev_fade > 0.05:
                _draw_subtitle_line(draw, font_prev, prev_line, t,
                                    y=SUB_Y0 + 8, center=True,
                                    show_all=True, alpha_scale=prev_fade * 0.6,
                                    force_color=COLOR_SUB_DONE,
                                    precomputed_widths=line_widths_prev[cur_idx - 1])

        return np.array(canvas)

    return VideoClip(make_frame, duration=duration, is_mask=False)


def _draw_subtitle_line(draw, font, line_words, t, y, center=True,
                        show_all=False, alpha_scale=1.0, force_color=None,
                        precomputed_widths=None):
    """渲染一行字幕到 draw 对象；precomputed_widths 传入可跳过重算"""
    if not line_words:
        return

    # 使用预计算字宽，或实时计算
    if precomputed_widths and len(precomputed_widths) == len(line_words):
        widths = precomputed_widths
    else:
        widths = []
        for w in line_words:
            try:
                bbox = draw.textbbox((0, 0), w["word"], font=font)
                widths.append(bbox[2] - bbox[0] + 4)
            except Exception:
                widths.append(SUB_FONT_SIZE + 4)

    total_w = sum(widths)
    x = (VW - total_w) // 2 if center else PAD

    for i, (w, ww) in enumerate(zip(line_words, widths)):
        if force_color:
            color = force_color
        elif t >= w["end"]:
            color = COLOR_SUB_DONE
        elif t >= w["start"]:
            color = COLOR_SUB_ACTIVE
        else:
            color = COLOR_SUB_FUTURE if show_all else None

        if color is None:
            x += ww
            continue

        alpha = int(255 * alpha_scale)

        # 阴影（提升可读性）
        for dx, dy in [(2, 2), (-1, 1)]:
            draw.text((x + dx, y + dy), w["word"], fill=(0, 0, 0, int(alpha * 0.7)), font=font)

        # 当前字发光效果
        if color == COLOR_SUB_ACTIVE:
            for r in [3, 2]:
                ga = int(alpha * 0.25)
                draw.text((x - r, y), w["word"], fill=(*COLOR_ACCENT[:3], ga), font=font)
                draw.text((x + r, y), w["word"], fill=(*COLOR_ACCENT[:3], ga), font=font)
            draw.text((x, y), w["word"], fill=(*color, alpha), font=font)
        else:
            draw.text((x, y), w["word"], fill=(*color, alpha), font=font)

        x += ww


# ── 右侧图表（滑入 + 轮播）────────────────────────────────────────────────────

def _make_chart_clip(image_paths: List[str], duration: float) -> Optional[VideoClip]:
    try:
        charts = []
        for path in image_paths:
            img = Image.open(path).convert("RGBA")
            # 图表区高度要避开底部字幕区
            max_h = SUB_Y0 - PAD * 2 - 20
            img.thumbnail((RIGHT_W - 20, max_h), Image.LANCZOS)
            img = _card_shadow(img)
            charts.append(np.array(img))

        n = len(charts)
        time_per = duration / n
        SLIDE_IN = 0.6
        FADE_DUR = 0.4

        def make_frame(t):
            canvas = np.zeros((VH, VW, 4), dtype=np.uint8)
            idx = min(int(t / time_per), n - 1)
            img_arr = charts[idx]
            ih, iw = img_arr.shape[:2]
            x_final = RIGHT_X + (RIGHT_W - iw) // 2
            y = (SUB_Y0 - ih) // 2 + PAD // 2

            if t < SLIDE_IN:
                progress = 1 - (1 - t / SLIDE_IN) ** 2
                x = int(VW + (x_final - VW) * progress)
            else:
                x = x_final

            t_in_seg = t - idx * time_per
            alpha_scale = min(1.0, t_in_seg / FADE_DUR) if idx > 0 else 1.0

            x0 = max(0, x); y0 = max(0, y)
            x1 = min(VW, x + iw); y1 = min(VH, y + ih)
            w = x1 - x0; h = y1 - y0
            if w > 0 and h > 0:
                src = img_arr[:h, (x0 - x):(x0 - x + w)].copy().astype(np.float32)
                src[:, :, 3] *= alpha_scale
                canvas[y0:y1, x0:x1] = src.astype(np.uint8)
            return canvas

        return VideoClip(make_frame, duration=duration, is_mask=False)
    except Exception as e:
        print(f"    [警告] 图表层失败: {e}")
        return None


def _card_shadow(img: Image.Image, radius: int = 16, border: int = 10) -> Image.Image:
    iw, ih = img.size
    bw, bh = iw + border * 2, ih + border * 2
    result = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    # 阴影
    shadow = Image.new("RGBA", (bw, bh), (0, 0, 0, 100))
    smask = Image.new("L", (bw, bh), 0)
    ImageDraw.Draw(smask).rounded_rectangle([4, 4, bw - 1, bh - 1], radius=radius, fill=200)
    shadow.putalpha(smask)
    result = Image.alpha_composite(result, shadow)
    # 白色卡片
    card = Image.new("RGBA", (bw, bh), (255, 255, 255, 245))
    cmask = Image.new("L", (bw, bh), 0)
    ImageDraw.Draw(cmask).rounded_rectangle([0, 0, bw - 3, bh - 3], radius=radius, fill=255)
    card.putalpha(cmask)
    result = Image.alpha_composite(result, card)
    result.paste(img, (border, border), img if img.mode == "RGBA" else None)
    return result


# ── 底部进度条 ────────────────────────────────────────────────────────────────

def _make_progress_bar(duration: float) -> VideoClip:
    BAR_H = 4
    BAR_Y = VH - 8

    def make_frame(t):
        canvas = np.zeros((VH, VW, 4), dtype=np.uint8)
        p = t / duration if duration > 0 else 0
        canvas[BAR_Y:BAR_Y + BAR_H, PAD:VW - PAD] = [40, 45, 70, 80]
        fw = int((VW - PAD * 2) * p)
        if fw > 2:
            canvas[BAR_Y:BAR_Y + BAR_H, PAD:PAD + fw] = [*COLOR_ACCENT[:3], 220]
            canvas[BAR_Y - 2:BAR_Y + BAR_H + 2, PAD + fw - 4:PAD + fw + 4] = [255, 220, 100, 255]
        return canvas

    return VideoClip(make_frame, duration=duration, is_mask=False)


# ── 片头页内容（淡入动画）─────────────────────────────────────────────────────

def _make_intro_content(title, abstract, analyst, date, total_pages, duration, data_source="") -> VideoClip:
    """片头内容：标题大字 + 分割线 + 摘要 + 底部信息栏，带淡入动画。
    预计算所有静态文字宽度，避免每帧重复调用 textbbox。"""

    font_title = _get_font(72)
    font_sub   = _get_font(34)
    font_info  = _get_font(28)
    font_badge = _get_font(24)

    FADE_IN = 1.2   # 淡入时长

    # ── 预计算所有静态内容宽度 ─────────────────────────────────────
    _tmp = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    _td  = ImageDraw.Draw(_tmp)

    def _tw(text, font, fallback_size):
        try:
            b = _td.textbbox((0, 0), text, font=font)
            return b[2] - b[0]
        except Exception:
            return len(text) * fallback_size

    badge_text    = "金融报告深度解读"
    badge_x       = VW // 2 - 130   # 固定位置

    title_display = title if title else "金融分析报告"
    if len(title_display) > 18:
        mid    = len(title_display) // 2
        lines  = [title_display[:mid], title_display[mid:]]
    else:
        lines  = [title_display]
    line_tws = [_tw(l, font_title, 72) for l in lines]
    line_txs = [(VW - tw) // 2 for tw in line_tws]

    abs_text = (abstract[:60] + "…" if abstract and len(abstract) > 60 else abstract or "")
    abs_tw   = _tw(abs_text, font_sub, 34) if abs_text else 0
    abs_tx   = (VW - abs_tw) // 2

    info_parts = []
    if analyst:     info_parts.append(f"分析师：{analyst}")
    if date:        info_parts.append(f"发布日期：{date}")
    if total_pages: info_parts.append(f"共 {total_pages} 页")
    info_str  = "    |    ".join(info_parts) if info_parts else "金融研究报告"
    info_tw   = _tw(info_str, font_info, 28)
    info_tx   = (VW - info_tw) // 2
    info_y    = VH - 160

    ds_text = f"数据来源：{data_source[:50]}" if data_source else ""
    ds_tw   = _tw(ds_text, font_badge, 24) if ds_text else 0
    ds_tx   = (VW - ds_tw) // 2

    # y_title 起始 Y坐标（居中标题占 88px 每行）
    title_y_start = 180
    line_y = title_y_start + len(lines) * 88 + 10   # 分割线 Y

    def make_frame(t):
        canvas = Image.new("RGBA", (VW, VH), (0, 0, 0, 0))
        draw = ImageDraw.Draw(canvas)

        fade  = min(1.0, t / FADE_IN)
        alpha = int(255 * fade)

        # ── 顶部品牌标签 ──
        draw.rounded_rectangle([VW // 2 - 180, 80, VW // 2 + 180, 130],
                                radius=8, fill=(200, 80, 15, int(alpha * 0.9)))
        draw.text((badge_x, 88), badge_text, fill=(255, 255, 255, alpha), font=font_badge)

        # ── 主标题 ──
        y_title = title_y_start
        for txt_line, tw, tx in zip(lines, line_tws, line_txs):
            for dx, dy in [(-2,-2),(2,-2),(-2,2),(2,2)]:
                draw.text((tx+dx, y_title+dy), txt_line,
                          fill=(0, 0, 0, int(alpha * 0.6)), font=font_title)
            draw.text((tx, y_title), txt_line,
                      fill=(255, 245, 220, alpha), font=font_title)
            y_title += 88

        # ── 橙色分割线 ──
        draw.line([(VW // 2 - 300, line_y), (VW // 2 + 300, line_y)],
                  fill=(*COLOR_ACCENT[:3], alpha), width=3)

        # ── 摘要文字 ──
        if abs_text:
            draw.text((abs_tx, line_y + 30), abs_text,
                      fill=(200, 215, 240, int(alpha * 0.9)), font=font_sub)

        # ── 底部信息栏 ──
        draw.rounded_rectangle([VW // 2 - 500, info_y - 10,
                                 VW // 2 + 500, info_y + 110],
                                radius=12, fill=(20, 30, 70, int(alpha * 0.8)))
        draw.text((info_tx, info_y + 10), info_str,
                  fill=(200, 210, 240, int(alpha * 0.95)), font=font_info)

        if ds_text:
            draw.text((ds_tx, info_y + 55), ds_text,
                      fill=(160, 180, 220, int(alpha * 0.8)), font=font_badge)

        return np.array(canvas)

    return VideoClip(make_frame, duration=duration, is_mask=False)
