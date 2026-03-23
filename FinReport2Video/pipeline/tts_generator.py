"""
TTS 语音生成模块 v3
- 主引擎：阿里云 qwen3-tts-flash（高质量中文语音）
- 备用引擎：edge-tts（离线，免费）
- 文本清洗：去除 PDF 乱码字、规范化标点、过滤表格行
- 分段合成：超长文本按句子切片分段调用，音频拼接后整体计时
- 时间戳：基于字符数 + 标点权重 + 音频时长估算（逐词精度）
"""
import os
import re
import json
import asyncio
import requests
import wave
import struct
from typing import Optional, List, Dict

import numpy as np

# 阿里云 TTS 配置（与通义万相共用 Key）
QWEN_TTS_API_KEY = os.getenv("QWEN_API_KEY", "sk-2f709569b1084aeea8d474c1e55d7bc6")
QWEN_TTS_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
QWEN_TTS_MODEL = "qwen3-tts-flash"
QWEN_TTS_VOICE = "Cherry"   # 女声，清晰专业；可选：Ethan(男)、Serena、Dylan

# edge-tts 配置（主引擎，免费）
EDGE_TTS_VOICE = "zh-CN-XiaoxiaoNeural"  # 女声，清晰自然
# 可选音色：zh-CN-YunxiNeural(男)、zh-CN-YunyangNeural(新闻风)、zh-CN-XiaoyiNeural(活泼)
EDGE_TTS_RATE = "+5%"    # 语速稍快一点，金融播报感
EDGE_TTS_VOLUME = "+0%"

# 金融术语读音修正（已禁用：LLM 已能生成流畅讲稿，不需要强制断句）
# _FUTURES_TERMS = (
#     r"空头|多头|期指|期货|持仓|减仓|开仓|平仓|基差|升水|贴水|连续平仓|政策面|资金面"
# )
# _FUTURES_TERMS_RE = re.compile(
#     rf'([\u4e00-\u9fff\w])({_FUTURES_TERMS})([\u4e00-\u9fff\w])'
# )
_FUTURES_TERMS_RE = None  # 禁用强制断句

SEGMENT_MAX_CHARS = 180  # 每段最大字数（避免 TTS 单次超长出错）

# 标点停顿权重（相对于普通字符）
PUNCT_WEIGHTS = {
    "。": 3.5, "！": 3.0, "？": 3.0,
    "，": 1.8, "、": 1.5, "；": 2.0,
    "：": 1.5, "…": 2.5,
    " ": 0.3,
}


# ── 文本清洗 ──────────────────────────────────────────────────────────────────

def _clean_tts_text(text: str) -> str:
    """
    清洗 PDF 提取的文本，适合输入 TTS。
    - NFKC 规范化（康熙部首等变体汉字 → 标准汉字）
    - 删除 Unicode 替换字符 \ufffd
    - 删除 PDF 常见乱码区间（PUA 私用区）
    - 将换行带入句号或逗号
    - 删除纯数字表格行（如 "12.3  45.6  78.9"）
    - 压缩连续空白/换行
    - 限制连续相同字符（防止平题线等）
    """
    import unicodedata
    # NFKC 规范化：将康熙部首、CJK兴趣区等变体字转换为标准字
    text = unicodedata.normalize('NFKC', text)
    # 删除 Unicode 替换字符
    text = text.replace('\ufffd', '')
    # 删除 PUA 私用区字符（PDF 字体映射乱码）
    text = re.sub(r'[\ue000-\uf8ff]', '', text)
    # 删除控制字符（除换行）
    text = re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '', text)

    # 逐行处理
    lines = text.split('\n')
    cleaned_lines = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # 跳过纯数字/符号行（表格行）
        if re.match(r'^[\d\s.,/%\-+()（）\[\]\u2014\u2013\u00b1★▲▼◆■●]+$', line):
            continue
        # 跳过过短行（< 3字）
        if len(line) < 3:
            continue
        cleaned_lines.append(line)

    # 将行合并为完整句子
    result = ''
    for line in cleaned_lines:
        if result and result[-1] in '。！？…':
            result += line
        elif result:
            result += '，' + line
        else:
            result = line

    # 删除连续相同字符（超过3个）
    result = re.sub(r'(.)\1{3,}', r'\1\1', result)
    # 压缩连续标点
    result = re.sub(r'[，、]{2,}', '，', result)
    result = re.sub(r'[。！？]{2,}', '。', result)
    # 末尾补句号
    if result and result[-1] not in '。！？…':
        result += '。'

    # 金融期货术语边界加停顿（已禁用：LLM 已能生成流畅讲稿）
    # result = _FUTURES_TERMS_RE.sub(r'\1\2' + '，' + r'\3', result)
    if _FUTURES_TERMS_RE:
        result = _FUTURES_TERMS_RE.sub(r'\1\2' + '，' + r'\3', result)
    # 压缩连续逗号
    result = re.sub(r'\uff0c{2,}', '\uff0c', result)
    # 将半角逗号统一为全角（TTS 读音更自然）
    result = result.replace(',', '\uff0c')
    return result.strip()


