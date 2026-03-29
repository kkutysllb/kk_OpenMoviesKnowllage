"""
FinReport2Video 主入口
金融报告 Markdown → 带语音讲解的 MP4 视频

用法:
    python main.py --input report.md
    python main.py --input report.md --output output/my_video.mp4
    python main.py --input report.md --skip-llm --voice zh-CN-YunyangNeural
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


def _process_markdown(md_path: str, md_name: str, args) -> list:
    """处理Markdown文件，返回视频片段列表"""
    from config import get_default_bg_video
    
    # ── Step 1: 解析 Markdown ───────────────────────────────────────────────────
    print("Step 1/4  解析 Markdown（按标题分章节）...")
    from config import TEMP_DIR
    metadata, sections = parse_markdown(md_path)
    
    # 获取 Markdown 文件目录（用于解析相对图片路径）
    md_dir = os.path.dirname(os.path.abspath(md_path))
    
    # 转换为 PageData，传入文件名用于创建子目录
    pages_data = convert_to_page_data(
        sections, 
        TEMP_DIR, 
        md_name=md_name,
        md_path=md_path,
        cover_image=metadata.cover_image,
        md_dir=md_dir
    )
    print(f"  文档标题: {metadata.title or md_name}")
    print(f"  文档日期: {metadata.date or '未知'}")
    print(f"  封面图片: {'已提取' if metadata.cover_image else '无'}")
    print(f"  共解析 {len(pages_data)} 个章节\n")

    # ── Step 2: 生成片头页 ───────────────────────────────────────────────────
    print("Step 2/4  生成片头页...")
    report_title = metadata.title or sections[0].title if sections else md_name
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
    intro_narration = f"以下是，{report_title}，"
    if metadata.abstract:
        # 完整朗读摘要内容（增加长度限制以确保完整性）
        intro_narration += metadata.abstract[:300]
    if metadata.date:
        intro_narration += f"。本文发布日期：{metadata.date}。"
    intro_narration += f"接下来让我们一起了解，共{total_sections}个章节的详细内容。"

    intro_audio_path = generate_audio(
        text=intro_narration,
        page_num=0,
        pdf_name=md_name,
    )
    intro_duration = max(6.0, _get_tts_duration(intro_audio_path))

    intro_clip = compose_intro_clip(
        bg_video_path=intro_bg_path,
        report_title=report_title,
        report_abstract=metadata.abstract,
        analyst=metadata.author,
        date=metadata.date,
        total_pages=total_sections,
        duration=intro_duration,
        audio_path=intro_audio_path,
        data_source=metadata.data_source,
        cover_image=pages_data[0].screenshot_path if pages_data else "",
    )
    print(f"  片头页完成（{report_title[:30]}...）\n")

    # ── Step 3: 逐章节处理 ─────────────────────────────────────────────────────
    print(f"Step 3/4  并发处理 {len(pages_data)} 个章节（最多 {MAX_CONCURRENT_PAGES} 章同时）...")
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
    print(f"  Step 3 完成，共耗时 {t2_elapsed:.0f}s\n")
    
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
    
    # 获取表格图片（渲染后的表格图片）
    table_images = getattr(page, 'table_images', [])

    # 获取图表图片（章节内的图片）
    chart_images = page.image_paths if page.image_paths else []

    # ── 空图片占位：使用 AI 生成 ─────────────────────────────────────────────
    # 右侧表格区无图时，生成表格占位图
    if not table_images:
        placeholder = get_images_for_page(
            page_num=pn,
            pdf_images=[],
            page_title=f"{page.title or '数据表格'}",
            pdf_name=md_name,
            generate_if_empty=True,
        )
        if placeholder:
            table_images = placeholder
            print(f"  [{tid}] 第 {pn} 章节 ✔ AI表格占位图")

    # 左侧图表区无图时，生成图表占位图
    if not chart_images:
        placeholder = get_images_for_page(
            page_num=pn,
            pdf_images=[],
            page_title=f"{page.title or '数据分析'}",
            pdf_name=md_name,
            generate_if_empty=True,
        )
        if placeholder:
            chart_images = placeholder
            print(f"  [{tid}] 第 {pn} 章节 ✔ AI图表占位图")
    
    # 生成讲稿（传入表格数据和章节序号）
    script = write_script(page.text, page_title=page.title, skip_llm=skip_llm,
                          pdf_name=md_name, page_num=pn, tables=tables,
                          section_index=page.page_num)
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

    # 合成页面视频片段
    # - 左侧：图表图片轮播
    # - 右侧：表格图片轮播
    word_ts = load_word_timestamps(pn, md_name)
    clip = compose_page_clip(
        bg_video_path=bg_video_path,
        audio_path=audio_path,
        image_paths=chart_images,          # 图表图片
        chart_images=chart_images,         # 左侧图表轮播
        table_images=table_images,         # 右侧表格轮播
        script_text=script,
        page_num=pn,
        page_title=page.title or "",
        key_points=key_points if key_points else page.key_points,
        word_timestamps=word_ts,
        screenshot_path=page.screenshot_path,  # 封面截图
    )
    print(f"  [{tid}] 第 {pn} 章节 ✔ 视频片段合成完成")
    return pn, clip


def parse_args():
    parser = argparse.ArgumentParser(
        description="金融报告 Markdown → 带语音讲解 MP4 视频",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py --input 收盘总结.md
  python main.py --input ETF分析.md --output output/etf_video.mp4
  python main.py --input 财务报告.md --skip-llm
  python main.py --list-voices
        """,
    )
    parser.add_argument("--input", "-i", help="输入 Markdown 文件路径")
    parser.add_argument("--output", "-o", help="输出 MP4 文件路径（默认 output/日期_文件名.mp4）")
    parser.add_argument("--voice", "-v", help="TTS 音色，使用 --list-voices 查看可选项")
    parser.add_argument("--skip-llm", action="store_true", help="跳过 LLM 润色，直接朗读原文（快速模式）")
    parser.add_argument("--list-voices", action="store_true", help="列出可用的中文音色")
    return parser.parse_args()


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
        print("错误：请指定输入 Markdown 文件路径，使用 --input 参数")
        print("使用 --help 查看帮助")
        sys.exit(1)

    if not os.path.exists(args.input):
        print(f"错误：文件不存在: {args.input}")
        sys.exit(1)

    input_path = os.path.abspath(args.input)
    input_name = os.path.splitext(os.path.basename(input_path))[0]

    # 确定输出路径
    if args.output:
        output_path = args.output
    else:
        date_str = datetime.now().strftime("%Y%m%d_%H%M")
        output_path = os.path.join(OUTPUT_DIR, f"{date_str}_{input_name}.mp4")

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    os.makedirs(TEMP_DIR, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"  FinReport2Video - 金融报告 Markdown 转视频工具")
    print(f"{'='*60}")
    print(f"  输入: {input_path}")
    print(f"  输出: {output_path}")
    print(f"  模式: {'快速（跳过LLM）' if args.skip_llm else 'LLM润色'}，并发度={MAX_CONCURRENT_PAGES}")
    print(f"{'='*60}\n")

    start_time = time.time()

    # 处理 Markdown 文件
    page_clips = _process_markdown(input_path, input_name, args)


    # ── Step 3: 合并所有片段 ───────────────────────────────────────────────────
    print("Step 3/4  合并视频片段...")
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
