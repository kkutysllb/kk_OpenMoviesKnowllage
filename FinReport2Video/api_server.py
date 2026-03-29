"""
FinReport2Video FastAPI 后端服务
提供 PDF 上传、视频生成任务管理、视频流式播放接口

启动: uvicorn api_server:app --port 8765 --reload
"""
import os
import sys
import uuid
import time
import threading
import subprocess
import hashlib
from urllib.parse import quote
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel
from typing import Dict

# 添加项目目录到路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from config import OUTPUT_DIR, TEMP_DIR, INPUT_DIR
from pipeline.pdf_parser import parse_pdf_smart

# ── 文件重复检查 ────────────────────────────────────────────────────────────────

def _compute_file_hash(content: bytes) -> str:
    """计算文件内容的 MD5 hash"""
    return hashlib.md5(content).hexdigest()


def _find_duplicate_file(content: bytes, input_dir: str) -> str | None:
    """
    检查 input_dir 下是否有相同内容的文件
    返回已存在文件的路径，如果没有返回 None
    """
    target_hash = _compute_file_hash(content)
    
    if not os.path.exists(input_dir):
        return None
    
    for filename in os.listdir(input_dir):
        if not filename.endswith('.pdf'):
            continue
        filepath = os.path.join(input_dir, filename)
        try:
            with open(filepath, 'rb') as f:
                existing_hash = _compute_file_hash(f.read())
            if existing_hash == target_hash:
                return filepath
        except Exception:
            continue
    
    return None


# ── 任务状态定义 ───────────────────────────────────────────────────────────────

class TaskStatus:
    PENDING   = "pending"
    RUNNING   = "running"
    COMPLETED = "completed"
    FAILED    = "failed"


class TaskInfo(BaseModel):
    task_id: str
    filename: str
    pdf_path: str
    output_path: Optional[str] = None
    status: str = TaskStatus.PENDING
    progress: int = 0          # 0-100
    log: str = ""
    created_at: str = ""
    completed_at: Optional[str] = None
    error: Optional[str] = None
    file_size_mb: Optional[float] = None

    class Config:
        # 允许字段修改
        extra = "allow"


# 内存任务存储（开发阶段够用）
TASKS: dict[str, TaskInfo] = {}
TASKS_LOCK = threading.Lock()
# 子进程注册表（task_id -> subprocess.Popen）
PROCS: dict[str, subprocess.Popen] = {}
PROCS_LOCK = threading.Lock()

# ── FastAPI 应用 ───────────────────────────────────────────────────────────────

