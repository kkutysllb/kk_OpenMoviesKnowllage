"""
FinReport2Video 主入口
金融报告 PDF → 带语音讲解的 MP4 视频

用法:
    python main.py --input report.pdf
    python main.py --input report.pdf --output output/my_video.mp4
    python main.py --input report.pdf --skip-llm --voice zh-CN-YunyangNeural
    python main.py --input report.pdf --pages 1-5
    python main.py --list-voices
"""
import os
import sys
import argparse
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import OUTPUT_DIR, TEMP_DIR
from pipeline.pdf_parser import parse_pdf, parse_pdf_by_sections, extract_report_meta
from pipeline.script_writer import write_script, extract_key_points
from pipeline.image_fetcher import get_images_for_page
from pipeline.tts_generator import generate_audio, get_available_voices, load_word_timestamps, _get_audio_duration as _get_tts_duration
from pipeline.video_generator import generate_bg_video, generate_intro_bg_video
from pipeline.prompt_builder import build_video_prompt
from pipeline.video_composer import compose_page_clip, compose_intro_clip, compose_final_video
from pipeline.markdown_parser import parse_markdown, convert_to_page_data

# 并发度：同时处理的页数（网络 API 并发，不占额外 CPU）
MAX_CONCURRENT_PAGES = 4

# 是否跳过默认背景文件、强制调用 MiniMax 文生视频
MINIMAX_VIDEO_ENABLED = os.getenv("MINIMAX_VIDEO_ENABLED", "false").lower() == "true"


def _process_pdf(pdf_path: str, pdf_name: str, args) -> list:
    """处理PDF文件，返回视频片段列表"""
    from pipeline.pdf_parser import extract_report_meta
    from pipeline.video_generator import generate_intro_bg_video
    from config import get_default_bg_video
    
    # ── Step 1: 解析 PDF ───────────────────────────────────────────────────────
    print("Step 1/5  解析 PDF（按大标题分章节）...")
    pages_data = parse_pdf_by_sections(pdf_path, pages=args.pages)
    print(f"  共解析 {len(pages_data)} 页\n")

    # ── Step 1.5: 提取报告元信息，生成片头页 ──────────────────────────────────
    print("Step 1.5/5  生成片头页...")
    report_meta = extract_report_meta(pdf_path)
    # 若未能提取到标题，用文件名兜底
    if not report_meta.title:
        report_meta.title = pdf_name
    report_meta.total_pages = len(pages_data)

    # 使用统一背景视频（与Markdown一致）
    intro_bg_path = get_default_bg_video()
    if not intro_bg_path:
        # 如果没有默认背景，生成一个
        intro_bg_path = generate_intro_bg_video(
            pdf_name=pdf_name,
            duration=15.0,
        )

    # 片头页旁白（播报报告标题和摘要）
    # 在关键词之间加停顿逗号，让 TTS 读得更流畅
    intro_narration = f"以下是，{report_meta.title}，"
    if report_meta.abstract:
        intro_narration += report_meta.abstract[:60]
    if report_meta.analyst:
        intro_narration += f"。分析师：{report_meta.analyst}"
    if report_meta.date:
        intro_narration += f"。发布时间：{report_meta.date}"
    intro_narration += f"。共{report_meta.total_pages}个章节。"

    intro_audio_path = generate_audio(
        text=intro_narration,
        page_num=0,
        pdf_name=pdf_name,
    )
    # 片头时长取音频时长（最少 6s，不设上限——充分播完旁白）
    intro_duration = max(6.0, _get_tts_duration(intro_audio_path))

    intro_clip = compose_intro_clip(
        bg_video_path=intro_bg_path,
        report_title=report_meta.title,
        report_abstract=report_meta.abstract,
        analyst=report_meta.analyst,
        date=report_meta.date,
        total_pages=report_meta.total_pages,
        duration=intro_duration,
        audio_path=intro_audio_path,
        data_source=report_meta.data_source,
    )
    print(f"  片头页完成（{report_meta.title[:30]}...）\n")

    # ── Step 2: 逐页处理 ───────────────────────────────────────────────────────
    print(f"Step 2/5  并发处理 {len(pages_data)} 页（最多 {MAX_CONCURRENT_PAGES} 页同时）...")
    t2_start = time.time()

    clips_map: dict = {}
    futures_map = {}

    with ThreadPoolExecutor(
        max_workers=MAX_CONCURRENT_PAGES,
        thread_name_prefix="page",
    ) as executor:
        for page in pages_data:
            fut = executor.submit(
                _process_page,
                page,
                pdf_name,
                args.skip_llm,
                args.no_ai_image,
                args.voice,
            )
            futures_map[fut] = page.page_num

        for fut in as_completed(futures_map):
            pn = futures_map[fut]
            try:
                page_num, clip = fut.result()
                clips_map[page_num] = clip
                elapsed_so_far = time.time() - t2_start
                done = len(clips_map)
                total = len(pages_data)
                print(f"  进度: {done}/{total} 页完成 ({elapsed_so_far:.0f}s 已耗)", flush=True)
            except Exception as e:
                print(f"  [错误] 第 {pn} 页处理失败: {e}")

    # 按原始页面顺序拼接 clips
    page_clips = [intro_clip]
    for page in pages_data:
        clip = clips_map.get(page.page_num)
        if clip is not None:
            page_clips.append(clip)
        else:
            print(f"  [警告] 第 {page.page_num} 页处理失败，跳过")

    t2_elapsed = time.time() - t2_start
    print(f"  Step 2 完成，共耗时 {t2_elapsed:.0f}s\n")
    
    return page_clips


