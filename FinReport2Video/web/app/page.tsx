'use client'

import { useState, useRef, useCallback, useEffect } from 'react'

// ── 类型定义 ──────────────────────────────────────────────────────────────────

type TaskStatus = 'pending' | 'running' | 'completed' | 'failed'

interface Chapter {
  page_num: number
  title: string
  preview: string
}

interface ParseResult {
  filename: string
  total_pages: number
  chapters: Chapter[]
}

interface Task {
  task_id: string
  filename: string
  pdf_path: string
  output_path?: string
  status: TaskStatus
  progress: number
  log: string
  created_at: string
  completed_at?: string
  error?: string
  file_size_mb?: number
}

interface HistoryVideo {
  filename: string       // mp4 文件名
  pdf_name: string       // 推断的 pdf 文件名
  output_path: string    // 服务器上的绝对路径
  file_size_mb: number
  created_at: string
}

interface Config {
  LLM_API_KEY: string
  LLM_BASE_URL: string
  LLM_MODEL: string
  QWEN_IMAGE_API_KEY: string
}

// ── 工具函数 ──────────────────────────────────────────────────────────────────

const API = (path: string) => `/api/py/${path}`

const statusLabel: Record<TaskStatus, string> = {
  pending: '等待中',
  running: '生成中',
  completed: '已完成',
  failed: '失败',
}

const statusColor: Record<TaskStatus, string> = {
  pending: 'text-yellow-400 bg-yellow-400/10 border-yellow-400/30',
  running: 'text-blue-400 bg-blue-400/10 border-blue-400/30',
  completed: 'text-emerald-400 bg-emerald-400/10 border-emerald-400/30',
  failed: 'text-red-400 bg-red-400/10 border-red-400/30',
}

const formatTime = (iso: string) =>
  new Date(iso).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })

// ── 进度条组件 ────────────────────────────────────────────────────────────────

function ConfigModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [form, setForm] = useState<Config>({
    LLM_API_KEY: '',
    LLM_BASE_URL: '',
    LLM_MODEL: '',
    QWEN_IMAGE_API_KEY: '',
  })
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState<{ type: 'ok' | 'err'; text: string } | null>(null)
  const [showKeys, setShowKeys] = useState<Record<string, boolean>>({})

  useEffect(() => {
    if (!open) return
    setLoading(true)
    setMsg(null)
    fetch(API('config'))
      .then(r => r.json())
      .then(data => setForm(data))
      .catch(() => setMsg({ type: 'err', text: '加载配置失败' }))
      .finally(() => setLoading(false))
  }, [open])

  const handleSave = async () => {
    setSaving(true)
    setMsg(null)
    try {
      const res = await fetch(API('config'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(form),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || '保存失败')
      setMsg({ type: 'ok', text: `已保存 ${data.updated?.length ?? 0} 项配置` })
    } catch (e: unknown) {
      setMsg({ type: 'err', text: e instanceof Error ? e.message : '保存失败' })
    } finally {
      setSaving(false)
    }
  }

  const toggleShow = (key: string) => setShowKeys(prev => ({ ...prev, [key]: !prev[key] }))

  if (!open) return null

  const fields: { key: keyof Config; label: string; placeholder: string; isKey?: boolean }[] = [
    { key: 'LLM_API_KEY',   label: 'LLM API Key',   placeholder: 'sk-...',                    isKey: true },
    { key: 'LLM_BASE_URL',  label: 'LLM Base URL',  placeholder: 'https://api.openai.com/v1' },
    { key: 'LLM_MODEL',     label: 'LLM 模型名',     placeholder: 'gpt-4' },
    { key: 'QWEN_IMAGE_API_KEY', label: '通义万相 API Key',    placeholder: 'sk-...',                    isKey: true },
  ]

  const renderField = ({ key, label, placeholder, isKey }: typeof fields[number]) => (
    <div key={key}>
      <label className="block text-xs text-slate-400 mb-1.5">{label}</label>
      <div className="relative">
        <input
          type={isKey && !showKeys[key] ? 'password' : 'text'}
          value={form[key]}
          onChange={e => setForm(prev => ({ ...prev, [key]: e.target.value }))}
          placeholder={loading ? '加载中...' : placeholder}
          disabled={loading}
          className="w-full bg-slate-800 border border-slate-600/60 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-orange-500/60 disabled:opacity-50 pr-9"
        />
        {isKey && (
          <button type="button" onClick={() => toggleShow(key)}
            className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300">
            {showKeys[key] ? (
              <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94"/>
                <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19"/>
                <line x1="1" y1="1" x2="23" y2="23"/>
              </svg>
            ) : (
              <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>
              </svg>
            )}
          </button>
        )}
      </div>
    </div>
  )

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div className="relative w-full max-w-md bg-slate-900 border border-slate-700/60 rounded-2xl shadow-2xl">
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-700/60">
          <div className="flex items-center gap-2.5">
            <svg className="w-4 h-4 text-orange-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="3"/>
              <path d="M19.07 4.93a10 10 0 0 1 0 14.14M4.93 4.93a10 10 0 0 0 0 14.14"/>
            </svg>
            <span className="font-semibold text-slate-100">模型配置</span>
          </div>
          <button onClick={onClose} className="text-slate-500 hover:text-slate-300 transition-colors">
            <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
            </svg>
          </button>
        </div>
        <div className="px-6 py-5 space-y-5">
          <div>
            <p className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-3">LLM 讲稿生成（DeepSeek）</p>
            <div className="space-y-3">{fields.slice(0, 3).map(renderField)}</div>
          </div>
          <div className="border-t border-slate-700/40" />
          <div>
            <p className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-3">图片生成（通义万相）</p>
            <div className="space-y-3">{fields.slice(3).map(renderField)}</div>
          </div>
          <p className="text-xs text-slate-600">配置将保存到服务端 .env 文件，即时生效无需重启</p>
          {msg && (
            <div className={`text-xs px-3 py-2 rounded-lg border ${
              msg.type === 'ok' ? 'text-emerald-400 bg-emerald-400/10 border-emerald-400/20'
                                : 'text-red-400 bg-red-400/10 border-red-400/20'
            }`}>{msg.text}</div>
          )}
        </div>
        <div className="flex gap-3 px-6 pb-5">
          <button onClick={onClose}
            className="flex-1 py-2 rounded-xl text-sm border border-slate-600/60 text-slate-400 hover:bg-slate-800 transition-colors">
            取消
          </button>
          <button onClick={handleSave} disabled={saving || loading}
            className="flex-1 py-2 rounded-xl text-sm font-medium bg-gradient-to-r from-orange-600 to-orange-500 hover:from-orange-500 hover:to-orange-400 text-white transition-all disabled:opacity-40 disabled:cursor-not-allowed">
            {saving ? (
              <span className="flex items-center justify-center gap-2">
                <svg className="w-3.5 h-3.5 animate-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>
                </svg>
                保存中…
              </span>
            ) : '保存配置'}
          </button>
        </div>
      </div>
    </div>
  )
}