def _split_into_segments(text: str, max_chars: int = SEGMENT_MAX_CHARS) -> List[str]:
    """
    将文本按句子切片，每段不超过 max_chars 个字。
    按句号、感叹号、问号分割。
    """
    if len(text) <= max_chars:
        return [text] if text.strip() else []

    segments: List[str] = []
    # 按句子分割符切分，保留分割符
    parts = re.split(r'(?<=[。！？…])', text)
    current = ''
    for part in parts:
        if not part:
            continue
        if len(current) + len(part) <= max_chars:
            current += part
        else:
            if current:
                segments.append(current)
            # 单句超长则强制按字数切分
            if len(part) > max_chars:
                for i in range(0, len(part), max_chars):
                    seg = part[i:i + max_chars]
                    if seg.strip():
                        segments.append(seg)
                current = ''
            else:
                current = part
    if current.strip():
        segments.append(current)

    return [s for s in segments if s.strip()]


# ── 公开接口 ──────────────────────────────────────────────────────────────────

def generate_audio(
    text: str,
    page_num: int,
    pdf_name: str,
    voice: Optional[str] = None,
    rate: Optional[str] = None,
    temp_dir: str = "temp",
) -> str:
    """
    生成 TTS 音频文件，同时生成逐词时间戳 JSON。

    Returns:
        音频文件路径（MP3）
        同目录下生成 page_XXX_words.json
    """
    save_dir = os.path.join(temp_dir, pdf_name, "audio")
    os.makedirs(save_dir, exist_ok=True)
    output_path = os.path.join(save_dir, f"page_{page_num:03d}.mp3")
    words_path = os.path.join(save_dir, f"page_{page_num:03d}_words.json")

    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        print(f"    音频已存在，跳过生成: {output_path}")
        return output_path

    # 1. 记录 LLM 標记的断行位置，再清洗文本
    # LLM 在讲稿里用 \n 表示字幕断行位置，需要先记录再删除
    newline_positions: List[int] = []  # 在原始文本中的 \n 位置
    stripped_text = ''                  # 删除 \n 后的文本（用于对比时间戳）
    for ch in text:
        if ch == '\n':
            newline_positions.append(len(stripped_text))  # 记在当前位置之后
        else:
            stripped_text += ch

    # 清洗文本（此时清洗已不含 \n）
    clean_text = _clean_tts_text(stripped_text)
    if not clean_text:
        print(f"    [警告] 清洗后文本为空，使用静音占位")
        _create_silent_audio(output_path, duration=5)
        return output_path

    print(f"    原文 {len(text)} 字 → 清洗后 {len(clean_text)} 字")

    # 2. 优先用 edge-tts（免费）
    duration = 0.0
    segments = _split_into_segments(clean_text, SEGMENT_MAX_CHARS)
    seg_durations: List[float] = []

    use_voice = voice or EDGE_TTS_VOICE
    use_rate  = rate or EDGE_TTS_RATE

    # edge-tts 一次性合成全文（它自身支持长文本，不需要我们分段）
    edge_ok = False
    try:
        asyncio.run(_edge_tts_generate(
            clean_text, output_path, use_voice, use_rate
        ))
        duration = _get_audio_duration(output_path)
        # 按分段分配时长（按字数比例）
        total_chars = sum(len(s) for s in segments)
        for seg in segments:
            ratio = len(seg) / total_chars if total_chars > 0 else 1.0 / len(segments)
            seg_durations.append(duration * ratio)
        print(f"    音频生成完成（edge-tts）: {output_path}")
        edge_ok = True
    except Exception as e:
        print(f"    [edge-tts 失败] {e}，尝试 Qwen TTS 备用...")

    if not edge_ok:
        # 备用：Qwen TTS 分段合成
        all_audio_bytes: List[bytes] = []
        seg_durations = []
        for seg_idx, seg in enumerate(segments, 1):
            audio_bytes = _generate_qwen_tts(seg, voice or QWEN_TTS_VOICE)
            if audio_bytes:
                all_audio_bytes.append(audio_bytes)
                seg_path = output_path + f".seg{seg_idx}.mp3"
                with open(seg_path, "wb") as f:
                    f.write(audio_bytes)
                seg_durations.append(_get_audio_duration(seg_path))
                os.remove(seg_path)
            else:
                seg_durations.append(len(seg) / 4.0)
                print(f"    [警告] 第 {seg_idx} 段 Qwen TTS 失败")

        if all_audio_bytes:
            with open(output_path, "wb") as f:
                f.write(b''.join(all_audio_bytes))
            duration = _get_audio_duration(output_path)
            print(f"    音频生成完成（Qwen TTS，{len(segments)} 段）: {output_path}")
        else:
            print(f"    [警告] TTS 全部失败，使用静音占位")
            _create_silent_audio(output_path, duration=5)
            duration = 5.0
            seg_durations = [duration / max(len(segments), 1)] * len(segments)

    # 3. 生成逐词时间戳（基于清洗后文本 + 分段时长分配）
    words = _estimate_word_timestamps_segmented(segments, seg_durations, duration)

    # 4. 将 LLM 标记的 \n 断行位置插回时间戳列表
    #    newline_positions 是基于 stripped_text 的位置索引
    #    words 里每个 token 对应 clean_text 的一个字符，两者长度可能因清洗而差异
    #    简单起见：按比例映射新行位置到 words 索引
    if newline_positions and words:
        ratio = len(words) / max(len(stripped_text), 1)
        final_words: List[Dict] = []
        inserted = set()
        for pos in sorted(newline_positions):
            word_idx = min(int(pos * ratio), len(words) - 1)
            inserted.add(word_idx)
        # 按顺序拼装：在对应位置插入虚拟 \n token
        nl_sorted = sorted((min(int(p * ratio), len(words) - 1), p)
                           for p in newline_positions)
        ni = 0  # newline 指针
        for idx, w in enumerate(words):
            # 如果当前 idx 达到某个断行点，插入虚拟 token
            while ni < len(nl_sorted) and nl_sorted[ni][0] == idx:
                # 时间拘取上一个 token 的 end
                t_ref = final_words[-1]["end"] if final_words else w["start"]
                final_words.append({"word": "\n", "start": t_ref, "end": t_ref})
                ni += 1
            final_words.append(w)
        # 尾部剩余的 \n
        while ni < len(nl_sorted):
            t_ref = final_words[-1]["end"] if final_words else duration
            final_words.append({"word": "\n", "start": t_ref, "end": t_ref})
            ni += 1
        words = final_words

    with open(words_path, "w", encoding="utf-8") as f:
        json.dump(words, f, ensure_ascii=False, indent=2)
    print(f"    时间戳生成: {len(words)} 个字符 / 总时长 {duration:.1f}s")

    return output_path


