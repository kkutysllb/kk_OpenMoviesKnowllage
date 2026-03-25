/**
 * Video Export API
 *
 * POST /api/export-video - Start export job
 * GET /api/export-video?jobId=xxx - Get job status
 */

import { type NextRequest, NextResponse } from 'next/server';
import { nanoid } from 'nanoid';
import { promises as fs } from 'fs';
import path from 'path';
import { readClassroom, CLASSROOMS_DIR } from '@/lib/server/classroom-storage';
import {
  buildTimeline,
  buildSimpleTimeline,
  buildAudioTrack,
  getAudioDurationsMap,
  exportVideo,
  isFFmpegAvailable,
  type ExportOptions,
  type ExportJob,
  type ExportProgress,
  type AudioSegment,
} from '@/lib/export';
import { apiError, apiSuccess, API_ERROR_CODES } from '@/lib/server/api-response';
import { createLogger } from '@/lib/logger';

const log = createLogger('ExportVideoAPI');

// In-memory job storage (for demo; use Redis in production)
const exportJobs = new Map<string, ExportJob>();

// Export directory
const EXPORTS_DIR = path.join(process.cwd(), 'data', 'exports');

/**
 * Ensure exports directory exists
 */
async function ensureExportsDir() {
  await fs.mkdir(EXPORTS_DIR, { recursive: true });
}

/**
 * GET - Get export job status
 */
export async function GET(req: NextRequest) {
  const jobId = req.nextUrl.searchParams.get('jobId');

  if (!jobId) {
    return apiError(API_ERROR_CODES.MISSING_REQUIRED_FIELD, 400, 'Missing jobId parameter');
  }

  const job = exportJobs.get(jobId);
  if (!job) {
    return apiError(API_ERROR_CODES.INVALID_REQUEST, 404, 'Job not found');
  }

  return apiSuccess({ job });
}

/**
 * POST - Start export job
 */
export async function POST(req: NextRequest) {
  try {
    // Check FFmpeg availability
    if (!isFFmpegAvailable()) {
      return apiError(API_ERROR_CODES.INTERNAL_ERROR, 500, 'FFmpeg is not installed. Please install FFmpeg to export videos.');
    }

    const body = await req.json();
    const { classroomId, options = {} } = body as {
      classroomId: string;
      options?: Partial<ExportOptions>;
    };

    if (!classroomId) {
      return apiError(API_ERROR_CODES.MISSING_REQUIRED_FIELD, 400, 'Missing classroomId');
    }

    // Load classroom data
    const classroom = await readClassroom(classroomId);
    if (!classroom) {
      return apiError(API_ERROR_CODES.INVALID_REQUEST, 404, 'Classroom not found');
    }

    // Create job
    const jobId = nanoid();
    const job: ExportJob = {
      id: jobId,
      classroomId,
      status: 'pending',
      progress: 0,
      currentStep: 'initializing',
      createdAt: Date.now(),
      updatedAt: Date.now(),
      options: {
        fps: 30,
        quality: 'medium',
        resolution: '1080p',
        includeAudio: true,
        transitionDuration: 0,
        ...options,
      },
    };

    exportJobs.set(jobId, job);

    // Start export in background
    const baseUrl = req.headers.get('x-forwarded-host')
      ? `${req.headers.get('x-forwarded-proto') || 'http'}://${req.headers.get('x-forwarded-host')}`
      : req.nextUrl.origin;

    // Run export asynchronously
    runExportJob(job, classroom, baseUrl).catch((err) => {
      log.error(`Export job ${jobId} failed:`, err);
      job.status = 'failed';
      job.error = err instanceof Error ? err.message : 'Unknown error';
      job.updatedAt = Date.now();
      exportJobs.set(jobId, job);
    });

    return apiSuccess({ jobId, status: 'pending' });
  } catch (err) {
    log.error('Export video error:', err);
    return apiError(
      API_ERROR_CODES.INTERNAL_ERROR,
      500,
      err instanceof Error ? err.message : 'Failed to start export',
    );
  }
}

/**
 * Run export job in background
 */
async function runExportJob(
  job: ExportJob,
  classroom: { id: string; stage: any; scenes: any[] },
  baseUrl: string,
): Promise<void> {
  const jobId = job.id;
  const updateJob = (updates: Partial<ExportJob>) => {
    Object.assign(job, updates, { updatedAt: Date.now() });
    exportJobs.set(jobId, job);
  };

  try {
    updateJob({ status: 'processing', currentStep: 'initializing' });
    await ensureExportsDir();

    const jobDir = path.join(EXPORTS_DIR, jobId);
    const tempDir = path.join(jobDir, 'temp');
    await fs.mkdir(jobDir, { recursive: true });
    await fs.mkdir(tempDir, { recursive: true });

    // Progress callback
    const onProgress = (progress: ExportProgress) => {
      updateJob({
        progress: progress.progress,
        currentStep: progress.step,
      });
    };

    // Step 1: Collect audio first to get real durations
    let audioPath: string | null = null;
    let audioDurations: Map<string, number> | undefined;
    let audioSegments: AudioSegment[] = [];

    if (job.options.includeAudio) {
      onProgress({ step: 'collecting_audio', progress: 5, message: 'Collecting audio...' });
      try {
        // Build merged audio track (also provides real per-segment durations)
        const audioResult = await buildAudioTrack(
          classroom.scenes,
          tempDir,
          jobDir,
          baseUrl,
        );
        audioPath = audioResult.audioPath;
        audioSegments = audioResult.track.segments;

        // Build audioId -> duration map from actual segments
        audioDurations = new Map<string, number>();
        for (const seg of audioResult.track.segments) {
          if (seg.audioId) {
            audioDurations.set(seg.audioId, seg.duration);
          }
        }
        log.info(`Got audio durations for ${audioDurations.size} segments`);
      } catch (err) {
        log.warn('Failed to collect audio, continuing without audio:', err);
      }
    }

    // Step 2: Build timeline using real audio durations (so slides match audio)
    onProgress({ step: 'initializing', progress: 15, message: 'Building timeline...' });
    const timeline = buildTimeline(classroom.stage, classroom.scenes, audioDurations);

    // Step 3: Export video
    onProgress({ step: 'encoding_video', progress: 20, message: 'Encoding video...' });
    const videoOutput = await exportVideo(
      timeline,
      jobDir,
      audioPath,
      audioSegments,
      job.options,
      onProgress,
    );

    // Step 4: Complete
    updateJob({
      status: 'completed',
      progress: 100,
      currentStep: 'finalizing',
      outputPath: videoOutput.path,
      outputUrl: `${baseUrl}/api/export-video/download?jobId=${jobId}`,
    });

    log.info(`Export job ${jobId} completed: ${videoOutput.filename}`);
  } catch (err) {
    log.error(`Export job ${jobId} failed:`, err);
    updateJob({
      status: 'failed',
      error: err instanceof Error ? err.message : 'Unknown error',
    });
    throw err;
  }
}
