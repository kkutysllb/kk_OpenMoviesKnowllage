/**
 * Video Download API
 *
 * GET /api/export-video/download?jobId=xxx - Download exported video
 */

import { type NextRequest, NextResponse } from 'next/server';
import { promises as fs } from 'fs';
import path from 'path';
import { apiError, API_ERROR_CODES } from '@/lib/server/api-response';
import { createLogger } from '@/lib/logger';

const log = createLogger('ExportDownloadAPI');

const EXPORTS_DIR = path.join(process.cwd(), 'data', 'exports');

export async function GET(req: NextRequest) {
  const jobId = req.nextUrl.searchParams.get('jobId');

  if (!jobId) {
    return apiError(API_ERROR_CODES.MISSING_REQUIRED_FIELD, 400, 'Missing jobId parameter');
  }

  // Find the video file in the job directory
  const jobDir = path.join(EXPORTS_DIR, jobId);

  try {
    const files = await fs.readdir(jobDir);
    const videoFile = files.find((f) => f.endsWith('.mp4'));

    if (!videoFile) {
      return apiError(API_ERROR_CODES.INVALID_REQUEST, 404, 'Video file not found');
    }

    const videoPath = path.join(jobDir, videoFile);
    const stat = await fs.stat(videoPath);

    // Read file and return as response
    const fileBuffer = await fs.readFile(videoPath);

    return new NextResponse(fileBuffer, {
      status: 200,
      headers: {
        'Content-Type': 'video/mp4',
        'Content-Length': String(stat.size),
        'Content-Disposition': `attachment; filename="${videoFile}"`,
      },
    });
  } catch (err) {
    log.error('Download error:', err);
    return apiError(API_ERROR_CODES.INVALID_REQUEST, 404, 'Export not found');
  }
}
