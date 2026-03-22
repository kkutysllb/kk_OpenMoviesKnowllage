/**
 * 反向代理：将 /api/py/** 请求转发到 Python FastAPI 后端 (localhost:8765)
 * 避免浏览器跨域问题，同时支持文件上传（multipart）和视频流（Range）
 */
import { NextRequest, NextResponse } from 'next/server'

const PYTHON_API_URL = process.env.PYTHON_API_URL || 'http://localhost:8765'

async function proxy(req: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  const { path } = await params
  const targetPath = '/api/' + path.join('/')
  const targetUrl = PYTHON_API_URL + targetPath + (req.nextUrl.search || '')

  // 转发 Headers（保留 Range、Content-Type 等）
  const headers = new Headers()
  req.headers.forEach((value, key) => {
    // 排除 Next.js 内部 header
    if (!['host', 'connection', 'transfer-encoding'].includes(key.toLowerCase())) {
      headers.set(key, value)
    }
  })

  let body: BodyInit | null = null
  if (req.method !== 'GET' && req.method !== 'HEAD') {
    body = await req.arrayBuffer()
  }

  const upstreamRes = await fetch(targetUrl, {
    method: req.method,
    headers,
    body,
    // @ts-ignore — Node.js fetch 支持 duplex
    duplex: 'half',
  })

  // 转发响应 headers
  const resHeaders = new Headers()
  upstreamRes.headers.forEach((value, key) => {
    if (!['transfer-encoding', 'connection'].includes(key.toLowerCase())) {
      resHeaders.set(key, value)
    }
  })

  return new NextResponse(upstreamRes.body, {
    status: upstreamRes.status,
    headers: resHeaders,
  })
}

export const GET = proxy
export const POST = proxy
export const PUT = proxy
export const DELETE = proxy
export const PATCH = proxy
export const HEAD = proxy

// App Router: 使用 route segment config 替代旧版 config
export const dynamic = 'force-dynamic'
export const runtime = 'nodejs'