app = FastAPI(title="FinReport2Video API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 辅助函数 ───────────────────────────────────────────────────────────────────

def _update_task(task_id: str, **kwargs):
    with TASKS_LOCK:
        task = TASKS.get(task_id)
        if task:
            for k, v in kwargs.items():
                setattr(task, k, v)


def _append_log(task_id: str, line: str):
    with TASKS_LOCK:
        task = TASKS.get(task_id)
        if task:
            task.log += line + "\n"


def _parse_progress(line: str) -> Optional[int]:
    """从日志行解析进度（0-100）。适配并发改造后的日志格式。"""
    # Step 1/5  解析 PDF
    if "Step 1/5" in line:
        return 5
    # Step 1.5/5  生成片头页
    if "Step 1.5/5" in line:
        return 10
    # Step 2/5  并发处理 N 页（并发次入口日志）
    if "Step 2/5" in line and "并发处理" in line:
        return 12
    # 并发进度："进度: {done}/{total} 页完成"
    if "进度:" in line and "页完成" in line:
        try:
            # 格式："  进度: 3/11 页完成 (45s 已耗)"
            part = line.split("进度:")[1].strip().split("页完成")[0].strip()
            cur, total = part.split("/")
            cur = int(cur.strip())
            total = int(total.strip())
            if total > 0:
                return 12 + int(cur / total * 70)   # 12% ~ 82%
        except Exception:
            pass
    # Step 3/5  合并视频片段
    if "Step 3/5" in line:
        return 85
    # 完成
    if "视频已保存" in line or "完成！" in line:
        return 100
    return None


def _run_generation(task_id: str, pdf_path: str, skip_llm: bool, pages: Optional[str]):
    """在后台线程中运行视频生成流程"""
    try:
        _update_task(task_id, status=TaskStatus.RUNNING, progress=2)
        _append_log(task_id, f"[{datetime.now().strftime('%H:%M:%S')}] 开始处理: {os.path.basename(pdf_path)}")

        # 确定输出路径
        pdf_name = os.path.splitext(os.path.basename(pdf_path))[0]
        date_str = datetime.now().strftime("%Y%m%d_%H%M")
        output_path = os.path.join(OUTPUT_DIR, f"{date_str}_{pdf_name}.mp4")
        _update_task(task_id, output_path=output_path)

        # 构建命令
        cmd = [
            sys.executable,
            os.path.join(BASE_DIR, "main.py"),
            "--input", pdf_path,
            "--output", output_path,
        ]
        if skip_llm:
            cmd.append("--skip-llm")
        if pages:
            cmd.extend(["--pages", pages])

        # 启动子进程，实时捕获输出
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=BASE_DIR,
            env={**os.environ, 'PYTHONUNBUFFERED': '1'},  # 强制 Python 无缓冲输出
        )
        # 注册子进程，供取消接口使用
        with PROCS_LOCK:
            PROCS[task_id] = proc

        # 实时读取输出
        import select
        while True:
            # 检查进程是否结束
            ret = proc.poll()
            # 非阻塞读取可用的输出
            ready, _, _ = select.select([proc.stdout], [], [], 0.1)
            if proc.stdout in ready:
                line = proc.stdout.readline()
                if line:
                    line = line.rstrip()
                    if line:
                        _append_log(task_id, f"[{datetime.now().strftime('%H:%M:%S')}] {line}")
                        prog = _parse_progress(line)
                        if prog is not None:
                            _update_task(task_id, progress=prog)
            # 进程结束且没有更多输出
            if ret is not None:
                # 清空剩余输出
                for line in proc.stdout:
                    line = line.rstrip()
                    if line:
                        _append_log(task_id, f"[{datetime.now().strftime('%H:%M:%S')}] {line}")
                        prog = _parse_progress(line)
                        if prog is not None:
                            _update_task(task_id, progress=prog)
                break

        # 清理进程注册
        with PROCS_LOCK:
            PROCS.pop(task_id, None)

        # 若任务已被取消，直接返回
        with TASKS_LOCK:
            current_status = TASKS.get(task_id)
        if current_status and current_status.status == TaskStatus.FAILED and "已取消" in (current_status.error or ""):
            return

        if proc.returncode == 0 and os.path.exists(output_path):
            file_size = os.path.getsize(output_path) / (1024 * 1024)
            _update_task(
                task_id,
                status=TaskStatus.COMPLETED,
                progress=100,
                completed_at=datetime.now().isoformat(),
                file_size_mb=round(file_size, 1),
            )
            _append_log(task_id, f"[{datetime.now().strftime('%H:%M:%S')}] 视频生成完成！({file_size:.1f} MB)")
        else:
            raise RuntimeError(f"进程退出码: {proc.returncode}，输出文件不存在")

    except Exception as e:
        with PROCS_LOCK:
            PROCS.pop(task_id, None)
        _update_task(
            task_id,
            status=TaskStatus.FAILED,
            error=str(e),
            completed_at=datetime.now().isoformat(),
        )
        _append_log(task_id, f"[{datetime.now().strftime('%H:%M:%S')}] 错误: {e}")


# ── API 端点 ──────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {"status": "ok", "service": "FinReport2Video"}


@app.post("/api/parse")
async def parse_chapters(
    file: UploadFile = File(...),
    pages: Optional[str] = Form(None),
):
    """上传 PDF 或 Markdown 并解析章节标题列表（用于前端动画展示）"""
    filename = file.filename.lower()
    
    if filename.endswith(".pdf"):
        return await _parse_pdf_chapters(file, pages)
    elif filename.endswith(".md") or filename.endswith(".markdown"):
        return await _parse_markdown_chapters(file)
    else:
        raise HTTPException(400, "只支持 PDF 或 Markdown 文件")