def load_word_timestamps(page_num: int, pdf_name: str, temp_dir: str = "temp") -> List[Dict]:
    """加载词时间戳 JSON，返回 [{word, start, end}, ...]"""
    words_path = os.path.join(temp_dir, pdf_name, "audio", f"page_{page_num:03d}_words.json")
    if not os.path.exists(words_path):
        return []
    with open(words_path, "r", encoding="utf-8") as f:
        return json.load(f)


# ── Qwen TTS ──────────────────────────────────────────────────────────────────

def _generate_qwen_tts(text: str, voice: str = QWEN_TTS_VOICE) -> Optional[bytes]:
    """调用阿里云 qwen3-tts-flash 生成音频，返回 bytes 或 None"""
    try:
        headers = {
            "Authorization": f"Bearer {QWEN_TTS_API_KEY}",
            "Content-Type": "application/json; charset=utf-8",
        }
        payload = {
            "model": QWEN_TTS_MODEL,
            "input": {"text": text, "voice": voice},
            "parameters": {"rate": 0},
        }
        r = requests.post(QWEN_TTS_URL, headers=headers, json=payload, timeout=60)
        if not r.ok:
            print(f"    [Qwen TTS] API 错误: {r.status_code} {r.text[:100]}")
            return None

        data = r.json()
        audio_url = data.get("output", {}).get("audio", {}).get("url")
        if not audio_url:
            print(f"    [Qwen TTS] 无音频 URL: {data}")
            return None

        audio_resp = requests.get(audio_url, timeout=60)
        if not audio_resp.ok:
            print(f"    [Qwen TTS] 下载失败: {audio_resp.status_code}")
            return None

        return audio_resp.content
    except Exception as e:
        print(f"    [Qwen TTS] 异常: {e}")
        return None