def _process_markdown(md_path: str, md_name: str, args) -> list:
    """处理Markdown文件，返回视频片段列表"""
    from config import get_default_bg_video
    
    # ── Step 1: 解析 Markdown ───────────────────────────────────────────────────
    print("Step 1/4  解析 Markdown（按标题分章节）...")
    from config import TEMP_DIR
    sections = parse_markdown(md_path)
    pages_data = convert_to_page_data(sections, TEMP_DIR)
    print(f"  共解析 {len(pages_data)} 个章节\n")

    # ── Step 1.5: 生成片头页 ───────────────────────────────────────────────────
    print("Step 1.5/4  生成片头页...")
    report_title = sections[0].title if sections else md_name
    total_sections = len(pages_data)

    # 使用统一背景视频
    intro_bg_path = get_default_bg_video()
    if not intro_bg_path:
        # 如果没有默认背景，生成一个
        intro_bg_path = generate_intro_bg_video(
            pdf_name=md_name,
            duration=15.0,
        )

    # 片头页旁白
    intro_narration = f"以下是，{report_title}，共{total_sections}个章节。"

    intro_audio_path = generate_audio(
        text=intro_narration,
        page_num=0,
        pdf_name=md_name,
    )
    intro_duration = max(6.0, _get_tts_duration(intro_audio_path))

    intro_clip = compose_intro_clip(
        bg_video_path=intro_bg_path,
        report_title=report_title,
        report_abstract="",
        analyst="",
        date="",
        total_pages=total_sections,
        duration=intro_duration,
        audio_path=intro_audio_path,
        data_source="",
    )
    print(f"  片头页完成（{report_title[:30]}...）\n")

    # ── Step 2: 逐章节处理 ─────────────────────────────────────────────────────
    print(f"Step 2/4  并发处理 {len(pages_data)} 个章节（最多 {MAX_CONCURRENT_PAGES} 章同时）...")
    t2_start = time.time()

    clips_map: dict = {}
    futures_map = {}

    with ThreadPoolExecutor(
        max_workers=MAX_CONCURRENT_PAGES,
        thread_name_prefix="md",
    ) as executor:
        for page in pages_data:
            fut = executor.submit(
                _process_markdown_section,
                page,
                md_name,
                args.skip_llm,
                args.voice,
            )
            futures_map[fut] = page.page_num

        for fut in as_completed(futures_map):
            pn = futures_map[fut]
            try:
                page_num, clip = fut.result()
                clips_map[page_num] = clip
                elapsed_so_far = time.time() - t2_start
                done = len(clips_map)
                total = len(pages_data)
                print(f"  进度: {done}/{total} 章节完成 ({elapsed_so_far:.0f}s 已耗)", flush=True)
            except Exception as e:
                print(f"  [错误] 第 {pn} 章节处理失败: {e}")

    # 按原始顺序拼接 clips
    page_clips = [intro_clip]
    for page in pages_data:
        clip = clips_map.get(page.page_num)
        if clip is not None:
            page_clips.append(clip)
        else:
            print(f"  [警告] 第 {page.page_num} 章节处理失败，跳过")

    t2_elapsed = time.time() - t2_start
    print(f"  Step 2 完成，共耗时 {t2_elapsed:.0f}s\n")
    
    return page_clips