async def _parse_pdf_chapters(pdf: UploadFile, pages: Optional[str]):
    """PDF 解析逻辑（按章节）"""
    # 保存临时文件
    os.makedirs(TEMP_DIR, exist_ok=True)
    tmp_name = f"parse_{int(time.time())}_{pdf.filename}"
    pdf_path = os.path.join(TEMP_DIR, tmp_name)
    content = await pdf.read()
    with open(pdf_path, "wb") as f:
        f.write(content)

    try:
        # 调用智能 PDF 解析（按章节）
        report_meta, chapters = parse_pdf_smart(pdf_path, pages=pages)
        
        # 转换章节格式（使用索引确保 key 唯一）
        chapters_data = [
            {
                "id": i,  # 使用索引作为唯一 ID
                "page_num": ch.start_page,
                "title": ch.title or f"第 {i+1} 章",
                "preview": ch.content[:80] + "..." if len(ch.content) > 80 else ch.content,
                "page_count": len(ch.page_indices),
            }
            for i, ch in enumerate(chapters)
        ]
        
        return {
            "filename": pdf.filename,
            "report_title": report_meta.title,
            "report_date": report_meta.date,
            "total_pages": len(chapters),  # 兼容前端
            "total_chapters": len(chapters),
            "chapters": chapters_data,
            "file_type": "pdf"
        }
    except Exception as e:
        raise HTTPException(500, f"解析失败: {str(e)}")
    finally:
        # 清理临时文件
        try:
            os.remove(pdf_path)
        except Exception:
            pass


async def _parse_markdown_chapters(md: UploadFile):
    """Markdown 解析逻辑"""
    from pipeline.markdown_parser import parse_markdown
    
    # 保存临时文件
    os.makedirs(TEMP_DIR, exist_ok=True)
    tmp_name = f"parse_{int(time.time())}_{md.filename}"
    md_path = os.path.join(TEMP_DIR, tmp_name)
    content = await md.read()
    with open(md_path, "wb") as f:
        f.write(content)

    try:
        # 调用 Markdown 解析（返回元组：metadata, sections）
        metadata, sections = parse_markdown(md_path)
        chapters = [
            {
                "page_num": s.order,
                "title": s.title or f"第 {s.order} 章",
                "preview": s.content[:80] + "..." if len(s.content) > 80 else s.content,
                "tables_count": len(s.tables),
                "images_count": len(s.images),
            }
            for s in sections
        ]
        return {
            "filename": md.filename,
            "report_title": metadata.title,
            "report_date": metadata.date,
            "report_abstract": metadata.abstract[:100] if metadata.abstract else "",
            "cover_image": metadata.cover_image,
            "total_pages": len(chapters),
            "total_chapters": len(chapters),
            "chapters": chapters,
            "file_type": "markdown"
        }
    except Exception as e:
        raise HTTPException(500, f"解析失败: {str(e)}")
    finally:
        # 清理临时文件
        try:
            os.remove(md_path)
        except Exception:
            pass


@app.post("/api/generate")
async def generate_video(
    file: UploadFile = File(...),
    skip_llm: bool = Form(False),
    pages: Optional[str] = Form(None),
):
    """上传 PDF 或 Markdown 并启动视频生成任务"""
    filename = file.filename.lower()
    
    if filename.endswith(".pdf"):
        return await _generate_pdf_video(file, skip_llm, pages)
    elif filename.endswith(".md") or filename.endswith(".markdown"):
        return await _generate_markdown_video(file, skip_llm)
    else:
        raise HTTPException(400, "只支持 PDF 或 Markdown 文件")