function ProgressBar({ progress, status }: { progress: number; status: TaskStatus }) {
  const isRunning = status === 'running'
  const color =
    status === 'completed' ? 'bg-emerald-500' :
    status === 'failed' ? 'bg-red-500' :
    'bg-orange-500'

  return (
    <div className="w-full bg-slate-700/50 rounded-full h-1.5 overflow-hidden">
      <div
        className={`h-full rounded-full transition-all duration-500 ${color} ${isRunning ? 'relative overflow-hidden' : ''}`}
        style={{ width: `${Math.max(2, progress)}%` }}
      >
        {isRunning && (
          <span className="absolute inset-0 bg-gradient-to-r from-transparent via-white/20 to-transparent animate-shimmer" />
        )}
      </div>
    </div>
  )
}

// ── 章节预览动画组件 ─────────────────────────────────────────────────────────────────────────────────────────

function ChapterPreview({
  result,
  onConfirm,
  onCancel,
}: {
  result: ParseResult
  onConfirm: () => void
  onCancel: () => void
}) {
  const [visibleCount, setVisibleCount] = useState(0)
  const [isComplete, setIsComplete] = useState(false)

  useEffect(() => {
    // 逐个显示动画
    setVisibleCount(0)
    setIsComplete(false)
    const timers: NodeJS.Timeout[] = []
    result.chapters.forEach((_, idx) => {
      timers.push(setTimeout(() => {
        setVisibleCount(prev => prev + 1)
        if (idx === result.chapters.length - 1) {
          setTimeout(() => setIsComplete(true), 300)
        }
      }, idx * 150))  // 每 150ms 显示一个
    })
    return () => timers.forEach(clearTimeout)
  }, [result])

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={onCancel} />
      <div className="relative w-full max-w-2xl max-h-[80vh] bg-slate-900 border border-slate-700/60 rounded-2xl shadow-2xl flex flex-col">
        {/* 头部 */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-700/60">
          <div>
            <h3 className="font-semibold text-slate-100">文档章节预览</h3>
            <p className="text-xs text-slate-500 mt-0.5">{result.filename} · 共 {result.total_pages} 章</p>
          </div>
          <button onClick={onCancel} className="text-slate-500 hover:text-slate-300 transition-colors">
            <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
            </svg>
          </button>
        </div>

        {/* 章节列表 */}
        <div className="flex-1 overflow-y-auto px-6 py-4 space-y-2">
          {result.chapters.map((chapter, idx) => (
            <div
              key={chapter.page_num}
              className={`flex items-start gap-3 p-3 rounded-xl border transition-all duration-300 ${
                idx < visibleCount
                  ? 'opacity-100 translate-y-0 bg-slate-800/60 border-slate-700/40'
                  : 'opacity-0 translate-y-4 pointer-events-none'
              }`}
              style={{ transitionDelay: `${idx * 50}ms` }}
            >
              <div className="w-8 h-8 rounded-lg bg-orange-500/15 border border-orange-500/30 flex items-center justify-center flex-shrink-0">
                <span className="text-xs font-semibold text-orange-400">{chapter.page_num}</span>
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-slate-200 truncate">{chapter.title}</p>
                <p className="text-xs text-slate-500 mt-0.5 line-clamp-1">{chapter.preview}</p>
              </div>
            </div>
          ))}
        </div>

        {/* 底部按钮 */}
        <div className="flex items-center justify-between px-6 py-4 border-t border-slate-700/60">
          <div className="flex items-center gap-2">
            {!isComplete ? (
              <>
                <span className="w-2 h-2 rounded-full bg-orange-500 animate-pulse" />
                <span className="text-xs text-slate-500">解析中...</span>
              </>
            ) : (
              <>
                <svg className="w-4 h-4 text-emerald-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <polyline points="20 6 9 17 4 12"/>
                </svg>
                <span className="text-xs text-emerald-400">已解析完成</span>
              </>
            )}
          </div>
          <div className="flex gap-3">
            <button
              onClick={onCancel}
              className="px-4 py-2 rounded-xl text-sm border border-slate-600/60 text-slate-400 hover:bg-slate-800 transition-colors"
            >
              取消
            </button>
            <button
              onClick={onConfirm}
              disabled={!isComplete}
              className="px-4 py-2 rounded-xl text-sm font-medium bg-gradient-to-r from-orange-600 to-orange-500 hover:from-orange-500 hover:to-orange-400 text-white transition-all disabled:opacity-40 disabled:cursor-not-allowed"
            >
              确认生成视频
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ── 任务卡片组件 ──────────────────────────────────────────────────────────────

function TaskCard({ task, onRefresh, onCancel }: { task: Task; onRefresh: () => void; onCancel: (id: string) => void }) {
  const [showLog, setShowLog] = useState(false)
  const [showPlayer, setShowPlayer] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [cleaning, setCleaning] = useState(false)
  const logRef = useRef<HTMLDivElement>(null)

  // 日志自动滚到底
  useEffect(() => {
    if (showLog && logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight
    }
  }, [task.log, showLog])

  const videoSrc = `/api/py/video/${task.task_id}`
  const downloadUrl = `/api/py/download/${task.task_id}`

  const handleCancel = async () => {
    if (!confirm('确定要停止该任务吗？')) return
    setCancelling(true)
    try {
      await fetch(API(`cancel/${task.task_id}`), { method: 'POST' })
      onCancel(task.task_id)
    } catch {}
    finally { setCancelling(false) }
  }

  const handleCleanup = async () => {
    if (!confirm('确定要清理该任务的所有临时资源吗？\n清理后将无法再次下载视频。')) return
    setCleaning(true)
    try {
      const res = await fetch(API(`task/${task.task_id}`), { method: 'DELETE' })
      if (res.ok) {
        onCancel(task.task_id) // 从列表中移除任务
      }
    } catch {}
    finally { setCleaning(false) }
  }

  return (
    <div className="bg-slate-800/60 border border-slate-700/60 rounded-xl overflow-hidden">
      {/* 卡片头部 */}
      <div className="p-4">
        <div className="flex items-start justify-between gap-3">
          {/* 文件图标 + 名称 */}
          <div className="flex items-center gap-3 min-w-0">
            <div className="w-9 h-9 rounded-lg bg-orange-500/15 border border-orange-500/30 flex items-center justify-center flex-shrink-0">
              <svg className="w-4 h-4 text-orange-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                <polyline points="14 2 14 8 20 8"/>
              </svg>
            </div>
            <div className="min-w-0">
              <p className="text-sm font-medium text-slate-100 truncate">{task.filename}</p>
              <p className="text-xs text-slate-500 mt-0.5">
                {formatTime(task.created_at)} · ID: {task.task_id}
                {task.file_size_mb && ` · ${task.file_size_mb} MB`}
              </p>
            </div>
          </div>
          {/* 状态标签 */}
          <span className={`text-xs px-2.5 py-1 rounded-full border font-medium flex-shrink-0 ${statusColor[task.status]}`}>
            {status === 'running' && (
              <span className="inline-block w-1.5 h-1.5 rounded-full bg-current mr-1.5 animate-pulse" />
            )}
            {statusLabel[task.status]}
          </span>
        </div>

        {/* 进度条 */}
        <div className="mt-3">
          <div className="flex justify-between text-xs text-slate-500 mb-1.5">
            <span>
              {task.status === 'running' ? '处理中...' :
               task.status === 'completed' ? '生成完成' :
               task.status === 'failed' ? (task.error || '生成失败') : '等待开始'}
            </span>
            <span>{task.progress}%</span>
          </div>
          <ProgressBar progress={task.progress} status={task.status} />
        </div>

        {/* 操作按钮 */}
        <div className="flex gap-2 mt-3">
          {task.status === 'completed' && (
            <>
              <button
                onClick={() => setShowPlayer(!showPlayer)}
                className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg bg-orange-500/15 border border-orange-500/30 text-orange-400 hover:bg-orange-500/25 transition-colors"
              >
                <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="currentColor">
                  <polygon points="5 3 19 12 5 21 5 3"/>
                </svg>
                {showPlayer ? '收起播放器' : '播放视频'}
              </button>
              <a
                href={downloadUrl}
                download
                className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg bg-slate-700/50 border border-slate-600/50 text-slate-300 hover:bg-slate-700 transition-colors"
              >
                <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                  <polyline points="7 10 12 15 17 10"/>
                  <line x1="12" y1="15" x2="12" y2="3"/>
                </svg>
                下载
              </a>
              <button
                onClick={handleCleanup}
                disabled={cleaning}
                className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg bg-red-500/15 border border-red-500/30 text-red-400 hover:bg-red-500/25 transition-colors disabled:opacity-50"
              >
                <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <polyline points="3 6 5 6 21 6"/>
                  <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
                </svg>
                {cleaning ? '清理中...' : '清理资源'}
              </button>
            </>
          )}
          {(task.status === 'running' || task.status === 'pending') && (
            <>
              <button
                onClick={onRefresh}
                className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg bg-slate-700/50 border border-slate-600/50 text-slate-400 hover:bg-slate-700 transition-colors"
              >
                <svg className="w-3.5 h-3.5 animate-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <polyline points="23 4 23 10 17 10"/>
                  <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>
                </svg>
                刷新
              </button>
              <button
                onClick={handleCancel}
                disabled={cancelling}
                className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg bg-red-500/10 border border-red-500/30 text-red-400 hover:bg-red-500/20 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              >
                <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <rect x="3" y="3" width="18" height="18" rx="2"/>
                  <line x1="9" y1="9" x2="15" y2="15"/><line x1="15" y1="9" x2="9" y2="15"/>
                </svg>
                {cancelling ? '停止中…' : '停止任务'}
              </button>
            </>
          )}
          <button
            onClick={() => setShowLog(!showLog)}
            className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg bg-slate-700/50 border border-slate-600/50 text-slate-400 hover:bg-slate-700 transition-colors ml-auto"
          >
            <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>
            </svg>
            {showLog ? '收起日志' : '查看日志'}
          </button>
        </div>
      </div>

      {/* 视频播放器 */}
      {showPlayer && task.status === 'completed' && (
        <div className="border-t border-slate-700/60 bg-black">
          <video
            key={videoSrc}
            src={videoSrc}
            controls
            className="w-full max-h-[500px] bg-black"
            preload="metadata"
          />
        </div>
      )}

      {/* 日志面板 */}
      {showLog && (
        <div className="border-t border-slate-700/60">
          <div
            ref={logRef}
            className="p-3 text-xs font-mono text-slate-400 bg-slate-900/60 max-h-48 overflow-y-auto whitespace-pre-wrap leading-relaxed"
          >
            {task.log || '（暂无日志）'}
          </div>
        </div>
      )}
    </div>
  )
}

// ── 历史视频卡片（后端重启后从文件系统恢复）────────────────────────────────

function HistoryVideoCard({ video, onDelete }: { video: HistoryVideo; onDelete: (output_path: string) => void }) {
  const [showPlayer, setShowPlayer] = useState(false)
  const [deleting, setDeleting] = useState(false)
  // 通过 /api/py/video/file?path=... 播放
  const videoSrc = `/api/py/video/file?path=${encodeURIComponent(video.output_path)}`
  const downloadUrl = videoSrc  // 直接下载同一地址

  const handleDelete = async () => {
    if (!confirm(`确定要删除该视频吗？\n${video.pdf_name}\n此操作不可恢复。`)) return
    setDeleting(true)
    try {
      const res = await fetch(API(`video/file?path=${encodeURIComponent(video.output_path)}`), {
        method: 'DELETE'
      })
      if (res.ok) {
        onDelete(video.output_path)
      } else {
        alert('删除失败，请重试')
      }
    } catch {
      alert('删除失败，请检查网络')
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className="bg-slate-800/40 border border-slate-700/40 rounded-xl overflow-hidden">
      <div className="p-4">
        <div className="flex items-start justify-between gap-3">
          {/* 文件图标 + 名称 */}
          <div className="flex items-center gap-3 min-w-0">
            <div className="w-9 h-9 rounded-lg bg-slate-700/50 border border-slate-600/40 flex items-center justify-center flex-shrink-0">
              <svg className="w-4 h-4 text-slate-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <polygon points="23 7 16 12 23 17 23 7"/>
                <rect x="1" y="5" width="15" height="14" rx="2" ry="2"/>
              </svg>
            </div>
            <div className="min-w-0">
              <p className="text-sm font-medium text-slate-300 truncate">{video.pdf_name}</p>
              <p className="text-xs text-slate-500 mt-0.5">
                {formatTime(video.created_at)} · {video.file_size_mb} MB
              </p>
            </div>
          </div>
          {/* 标记为历史视频 */}
          <span className="text-xs px-2.5 py-1 rounded-full border font-medium flex-shrink-0 text-slate-400 bg-slate-700/40 border-slate-600/40">
            历史视频
          </span>
        </div>

        {/* 操作按钮 */}
        <div className="flex gap-2 mt-3">
          <button
            onClick={() => setShowPlayer(!showPlayer)}
            className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg bg-orange-500/15 border border-orange-500/30 text-orange-400 hover:bg-orange-500/25 transition-colors"
          >
            <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="currentColor">
              <polygon points="5 3 19 12 5 21 5 3"/>
            </svg>
            {showPlayer ? '收起播放器' : '播放视频'}
          </button>
          <a
            href={downloadUrl}
            download={video.filename}
            className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg bg-slate-700/50 border border-slate-600/50 text-slate-300 hover:bg-slate-700 transition-colors"
          >
            <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
              <polyline points="7 10 12 15 17 10"/>
              <line x1="12" y1="15" x2="12" y2="3"/>
            </svg>
            下载
          </a>
          <button
            onClick={handleDelete}
            disabled={deleting}
            className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg bg-red-500/15 border border-red-500/30 text-red-400 hover:bg-red-500/25 transition-colors disabled:opacity-50 ml-auto"
          >
            <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <polyline points="3 6 5 6 21 6"/>
              <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
            </svg>
            {deleting ? '删除中...' : '删除'}
          </button>
        </div>
      </div>

      {/* 视频播放器 */}
      {showPlayer && (
        <div className="border-t border-slate-700/60 bg-black">
          <video
            key={videoSrc}
            src={videoSrc}
            controls
            className="w-full max-h-[500px] bg-black"
            preload="metadata"
          />
        </div>
      )}
    </div>
  )
}

// ── 历史视频区块（带折叠）──────────────────────────────────────────────────────────

const HISTORY_PREVIEW_COUNT = 1  // 默认展示条数

function HistoryVideoSection({ videos, onDelete }: { videos: HistoryVideo[]; onDelete: (path: string) => void }) {
  const [expanded, setExpanded] = useState(false)
  const shown = expanded ? videos : videos.slice(0, HISTORY_PREVIEW_COUNT)
  const hiddenCount = videos.length - HISTORY_PREVIEW_COUNT

  return (
    <section>
      {/* 标题行 */}
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-medium text-slate-400 uppercase tracking-wider">历史视频</h2>
        {videos.length > HISTORY_PREVIEW_COUNT && (
          <button
            onClick={() => setExpanded(!expanded)}
            className="flex items-center gap-1 text-xs text-slate-500 hover:text-slate-300 transition-colors"
          >
            {expanded ? (
              <>
                <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <polyline points="18 15 12 9 6 15"/>
                </svg>
                收起
              </>
            ) : (
              <>
                <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <polyline points="6 9 12 15 18 9"/>
                </svg>
                展开全部 {videos.length} 条
              </>
            )}
          </button>
        )}
      </div>

      {/* 卡片列表 */}
      <div className="space-y-3">
        {shown.map(video => (
          <HistoryVideoCard key={video.output_path} video={video} onDelete={onDelete} />
        ))}
      </div>

      {/* 未展开时显示提示 */}
      {!expanded && hiddenCount > 0 && (
        <button
          onClick={() => setExpanded(true)}
          className="mt-2 w-full py-2 text-xs text-slate-500 hover:text-slate-300 border border-dashed border-slate-700/60 hover:border-slate-600 rounded-xl transition-colors"
        >
          还有 {hiddenCount} 条历史视频，点击展开
        </button>
      )}
    </section>
  )
}

// ── 上传区组件 ────────────────────────────────────────────────────────────────

function UploadZone({ onSubmit, uploading }: {
  onSubmit: (file: File, skipLlm: boolean, pages: string) => void
  uploading: boolean
}) {
  const [dragging, setDragging] = useState(false)
  const [file, setFile] = useState<File | null>(null)
  const [skipLlm, setSkipLlm] = useState(false)
  const [pages, setPages] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
    const f = e.dataTransfer.files[0]
    if (f && (f.name.endsWith('.pdf') || f.name.endsWith('.md'))) setFile(f)
  }, [])

  const handleFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (f) setFile(f)
  }

  const handleSubmit = () => {
    if (!file) return
    onSubmit(file, skipLlm, pages)
    setFile(null)
    if (inputRef.current) inputRef.current.value = ''
  }

  return (
    <div className="space-y-4">
      {/* Drop Zone */}
      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
        onClick={() => !file && inputRef.current?.click()}
        className={`relative border-2 border-dashed rounded-xl p-8 text-center transition-all cursor-pointer
          ${dragging ? 'border-orange-500 bg-orange-500/5' : 'border-slate-600/60 hover:border-slate-500 hover:bg-slate-800/40'}
          ${file ? 'cursor-default' : ''}`}
      >
        <input
          ref={inputRef}
          type="file"
          accept=".pdf,.md"
          className="hidden"
          onChange={handleFile}
        />
        {file ? (
          <div className="flex items-center justify-center gap-3">
            <div className="w-10 h-10 rounded-lg bg-orange-500/15 border border-orange-500/30 flex items-center justify-center">
              <svg className="w-5 h-5 text-orange-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                <polyline points="14 2 14 8 20 8"/>
              </svg>
            </div>
            <div className="text-left">
              <p className="text-sm font-medium text-slate-100">{file.name}</p>
              <p className="text-xs text-slate-500">{(file.size / 1024).toFixed(1)} KB</p>
            </div>
            <button
              onClick={(e) => { e.stopPropagation(); setFile(null) }}
              className="ml-auto text-slate-500 hover:text-slate-300 transition-colors"
            >
              <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
              </svg>
            </button>
          </div>
        ) : (
          <div>
            <div className="w-12 h-12 rounded-full bg-slate-700/60 flex items-center justify-center mx-auto mb-3">
              <svg className="w-6 h-6 text-slate-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                <polyline points="17 8 12 3 7 8"/>
                <line x1="12" y1="3" x2="12" y2="15"/>
              </svg>
            </div>
            <p className="text-sm text-slate-300 font-medium">拖拽 PDF 或 Markdown 到此处，或点击选择</p>
            <p className="text-xs text-slate-500 mt-1">支持金融研报、分析报告等 PDF 或 Markdown 文件</p>
          </div>
        )}
      </div>

      {/* 选项 */}
      <div className="flex flex-wrap gap-4 items-center">
        <label className="flex items-center gap-2 cursor-pointer select-none">
          <div
            onClick={() => setSkipLlm(!skipLlm)}
            className={`w-8 h-4.5 rounded-full transition-colors relative ${skipLlm ? 'bg-orange-500' : 'bg-slate-600'}`}
          >
            <span className={`absolute top-0.5 w-3.5 h-3.5 bg-white rounded-full shadow transition-transform ${skipLlm ? 'translate-x-3.5' : 'translate-x-0.5'}`} />
          </div>
          <span className="text-sm text-slate-400">快速模式（跳过 LLM 润色）</span>
        </label>
        <div className="flex items-center gap-2">
          <span className="text-sm text-slate-500">指定页码:</span>
          <input
            type="text"
            placeholder="如 1-5 或留空"
            value={pages}
            onChange={(e) => setPages(e.target.value)}
            className="text-sm bg-slate-800 border border-slate-600/60 rounded-lg px-3 py-1.5 text-slate-300 placeholder-slate-600 w-28 focus:outline-none focus:border-orange-500/50"
          />
        </div>
      </div>

      {/* 提交按钮 */}
      <button
        onClick={handleSubmit}
        disabled={!file || uploading}
        className="w-full py-2.5 rounded-xl font-medium text-sm transition-all
          bg-gradient-to-r from-orange-600 to-orange-500 hover:from-orange-500 hover:to-orange-400
          text-white shadow-lg shadow-orange-900/20
          disabled:opacity-40 disabled:cursor-not-allowed disabled:shadow-none"
      >
        {uploading ? (
          <span className="flex items-center justify-center gap-2">
            <svg className="w-4 h-4 animate-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <polyline points="23 4 23 10 17 10"/>
              <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>
            </svg>
            上传中…
          </span>
        ) : '开始生成视频'}
      </button>
    </div>
  )
}