def _process_markdown_section(page, md_name: str, skip_llm: bool, voice: str):
    """处理单个Markdown章节"""
    import threading
    from config import get_default_bg_video
    
    tid = threading.current_thread().name
    pn = page.page_num
    print(f"  [{tid}] 开始处理第 {pn} 章节: {page.title or '(无标题)'}")

    # 从 page 获取原始表格数据（如果存在）
    tables = getattr(page, 'tables', None)
    
    # 生成讲稿（传入表格数据）
    script = write_script(page.text, page_title=page.title, skip_llm=skip_llm,
                          pdf_name=md_name, page_num=pn, tables=tables)
    print(f"  [{tid}] 第 {pn} 章节 ✔ 讲稿 ({len(script)}字)")
    
    # 提取关键要点（用于左侧信息卡展示）
    if not skip_llm:
        key_points = extract_key_points(page.text, page.title, tables)
        print(f"  [{tid}] 第 {pn} 章节 ✔ 关键要点 ({len(key_points)}条)")
    else:
        key_points = []

    # TTS 音频
    audio_path = generate_audio(
        text=script,
        page_num=pn,
        pdf_name=md_name,
        voice=voice,
    )
    print(f"  [{tid}] 第 {pn} 章节 ✔ 音频")

    # 使用统一背景视频
    bg_video_path = get_default_bg_video() if not MINIMAX_VIDEO_ENABLED else None
    if not bg_video_path:
        # 开启 MiniMax 模式或没有默认背景，使用章节内容生成
        video_prompt = build_video_prompt(script, page_title=page.title or "")
        bg_video_path = generate_bg_video(
            prompt=video_prompt,
            page_num=pn,
            pdf_name=md_name,
            screenshot_path=None,
        )
    print(f"  [{tid}] 第 {pn} 章节 ✔ 背景视频")

    # 合成页面视频片段（表格图片在 page.image_paths 中）
    word_ts = load_word_timestamps(pn, md_name)
    clip = compose_page_clip(
        bg_video_path=bg_video_path,
        audio_path=audio_path,
        image_paths=page.image_paths,  # 包含表格图片
        script_text=script,
        page_num=pn,
        page_title=page.title or "",
        key_points=key_points if key_points else page.key_points,  # 使用LLM提取的要点
        word_timestamps=word_ts,
        screenshot_path=None,
    )
    print(f"  [{tid}] 第 {pn} 章节 ✔ 视频片段合成完成")
    return pn, clip


def _process_page(page, pdf_name, skip_llm, no_ai_image, voice):
    """
    处理单页：LLM讲稿 + 配图 + TTS音频 + 统一背景视频。
    返回 (page_num, clip) 元组。
    """
    import threading
    from config import get_default_bg_video
    
    tid = threading.current_thread().name
    pn = page.page_num
    print(f"  [{tid}] 开始处理第 {pn} 页: {page.title or '(无标题)'}")

    # 2a. LLM 润色讲稿（串行，后续步骤依赖脚本内容）
    script = write_script(page.text, page_title=page.title, skip_llm=skip_llm,
                          pdf_name=pdf_name, page_num=pn)
    print(f"  [{tid}] 第 {pn} 页 ✔ 讲稿 ({len(script)}字)")

    # 2b/2c. 配图 + TTS 两路并发（背景使用统一视频，不再单独生成）
    sub_results = {}
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix=f"p{pn}") as sub:
        fut_img = sub.submit(
            get_images_for_page,
            page_num=pn,
            pdf_images=page.image_paths,
            page_title=page.title,
            pdf_name=pdf_name,
            generate_if_empty=not no_ai_image,
        )
        fut_tts = sub.submit(
            generate_audio,
            text=script,
            page_num=pn,
            pdf_name=pdf_name,
            voice=voice,
        )
        sub_results["images"] = fut_img.result()
        sub_results["audio"]  = fut_tts.result()

    # 使用统一背景视频
    bg_video_path = get_default_bg_video() if not MINIMAX_VIDEO_ENABLED else None
    if not bg_video_path:
        # 开启 MiniMax 模式或没有默认背景，使用页面内容生成
        video_prompt = build_video_prompt(script, page_title=page.title or "")
        bg_video_path = generate_bg_video(
            prompt=video_prompt,
            page_num=pn,
            pdf_name=pdf_name,
            screenshot_path=page.screenshot_path,
        )
    print(f"  [{tid}] 第 {pn} 页 ✔ 配图{len(sub_results['images'])}张 / 音频 / 背景")

    # 2e. 合成页面视频片段
    word_ts = load_word_timestamps(pn, pdf_name)
    clip = compose_page_clip(
        bg_video_path=bg_video_path,
        audio_path=sub_results["audio"],
        image_paths=sub_results["images"],
        script_text=script,
        page_num=pn,
        page_title=page.title or "",
        key_points=page.key_points,
        word_timestamps=word_ts,
        screenshot_path=page.screenshot_path,
    )
    print(f"  [{tid}] 第 {pn} 页 ✔ 视频片段合成完成")
    return pn, clip