async def _generate_pdf_video(pdf: UploadFile, skip_llm: bool, pages: Optional[str]):
    """PDF 视频生成"""
    # 读取上传文件内容
    content = await pdf.read()
    
    # 检查是否已存在相同内容的文件
    existing_path = _find_duplicate_file(content, INPUT_DIR)
    if existing_path:
        print(f"    [上传] 文件已存在，复用: {existing_path}")
        pdf_path = existing_path
    else:
        # 保存新文件到 input/
        os.makedirs(INPUT_DIR, exist_ok=True)
        safe_name = f"{int(time.time())}_{pdf.filename}"
        pdf_path = os.path.join(INPUT_DIR, safe_name)
        with open(pdf_path, "wb") as f:
            f.write(content)
        print(f"    [上传] 保存新文件: {pdf_path}")

    # 创建任务
    task_id = str(uuid.uuid4())[:8]
    task = TaskInfo(
        task_id=task_id,
        filename=pdf.filename,
        pdf_path=pdf_path,
        created_at=datetime.now().isoformat(),
    )
    with TASKS_LOCK:
        TASKS[task_id] = task

    # 后台线程生成
    thread = threading.Thread(
        target=_run_generation,
        args=(task_id, pdf_path, skip_llm, pages),
        daemon=True,
    )
    thread.start()

    return {"task_id": task_id, "filename": pdf.filename, "status": TaskStatus.PENDING}


async def _generate_markdown_video(md: UploadFile, skip_llm: bool):
    """Markdown 视频生成"""
    # 保存文件到 input/
    os.makedirs(INPUT_DIR, exist_ok=True)
    safe_name = f"{int(time.time())}_{md.filename}"
    md_path = os.path.join(INPUT_DIR, safe_name)
    content = await md.read()
    with open(md_path, "wb") as f:
        f.write(content)
    print(f"    [上传] 保存 Markdown: {md_path}")

    # 创建任务
    task_id = str(uuid.uuid4())[:8]
    task = TaskInfo(
        task_id=task_id,
        filename=md.filename,
        pdf_path=md_path,  # 兼容性：使用 pdf_path 字段存储 md 路径
        created_at=datetime.now().isoformat(),
    )
    with TASKS_LOCK:
        TASKS[task_id] = task

    # 后台线程生成
    thread = threading.Thread(
        target=_run_markdown_generation,
        args=(task_id, md_path, skip_llm),
        daemon=True,
    )
    thread.start()

    return {"task_id": task_id, "filename": md.filename, "status": TaskStatus.PENDING}


@app.get("/api/status/{task_id}")
def get_status(task_id: str):
    """查询任务状态"""
    with TASKS_LOCK:
        task = TASKS.get(task_id)
    if not task:
        raise HTTPException(404, f"任务不存在: {task_id}")
    return task.dict()


@app.get("/api/tasks")
def list_tasks():
    """列出所有任务（最新在前）"""
    with TASKS_LOCK:
        tasks = list(TASKS.values())
    tasks.sort(key=lambda t: t.created_at, reverse=True)
    return [t.dict() for t in tasks]


@app.get("/api/videos")
def list_output_videos():
    """
    扫描 output 目录返回已生成的视频列表。
    用于后端重启后前端追溯历史视频。
    """
    output_dir = OUTPUT_DIR
    videos = []
    if os.path.exists(output_dir):
        for filename in sorted(os.listdir(output_dir), reverse=True):
            if not filename.endswith('.mp4'):
                continue
            filepath = os.path.join(output_dir, filename)
            try:
                stat = os.stat(filepath)
                # 从文件名解析：格式为 YYYYmmdd_xxx.mp4
                name_no_ext = filename[:-4]  # 去挈9 .mp4
                # 取文件名作为 pdf_name（去挈4位日期前缀）
                if len(name_no_ext) > 9 and name_no_ext[8] == '_':
                    pdf_name = name_no_ext[9:]  # 20260320_xxx -> xxx
                else:
                    pdf_name = name_no_ext
                created_ts = stat.st_mtime
                created_at = datetime.fromtimestamp(created_ts).isoformat()
                videos.append({
                    "filename": filename,
                    "pdf_name": pdf_name + ".pdf",
                    "output_path": filepath,
                    "file_size_mb": round(stat.st_size / 1024 / 1024, 1),
                    "created_at": created_at,
                })
            except Exception:
                continue
    return videos


