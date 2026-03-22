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
    """从日志行解析进度（0-100）"""
    if "Step 1/5" in line:
        return 5
    if "Step 1.5/5" in line:
        return 10
    if "处理第" in line and "/8" in line:
        try:
            part = line.split("（")[1].split("）")[0]
            cur, total = part.split("/")
            return 10 + int(int(cur) / int(total) * 75)
        except Exception:
            return None
    if "Step 3/5" in line:
        return 88
    if "Step 4/5" in line or "完成" in line and "输出文件" in line:
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
        )
        # 注册子进程，供取消接口使用
        with PROCS_LOCK:
            PROCS[task_id] = proc

        for line in proc.stdout:
            line = line.rstrip()
            if line:
                _append_log(task_id, f"[{datetime.now().strftime('%H:%M:%S')}] {line}")
                prog = _parse_progress(line)
                if prog is not None:
                    _update_task(task_id, progress=prog)

        proc.wait()
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


@app.post("/api/generate")
async def generate_video(
    pdf: UploadFile = File(...),
    skip_llm: bool = Form(False),
    pages: Optional[str] = Form(None),
):
    """上传 PDF 并启动视频生成任务"""
    if not pdf.filename.endswith(".pdf"):
        raise HTTPException(400, "只支持 PDF 文件")

    # 保存上传文件到 input/
    os.makedirs(INPUT_DIR, exist_ok=True)
    safe_name = f"{int(time.time())}_{pdf.filename}"
    pdf_path = os.path.join(INPUT_DIR, safe_name)
    content = await pdf.read()
    with open(pdf_path, "wb") as f:
        f.write(content)

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
        "Content-Disposition": f'inline; filename="{os.path.basename(output_path)}"',
    }
    return StreamingResponse(
        iterfile(),
        status_code=status_code,
        media_type="video/mp4",
        headers=headers,
    )


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
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(file_size),
        },
    )


# 可配置的 .env key 列表（顺序将在前端展示）
_ENV_KEYS = [
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_MODEL",
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