// ── 主页面 ────────────────────────────────────────────────────────────────────

export default function Home() {
  const [tasks, setTasks] = useState<Task[]>([])
  const [historyVideos, setHistoryVideos] = useState<HistoryVideo[]>([])
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [configOpen, setConfigOpen] = useState(false)
  const [parseResult, setParseResult] = useState<ParseResult | null>(null)
  const [pendingFile, setPendingFile] = useState<{ file: File; skipLlm: boolean; pages: string } | null>(null)
  const pollingRef = useRef<NodeJS.Timeout | null>(null)
  const initialLoadRef = useRef(false)

  // 刷新任务列表
  const refreshTasks = useCallback(async () => {
    try {
      const res = await fetch(API('tasks'))
      if (!res.ok) return
      const data: Task[] = await res.json()
      setTasks(data)
    } catch {}
  }, [])

  // 加载历史视频（output 目录下已生成的）
  const loadHistoryVideos = useCallback(async () => {
    try {
      const res = await fetch(API('videos'))
      if (!res.ok) return
      const data: HistoryVideo[] = await res.json()
      setHistoryVideos(data)
    } catch {}
  }, [])

  // 首次加载
  useEffect(() => {
    if (!initialLoadRef.current) {
      initialLoadRef.current = true
      refreshTasks()
      loadHistoryVideos()
    }
  }, [refreshTasks, loadHistoryVideos])

  // 定时轮询（只有活跃任务时才轮询）
  useEffect(() => {
    const hasActive = tasks.some(t => t.status === 'running' || t.status === 'pending')
    
    // 有活跃任务时才启动轮询
    if (hasActive) {
      pollingRef.current = setInterval(() => {
        refreshTasks()
      }, 1000)
    }
    
    return () => {
      if (pollingRef.current) clearInterval(pollingRef.current)
    }
  }, [refreshTasks, tasks])

  // 第一步：上传并解析 PDF 章节
  const handleSubmit = async (file: File, skipLlm: boolean, pages: string) => {
    setUploading(true)
    setError(null)
    try {
      const form = new FormData()
      form.append('file', file)
      if (pages) form.append('pages', pages)

      const res = await fetch(API('parse'), { method: 'POST', body: form })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || '解析失败')

      // 保存结果和参数，展示预览弹窗
      setParseResult(data)
      setPendingFile({ file, skipLlm, pages })
    } catch (e: any) {
      setError(e.message)
    } finally {
      setUploading(false)
    }
  }

  // 第二步：确认后提交生成任务
  const handleConfirmGenerate = async () => {
    if (!pendingFile) return
    setParseResult(null)
    setUploading(true)
    try {
      const { file, skipLlm, pages } = pendingFile
      const form = new FormData()
      form.append('file', file)
      form.append('skip_llm', String(skipLlm))
      if (pages) form.append('pages', pages)

      const res = await fetch(API('generate'), { method: 'POST', body: form })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || '提交失败')

      await refreshTasks()
    } catch (e: any) {
      setError(e.message)
    } finally {
      setUploading(false)
      setPendingFile(null)
    }
  }

  // 取消预览
  const handleCancelPreview = () => {
    setParseResult(null)
    setPendingFile(null)
  }

  const activeTasks = tasks.filter(t => t.status === 'running' || t.status === 'pending')
  const doneTasks = tasks.filter(t => t.status === 'completed' || t.status === 'failed')

  // 历史视频：过滤掉已在 tasks 中存在的（避免重复展示）
  const taskOutputPaths = new Set(tasks.map(t => t.output_path).filter(Boolean))
  const orphanVideos = historyVideos.filter(v => !taskOutputPaths.has(v.output_path))

  // 删除历史视频（仅从前端列表移除，实际文件由后端删除）
  const handleDeleteHistoryVideo = useCallback((outputPath: string) => {
    setHistoryVideos(prev => prev.filter(v => v.output_path !== outputPath))
  }, [])

  // 取消任务后刷新列表
  const handleCancel = useCallback(async (_taskId: string) => {
    await refreshTasks()
  }, [refreshTasks])

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <ConfigModal open={configOpen} onClose={() => setConfigOpen(false)} />
      {parseResult && (
        <ChapterPreview
          result={parseResult}
          onConfirm={handleConfirmGenerate}
          onCancel={handleCancelPreview}
        />
      )}
      {/* 顶部导航 */}
      <header className="border-b border-slate-800/60 bg-slate-900/80 backdrop-blur sticky top-0 z-10">
        <div className="max-w-4xl mx-auto px-4 h-14 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-orange-500 to-orange-600 flex items-center justify-center">
              <svg className="w-4 h-4 text-white" viewBox="0 0 24 24" fill="currentColor">
                <polygon points="23 7 16 12 23 17 23 7"/>
                <rect x="1" y="5" width="15" height="14" rx="2" ry="2"/>
              </svg>
            </div>
            <span className="font-bold text-base tracking-tight">FinReport<span className="text-orange-400">2Video</span></span>
          </div>
          <div className="flex items-center gap-2">
            {activeTasks.length > 0 && (
              <span className="text-xs text-blue-400 bg-blue-400/10 border border-blue-400/30 px-2 py-0.5 rounded-full">
                {activeTasks.length} 个任务进行中
              </span>
            )}
            <button
              onClick={() => setConfigOpen(true)}
              title="模型配置"
              className="w-8 h-8 flex items-center justify-center rounded-lg text-slate-400 hover:text-slate-200 hover:bg-slate-800 transition-colors"
            >
              <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="3"/>
                <path d="M19.07 4.93a10 10 0 0 1 0 14.14M4.93 4.93a10 10 0 0 0 0 14.14"/>
                <path d="M12 2v2m0 16v2M2 12h2m16 0h2"/>
              </svg>
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-4xl mx-auto px-4 py-8 space-y-8">
        {/* 上传区块 */}
        <section>
          <div className="mb-4">
            <h2 className="text-lg font-semibold text-slate-100">上传金融报告</h2>
            <p className="text-sm text-slate-500 mt-0.5">支持 PDF 或 Markdown 格式，自动解析并生成带语音讲解的视频</p>
          </div>
          <div className="bg-slate-800/40 border border-slate-700/60 rounded-2xl p-6">
            <UploadZone onSubmit={handleSubmit} uploading={uploading} />
          </div>
          {error && (
            <div className="mt-3 text-sm text-red-400 bg-red-400/10 border border-red-400/20 rounded-lg px-4 py-2.5">
              {error}
            </div>
          )}
        </section>

        {/* 进行中任务 */}
        {activeTasks.length > 0 && (
          <section>
            <h2 className="text-sm font-medium text-slate-400 uppercase tracking-wider mb-3">进行中</h2>
            <div className="space-y-3">
              {activeTasks.map(task => (
                <TaskCard key={task.task_id} task={task} onRefresh={refreshTasks} onCancel={handleCancel} />
              ))}
            </div>
          </section>
        )}

        {/* 已完成任务 */}
        {doneTasks.length > 0 && (
          <section>
            <h2 className="text-sm font-medium text-slate-400 uppercase tracking-wider mb-3">历史记录</h2>
            <div className="space-y-3">
              {doneTasks.map(task => (
                <TaskCard key={task.task_id} task={task} onRefresh={refreshTasks} onCancel={handleCancel} />
              ))}
            </div>
          </section>
        )}

        {/* 历史视频（后端重启后从 output 目录恢复） */}
        {orphanVideos.length > 0 && (
          <HistoryVideoSection videos={orphanVideos} onDelete={handleDeleteHistoryVideo} />
        )}

        {/* 空状态 */}
        {tasks.length === 0 && orphanVideos.length === 0 && !uploading && (
          <div className="text-center py-16 text-slate-600">
            <svg className="w-12 h-12 mx-auto mb-3 opacity-40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <polygon points="23 7 16 12 23 17 23 7"/>
              <rect x="1" y="5" width="15" height="14" rx="2" ry="2"/>
            </svg>
            <p className="text-sm">上传 PDF 后，生成记录将在此显示</p>
          </div>
        )}
      </main>
    </div>
  )
}