@app.get("/api/video/file")
def stream_video_by_path(path: str, range: Optional[str] = None):
    """按文件路径流式返回视频（用于历史视频播放）"""
    # 安全校验：必须在 output 目录内
    abs_path = os.path.realpath(path)
    abs_output = os.path.realpath(OUTPUT_DIR)
    if not abs_path.startswith(abs_output):
        raise HTTPException(403, "无权访问该文件")
    if not os.path.exists(abs_path):
        raise HTTPException(404, "视频文件不存在")

    file_size = os.path.getsize(abs_path)
    start, end = 0, file_size - 1
    status_code = 200
    if range:
        try:
            range_val = range.replace("bytes=", "")
            parts = range_val.split("-")
            start = int(parts[0]) if parts[0] else 0
            end = int(parts[1]) if parts[1] else file_size - 1
            status_code = 206
        except Exception:
            pass

    chunk_size = 1024 * 1024

    def iterfile():
        remaining = end - start + 1
        with open(abs_path, "rb") as f:
            f.seek(start)
            while remaining > 0:
                data = f.read(min(chunk_size, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data

    filename = os.path.basename(abs_path)
    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
        "Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}",
    }
    return StreamingResponse(
        iterfile(),
        status_code=status_code,
        media_type="video/mp4",
        headers=headers,
    )


@app.get("/api/video/{task_id}")
def stream_video(task_id: str, range: Optional[str] = None):
    """流式返回视频文件，支持 Range 请求（浏览器进度条/快进）"""
    with TASKS_LOCK:
        task = TASKS.get(task_id)
    if not task or task.status != TaskStatus.COMPLETED:
        raise HTTPException(404, "视频尚未生成完成")
    output_path = task.output_path
    if not output_path or not os.path.exists(output_path):
        raise HTTPException(404, "视频文件不存在")

    file_size = os.path.getsize(output_path)

    # 解析 Range 头
    start, end = 0, file_size - 1
    status_code = 200
    if range:
        try:
            range_val = range.replace("bytes=", "")
            parts = range_val.split("-")
            start = int(parts[0]) if parts[0] else 0
            end = int(parts[1]) if parts[1] else file_size - 1
            status_code = 206
        except Exception:
            pass

    chunk_size = min(end - start + 1, 1024 * 1024)  # 1MB chunks

    def iterfile():
        with open(output_path, "rb") as f:
            f.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                data = f.read(min(chunk_size, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data

    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
        "Content-Disposition": f"inline; filename*=UTF-8''{quote(os.path.basename(output_path))}",
    }
    return StreamingResponse(
        iterfile(),
        status_code=status_code,
        media_type="video/mp4",
        headers=headers,
    )


@app.delete("/api/video/file")
def delete_video_file(path: str):
    """删除指定路径的视频文件。
    安全校验：必须在 output 目录内。
    """
    # 安全校验：必须在 output 目录内
    abs_path = os.path.realpath(path)
    abs_output = os.path.realpath(OUTPUT_DIR)
    if not abs_path.startswith(abs_output):
        raise HTTPException(403, "无权访问该文件")
    if not os.path.exists(abs_path):
        raise HTTPException(404, "视频文件不存在")
    
    try:
        os.remove(abs_path)
        return {"success": True, "message": "文件已删除"}
    except Exception as e:
        raise HTTPException(500, f"删除失败: {str(e)}")


@app.post("/api/cancel/{task_id}")
def cancel_task(task_id: str):
    """取消进行中的任务，终止子进程"""
    with TASKS_LOCK:
        task = TASKS.get(task_id)
    if not task:
        raise HTTPException(404, f"任务不存在: {task_id}")
    if task.status not in (TaskStatus.PENDING, TaskStatus.RUNNING):
        raise HTTPException(400, f"任务已结束，无法取消（当前状态: {task.status}）")

    # 终止子进程
    with PROCS_LOCK:
        proc = PROCS.get(task_id)
    if proc:
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        except Exception:
            pass
        with PROCS_LOCK:
            PROCS.pop(task_id, None)

    _update_task(
        task_id,
        status=TaskStatus.FAILED,
        error="已取消",
        completed_at=datetime.now().isoformat(),
    )
    _append_log(task_id, f"[{datetime.now().strftime('%H:%M:%S')}] 任务已被用户取消")
    return {"task_id": task_id, "status": "cancelled"}


@app.get("/api/download/{task_id}")
def download_video(task_id: str):
    """触发浏览器下载"""
    with TASKS_LOCK:
        task = TASKS.get(task_id)
    if not task or task.status != TaskStatus.COMPLETED:
        raise HTTPException(404, "视频尚未生成完成")
    output_path = task.output_path
    if not output_path or not os.path.exists(output_path):
        raise HTTPException(404, "视频文件不存在")

    filename = os.path.basename(output_path)
    file_size = os.path.getsize(output_path)

    def iterfile():
        with open(output_path, "rb") as f:
            while chunk := f.read(1024 * 1024):
                yield chunk

    return StreamingResponse(
        iterfile(),
        media_type="video/mp4",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            "Content-Length": str(file_size),
        },
    )


@app.delete("/api/task/{task_id}")
def cleanup_task(task_id: str):
    """清理任务相关的所有资源：input PDF + temp 临时目录 + output 视频"""
    import shutil
    
    with TASKS_LOCK:
        task = TASKS.get(task_id)
    
    if not task:
        raise HTTPException(404, "任务不存在")
    
    cleaned = []
    errors = []

    # 1. 清理 temp/{pdf_name}/ 目录
    #    pdf_path 格式： input/{timestamp}_{filename}.pdf
    #    main.py 会用 os.path.splitext(os.path.basename(pdf_path))[0] 作为 temp 子目录名
    #    即 temp/{timestamp}_{filename}/，所以这里直接取带时间戳的完整名。
    if task.pdf_path:
        pdf_name = os.path.splitext(os.path.basename(task.pdf_path))[0]
        temp_dir = os.path.join(TEMP_DIR, pdf_name)
        if os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
                cleaned.append(f"temp/{pdf_name}/")
                print(f"    [清理] 已删除临时目录: {temp_dir}")
            except Exception as e:
                errors.append(f"temp/{pdf_name}/: {e}")
                print(f"    [清理] 删除临时目录失败: {e}")
    
    # 2. 清理 input/ 中的 PDF 文件
    if task.pdf_path and os.path.exists(task.pdf_path):
        try:
            os.remove(task.pdf_path)
            cleaned.append(f"input/{os.path.basename(task.pdf_path)}")
            print(f"    [清理] 已删除输入文件: {task.pdf_path}")
        except Exception as e:
            errors.append(f"input/{os.path.basename(task.pdf_path)}: {e}")
            print(f"    [清理] 删除输入文件失败: {e}")

    # 3. 从内存中移除任务
    with TASKS_LOCK:
        TASKS.pop(task_id, None)
    
    return {
        "task_id": task_id,
        "cleaned": cleaned,
        "errors": errors,
        "message": f"已清理 {len(cleaned)} 项资源" + (f"，{len(errors)} 项失败" if errors else "")
    }


# 可配置的 .env key 列表（顺序将在前端展示）
_ENV_KEYS = [
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "LLM_MODEL",
    "QWEN_IMAGE_API_KEY",
]

ENV_PATH = Path(BASE_DIR) / ".env"


def _read_env_file() -> dict:
    """读取 .env 文件，返回 key->value 字典"""
    result = {}
    if not ENV_PATH.exists():
        return result
    with open(ENV_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def _write_env_file(updates: dict):
    """将 updates 写入 .env，保留注释行，将 updates 中的 key 更新或新增"""
    lines = []
    seen_keys = set()

    # 保留原有文件内容，更新就地更新
    if ENV_PATH.exists():
        with open(ENV_PATH, encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and "=" in stripped:
                    key = stripped.partition("=")[0].strip()
                    if key in updates:
                        lines.append(f"{key}={updates[key]}\n")
                        seen_keys.add(key)
                        continue
                lines.append(line if line.endswith("\n") else line + "\n")

    # 新增在 updates 中但原文件不存在的 key
    for key, value in updates.items():
        if key not in seen_keys:
            lines.append(f"{key}={value}\n")

    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.writelines(lines)


@app.get("/api/config")
def get_config():
    """读取当前 .env 配置（隐藏 Key 中间部分）"""
    env = _read_env_file()
    result = {}
    for key in _ENV_KEYS:
        value = env.get(key, "")
        result[key] = value
    return result


@app.post("/api/config")
def save_config(body: Dict[str, str]):
    """保存配置到 .env 文件，并刷新内存中的环境变量"""
    # 只允许更新白名单内的 key
    allowed = {k: v for k, v in body.items() if k in _ENV_KEYS}
    if not allowed:
        raise HTTPException(400, "没有可更新的配置项")
    _write_env_file(allowed)
    # 刷新内存中的环境变量（对当前进程生效）
    for k, v in allowed.items():
        os.environ[k] = v
    return {"ok": True, "updated": list(allowed.keys())}


if __name__ == "__main__":
    import uvicorn
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(TEMP_DIR, exist_ok=True)
    os.makedirs(INPUT_DIR, exist_ok=True)
    uvicorn.run(app, host="0.0.0.0", port=8765, reload=False)


def _run_markdown_generation(task_id: str, md_path: str, skip_llm: bool):
    """在后台线程中运行 Markdown 视频生成流程"""
    try:
        _update_task(task_id, status=TaskStatus.RUNNING, progress=2)
        _append_log(task_id, f"[{datetime.now().strftime('%H:%M:%S')}] 开始处理 Markdown: {os.path.basename(md_path)}")

        # 确定输出路径
        md_name = os.path.splitext(os.path.basename(md_path))[0]
        date_str = datetime.now().strftime("%Y%m%d_%H%M")
        output_path = os.path.join(OUTPUT_DIR, f"{date_str}_{md_name}.mp4")
        _update_task(task_id, output_path=output_path)

        # 构建命令
        cmd = [
            sys.executable,
            os.path.join(BASE_DIR, "main.py"),
            "--input", md_path,
            "--output", output_path,
            "--format", "markdown",
        ]
        if skip_llm:
            cmd.append("--skip-llm")

        # 启动子进程
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=BASE_DIR,
            env={**os.environ, 'PYTHONUNBUFFERED': '1'},
        )
        with PROCS_LOCK:
            PROCS[task_id] = proc

        # 实时读取输出
        import select
        while True:
            ret = proc.poll()
            ready, _, _ = select.select([proc.stdout], [], [], 0.1)
            if proc.stdout in ready:
                line = proc.stdout.readline()
                if line:
                    line = line.rstrip()
                    if line:
                        _append_log(task_id, f"[{datetime.now().strftime('%H:%M:%S')}] {line}")
                        prog = _parse_progress(line)
                        if prog is not None:
                            _update_task(task_id, progress=prog)
            if ret is not None:
                for line in proc.stdout:
                    line = line.rstrip()
                    if line:
                        _append_log(task_id, f"[{datetime.now().strftime('%H:%M:%S')}] {line}")
                        prog = _parse_progress(line)
                        if prog is not None:
                            _update_task(task_id, progress=prog)
                break

        with PROCS_LOCK:
            PROCS.pop(task_id, None)

        with TASKS_LOCK:
            current_status = TASKS.get(task_id)
        if current_status and current_status.status == TaskStatus.FAILED and "已取消" in (current_status.error or ""):
            return

        if proc.returncode == 0 and os.path.exists(output_path):
            file_size = os.path.getsize(output_path) / (1024 * 1024)
            _update_task(
                task_id,
                status=TaskStatus.COMPLETED,
                progress=100,
                completed_at=datetime.now().isoformat(),
                file_size_mb=round(file_size, 1),
            )
            _append_log(task_id, f"[{datetime.now().strftime('%H:%M:%S')}] 视频生成完成！({file_size:.1f} MB)")
        else:
            raise RuntimeError(f"进程退出码: {proc.returncode}")

    except Exception as e:
        with PROCS_LOCK:
            PROCS.pop(task_id, None)
        _update_task(
            task_id,
            status=TaskStatus.FAILED,
            error=str(e),
            completed_at=datetime.now().isoformat(),
        )
        _append_log(task_id, f"[{datetime.now().strftime('%H:%M:%S')}] 错误: {e}")