# ── edge-tts 备用 ──────────────────────────────────────────────────────────────

async def _edge_tts_generate(text: str, output_path: str, voice: str, rate: str):
    import edge_tts
    communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate, volume=EDGE_TTS_VOLUME)
    await communicate.save(output_path)


# ── 时间戳估算 ────────────────────────────────────────────────────────────────

def _estimate_word_timestamps_segmented(
    segments: List[str], seg_durations: List[float], total_duration: float
) -> List[Dict]:
    """
    分段时间戳估算：
    1. 先按各段实际时长划分时间区间
    2. 段内按字符权重细分
    """
    if not segments:
        return []

    # 如果分段时长之和与总时长偏差较大，整体等比缩放
    sum_seg = sum(seg_durations)
    if sum_seg <= 0:
        sum_seg = total_duration

    scale = total_duration / sum_seg if sum_seg > 0 else 1.0

    words: List[Dict] = []
    t_offset = 0.0

    for seg_text, seg_dur in zip(segments, seg_durations):
        scaled_dur = seg_dur * scale
        # 段内按字符权重分配时间
        seg_words = _estimate_word_timestamps(seg_text, scaled_dur, t_offset)
        words.extend(seg_words)
        t_offset += scaled_dur

    return words


def _estimate_word_timestamps(text: str, duration: float, offset: float = 0.0) -> List[Dict]:
    """
    基于字符数 + 标点权重估算逐字时间戳。
    """
    if not text or duration <= 0:
        return []

    weights = [PUNCT_WEIGHTS.get(ch, 1.0) for ch in text]
    total_weight = sum(weights)
    if total_weight == 0:
        return []

    words = []
    t = offset
    for ch, w in zip(text, weights):
        dur = (w / total_weight) * duration
        words.append({
            "word": ch,
            "start": round(t, 3),
            "end": round(t + dur, 3),
        })
        t += dur

    return words


# ── 音频工具 ──────────────────────────────────────────────────────────────────

def _get_audio_duration(audio_path: str) -> float:
    """读取音频文件时长（秒）"""
    try:
        if audio_path.endswith(".wav"):
            with wave.open(audio_path, "r") as wf:
                return wf.getnframes() / wf.getframerate()
        import subprocess
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
            capture_output=True, text=True, timeout=10,
        )
        return float(result.stdout.strip())
    except Exception:
        size = os.path.getsize(audio_path) if os.path.exists(audio_path) else 0
        return size / 16000


def _create_silent_audio(output_path: str, duration: int = 5):
    """生成静音 WAV 占位文件"""
    wav_path = output_path.replace(".mp3", ".wav")
    sample_rate = 22050
    n_samples = sample_rate * duration
    with wave.open(wav_path, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack("<" + "h" * n_samples, *([0] * n_samples)))
    # 如果需要 mp3 路径，直接把 wav 路径返回（函数调用方用 output_path 接收）
    if output_path.endswith(".mp3"):
        import shutil
        shutil.copy(wav_path, output_path)


def get_available_voices() -> list:
    """返回推荐音色列表"""
    return [
        ("Cherry", "Cherry - 女声，清晰专业（推荐）"),
        ("Serena", "Serena - 女声，温暖自然"),
        ("Ethan", "Ethan - 男声，沉稳"),
        ("Dylan", "Dylan - 男声，活力"),
        ("zh-CN-XiaoxiaoNeural", "[edge-tts] 晓晓 - 女声"),
        ("zh-CN-YunyangNeural", "[edge-tts] 云扬 - 男声，新闻风"),
    ]