def parse_args():
    parser = argparse.ArgumentParser(
        description="金融报告 PDF → 带语音讲解 MP4 视频",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py --input 收盘总结.pdf
  python main.py --input ETF分析.pdf --output output/etf_video.mp4
  python main.py --input 财务报告.pdf --pages 1-10 --skip-llm
  python main.py --list-voices
        """,
    )
    parser.add_argument("--input", "-i", help="输入 PDF 或 Markdown 文件路径")
    parser.add_argument("--output", "-o", help="输出 MP4 文件路径（默认 output/日期_文件名.mp4）")
    parser.add_argument("--pages", "-p", help='处理页面范围，如 "1-5" 或 "1,3,5"，默认全部（仅PDF）')
    parser.add_argument("--voice", "-v", help="TTS 音色，使用 --list-voices 查看可选项")
    parser.add_argument("--skip-llm", action="store_true", help="跳过 LLM 润色，直接朗读原文（快速模式）")
    parser.add_argument("--no-ai-image", action="store_true", help="不生成 AI 配图（仅使用 PDF 原图）")
    parser.add_argument("--list-voices", action="store_true", help="列出可用的中文音色")
    parser.add_argument("--format", "-f", choices=["pdf", "markdown", "auto"], default="auto", help="输入文件格式（默认auto自动检测）")
    return parser.parse_args()


def _detect_format(file_path: str, format_arg: str) -> str:
    """根据文件扩展名或参数检测输入格式"""
    if format_arg != "auto":
        return format_arg
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".md" or ext == ".markdown":
        return "markdown"
    return "pdf"


def main():
    args = parse_args()

    # 列出音色
    if args.list_voices:
        print("\n可用的中文音色：\n")
        for voice_id, desc in get_available_voices():
            print(f"  {voice_id}")
            print(f"    {desc}\n")
        return

    # 检查输入
    if not args.input:
        print("错误：请指定输入 PDF 文件路径，使用 --input 参数")
        print("使用 --help 查看帮助")
        sys.exit(1)

    if not os.path.exists(args.input):
        print(f"错误：文件不存在: {args.input}")
        sys.exit(1)

    input_path = os.path.abspath(args.input)
    input_name = os.path.splitext(os.path.basename(input_path))[0]
    input_format = _detect_format(input_path, args.format)

    # 确定输出路径
    if args.output:
        output_path = args.output
    else:
        date_str = datetime.now().strftime("%Y%m%d_%H%M")
        output_path = os.path.join(OUTPUT_DIR, f"{date_str}_{input_name}.mp4")

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    os.makedirs(TEMP_DIR, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"  FinReport2Video - 金融报告转视频工具")
    print(f"{'='*60}")
    print(f"  输入: {input_path}")
    print(f"  格式: {input_format.upper()}")
    print(f"  输出: {output_path}")
    print(f"  模式: {'快速（跳过LLM）' if args.skip_llm else 'LLM润色'}，并发度={MAX_CONCURRENT_PAGES}")
    if args.pages and input_format == "pdf":
        print(f"  页面: {args.pages}")
    print(f"{'='*60}\n")

    start_time = time.time()

    # 根据格式选择处理流程
    if input_format == "markdown":
        page_clips = _process_markdown(input_path, input_name, args)
    else:
        page_clips = _process_pdf(input_path, input_name, args)


    # ── Step 3: 合并所有片段 ───────────────────────────────────────────────────
    print("Step 3/5  合并视频片段...")
    compose_final_video(page_clips, output_path)

    # ── Step 4: 完成 ───────────────────────────────────────────────────────────
    elapsed = time.time() - start_time
    file_size = os.path.getsize(output_path) / (1024 * 1024)

    print(f"\n{'='*60}")
    print(f"  完成！")
    print(f"  输出文件: {output_path}")
    print(f"  文件大小: {file_size:.1f} MB")
    print(f"  处理时间: {elapsed:.0f} 秒")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
