/**
 * Video Encoder - Encodes slides and audio into MP4 video
 *
 * Uses FFmpeg to combine slide images and audio into a video file.
 */

import { promises as fs } from 'fs';
import path from 'path';
import { spawn, spawnSync } from 'child_process';
import type { Slide } from '@/lib/types/slides';
import type {
  ExportOptions,
  VideoTimeline,
  SceneTimeline,
  VideoOutput,
  ExportProgress,
  AudioSegment,
} from './types';
import { DEFAULT_EXPORT_OPTIONS, RESOLUTION_DIMENSIONS, QUALITY_PRESETS } from './types';
import { renderSlideToPng } from './slide-renderer';
import { createLogger } from '@/lib/logger';

const log = createLogger('VideoEncoder');

// Path to FFmpeg binary (use custom build with libass support if available)
const FFMPEG_PATH = process.env.FFMPEG_PATH || 'ffmpeg';

/**
 * Check if FFmpeg is available
 */
export function isFFmpegAvailable(): boolean {
  try {
    const result = spawnSync(FFMPEG_PATH, ['-version'], { encoding: 'utf-8' });
    return result.status === 0;
  } catch {
    return false;
  }
}

/**
 * Render all slides to images
 */
async function renderSlidesToImages(
  timeline: VideoTimeline,
  outputDir: string,
  options: ExportOptions,
  onProgress?: (progress: ExportProgress) => void,
): Promise<string[]> {
  const { resolution } = options;
  const imagePaths: string[] = [];

  for (let i = 0; i < timeline.scenes.length; i++) {
    const scene = timeline.scenes[i];

    onProgress?.({
      step: 'rendering_slides',
      progress: Math.round((i / timeline.scenes.length) * 100),
      message: `Rendering slide ${i + 1}/${timeline.scenes.length}`,
      currentScene: i + 1,
      totalScenes: timeline.scenes.length,
    });

    const imagePath = path.join(outputDir, `slide_${String(i).padStart(4, '0')}.png`);
    const pngBuffer = await renderSlideToPng(scene.slide, resolution);
    await fs.writeFile(imagePath, pngBuffer);
    imagePaths.push(imagePath);

    log.debug(`Rendered slide ${i + 1} to ${imagePath}`);
  }

  return imagePaths;
}

/**
 * Create FFmpeg concat file for slides
 */
async function createConcatFile(
  scenes: SceneTimeline[],
  imagePaths: string[],
  concatFilePath: string,
): Promise<void> {
  const lines: string[] = [];

  for (let i = 0; i < scenes.length; i++) {
    const scene = scenes[i];
    const imagePath = imagePaths[i];
    const duration = scene.duration / 1000; // Convert ms to seconds

    lines.push(`file '${imagePath}'`);
    lines.push(`duration ${duration}`);
  }

  // Add last image again (FFmpeg concat requirement)
  if (imagePaths.length > 0) {
    lines.push(`file '${imagePaths[imagePaths.length - 1]}'`);
  }

  await fs.writeFile(concatFilePath, lines.join('\n'));
}

/**
 * Generate SRT subtitle content from audio segments
 * Format: startTime and duration are in milliseconds
 */
function generateSRT(segments: AudioSegment[]): string {
  const lines: string[] = [];
  let index = 1;

  for (const seg of segments) {
    if (!seg.text?.trim()) continue;

    const startMs = seg.startTime;
    const endMs = seg.startTime + seg.duration;

    // Convert to SRT time format: HH:MM:SS,mmm
    const start = formatSRTTime(startMs);
    const end = formatSRTTime(endMs);

    lines.push(String(index));
    lines.push(`${start} --> ${end}`);
    lines.push(seg.text.trim());
    lines.push('');
    index++;
  }

  return lines.join('\n');
}

/**
 * Format milliseconds to SRT time: HH:MM:SS,mmm
 */
function formatSRTTime(ms: number): string {
  const hours = Math.floor(ms / 3600000);
  const minutes = Math.floor((ms % 3600000) / 60000);
  const seconds = Math.floor((ms % 60000) / 1000);
  const millis = Math.floor(ms % 1000);

  return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')},${String(millis).padStart(3, '0')}`;
}

/**
 * Escape text for FFmpeg drawtext filter
 */
function escapeDrawtext(text: string): string {
  return text
    .replace(/\\/g, '\\\\')
    .replace(/'/g, '\\\'')
    .replace(/:/g, '\\:')
    .replace(/,/g, '\\,');
}

/**
 * Generate ASS subtitle file - simple static display without typewriter effect
 */
function generateASS(segments: AudioSegment[], width: number, height: number): string {
  const header = `[Script Info]
Title: KK量化课堂 Subtitles
ScriptType: v4.00+
PlayResX: ${width}
PlayResY: ${height}
Timer: 100.0000

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,STHeiti Medium,28,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,3,2,0,2,60,60,80,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
`;

  const lines: string[] = [header];

  for (const seg of segments) {
    if (!seg.text?.trim()) continue;

    const start = formatASSTime(seg.startTime);
    const end = formatASSTime(seg.startTime + seg.duration);
    const text = seg.text.trim().replace(/\n/g, '\\N');

    lines.push(`Dialogue: 0,${start},${end},Default,,0,0,0,,${text}`);
  }

  return lines.join('\n');
}

/**
 * Format milliseconds to ASS time: H:MM:SS.cc
 */
function formatASSTime(ms: number): string {
  const hours = Math.floor(ms / 3600000);
  const minutes = Math.floor((ms % 3600000) / 60000);
  const seconds = Math.floor((ms % 60000) / 1000);
  const centis = Math.floor((ms % 1000) / 10);

  return `${hours}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}.${String(centis).padStart(2, '0')}`;
}

/**
 * Encode video from slides and audio using FFmpeg
 */
async function encodeVideo(
  concatFilePath: string,
  audioPath: string | null,
  outputPath: string,
  options: ExportOptions,
  timeline: VideoTimeline,
  subtitlePath?: string | null,
): Promise<void> {
  const { width, height } = RESOLUTION_DIMENSIONS[options.resolution];
  const { crf, preset } = QUALITY_PRESETS[options.quality];
  const totalDuration = timeline.duration / 1000;

  const args = [
    '-y',
    '-f', 'concat',
    '-safe', '0',
    '-i', concatFilePath,
  ];

  // Add audio input if available
  if (audioPath) {
    args.push('-i', audioPath);
  }

  // Map streams
  args.push('-map', '0:v'); // Video from concat
  if (audioPath) {
    args.push('-map', '1:a'); // Audio from second input
  }

  // Build video filter
  let videoFilter = `scale=${width}:${height}:force_original_aspect_ratio=decrease,pad=${width}:${height}:(ow-iw)/2:(oh-ih)/2`;
  
  // Add subtitle burn-in if available using ass filter
  if (subtitlePath) {
    // Escape colons in path for FFmpeg filter
    const escapedPath = subtitlePath.replace(/:/g, '\\:');
    videoFilter += `,ass=${escapedPath}`;
  }

  // Video encoding options
  args.push(
    '-c:v', 'libx264',
    '-crf', String(crf),
    '-preset', preset,
    '-pix_fmt', 'yuv420p',
    '-vf', videoFilter,
    '-r', String(options.fps),
    '-t', String(totalDuration),
  );

  // Audio encoding options
  if (audioPath) {
    args.push(
      '-c:a', 'aac',
      '-b:a', '128k',
    );
  }

  // Output optimization
  args.push(
    '-movflags', '+faststart',
    outputPath,
  );

  log.info('FFmpeg args:', args.join(' '));

  await new Promise<void>((resolve, reject) => {
    const ffmpeg = spawn(FFMPEG_PATH, args);

    let stderr = '';
    ffmpeg.stderr.on('data', (data) => {
      stderr += data.toString();
    });

    ffmpeg.on('close', (code) => {
      if (code === 0) {
        resolve();
      } else {
        log.error('FFmpeg stderr:', stderr);
        reject(new Error(`FFmpeg exited with code ${code}`));
      }
    });

    ffmpeg.on('error', (err) => {
      log.error('FFmpeg error:', err);
      reject(err);
    });
  });
}

/**
 * Get video file info
 */
async function getVideoInfo(filePath: string): Promise<{
  duration: number;
  width: number;
  height: number;
  fps: number;
  bitrate: number;
  size: number;
}> {
  return new Promise((resolve, reject) => {
    const ffprobe = spawn('ffprobe', [
      '-v', 'error',
      '-select_streams', 'v:0',
      '-show_entries', 'stream=width,height,r_frame_rate,bit_rate,duration:format=duration,size',
      '-of', 'json',
      filePath,
    ]);

    let stdout = '';
    ffprobe.stdout.on('data', (data) => {
      stdout += data.toString();
    });

    ffprobe.on('close', (code) => {
      if (code === 0) {
        try {
          const info = JSON.parse(stdout);
          const stream = info.streams?.[0] || {};
          const format = info.format || {};

          // Parse frame rate (e.g., "30/1")
          let fps = 30;
          if (stream.r_frame_rate) {
            const [num, den] = stream.r_frame_rate.split('/').map(Number);
            fps = den ? num / den : num;
          }

          resolve({
            duration: Math.round((parseFloat(stream.duration || format.duration || 0)) * 1000),
            width: stream.width || 1920,
            height: stream.height || 1080,
            fps: Math.round(fps),
            bitrate: parseInt(stream.bit_rate || '0') || 0,
            size: parseInt(format.size || '0') || 0,
          });
        } catch (err) {
          reject(err);
        }
      } else {
        reject(new Error('ffprobe failed'));
      }
    });

    ffprobe.on('error', reject);
  });
}

/**
 * Export video from timeline
 */
export async function exportVideo(
  timeline: VideoTimeline,
  outputDir: string,
  audioPath: string | null,
  audioSegments: AudioSegment[],
  options: Partial<ExportOptions> = {},
  onProgress?: (progress: ExportProgress) => void,
): Promise<VideoOutput> {
  const opts: ExportOptions = { ...DEFAULT_EXPORT_OPTIONS, ...options };

  // Check FFmpeg availability
  if (!isFFmpegAvailable()) {
    throw new Error('FFmpeg is not available. Please install FFmpeg to export videos.');
  }

  await fs.mkdir(outputDir, { recursive: true });

  const tempDir = path.join(outputDir, 'temp');
  await fs.mkdir(tempDir, { recursive: true });

  try {
    // Step 1: Render slides
    onProgress?.({
      step: 'rendering_slides',
      progress: 0,
      message: 'Starting slide rendering...',
    });

    const imagePaths = await renderSlidesToImages(timeline, tempDir, opts, onProgress);

    if (imagePaths.length === 0) {
      throw new Error('No slides to render');
    }

    // Step 2: Create concat file
    onProgress?.({
      step: 'encoding_video',
      progress: 0,
      message: 'Preparing video encoding...',
    });

    const concatFilePath = path.join(tempDir, 'concat.txt');
    await createConcatFile(timeline.scenes, imagePaths, concatFilePath);

    // Step 3: Generate subtitle file (disabled - no subtitles)
    let subtitlePath: string | null = null;
    // Subtitles disabled as per user request
    // if (audioSegments.length > 0) {
    //   const assContent = generateASS(audioSegments, opts.resolution === '1080p' ? 1920 : 1280, opts.resolution === '1080p' ? 1080 : 720);
    //   if (assContent.trim()) {
    //     subtitlePath = path.join(tempDir, 'subtitles.ass');
    //     await fs.writeFile(subtitlePath, assContent, 'utf-8');
    //     log.info(`Generated ASS subtitle file with ${audioSegments.length} segments`);
    //   }
    // }

    // Step 4: Encode video
    onProgress?.({
      step: 'encoding_video',
      progress: 50,
      message: 'Encoding video...',
    });

    const filename = `classroom_${timeline.stage.id}_${Date.now()}.mp4`;
    const outputPath = path.join(outputDir, filename);

    await encodeVideo(concatFilePath, audioPath, outputPath, opts, timeline, subtitlePath);

    // Step 4: Get video info
    onProgress?.({
      step: 'finalizing',
      progress: 90,
      message: 'Finalizing...',
    });

    const videoInfo = await getVideoInfo(outputPath);

    // Clean up temp files
    try {
      for (const imagePath of imagePaths) {
        await fs.unlink(imagePath);
      }
      await fs.unlink(concatFilePath);
      await fs.rmdir(tempDir);
    } catch (err) {
      log.warn('Failed to clean up temp files:', err);
    }

    onProgress?.({
      step: 'finalizing',
      progress: 100,
      message: 'Export complete!',
    });

    return {
      path: outputPath,
      filename,
      size: videoInfo.size,
      duration: videoInfo.duration,
      width: videoInfo.width,
      height: videoInfo.height,
      fps: videoInfo.fps,
      bitrate: videoInfo.bitrate,
    };
  } catch (err) {
    // Clean up on error
    try {
      await fs.rm(tempDir, { recursive: true, force: true });
    } catch {}

    throw err;
  }
}

/**
 * Export video from slides directly (simplified)
 */
export async function exportSlidesToVideo(
  slides: Slide[],
  durations: number[], // in milliseconds
  outputDir: string,
  audioPath: string | null,
  options: Partial<ExportOptions> = {},
): Promise<VideoOutput> {
  const opts: ExportOptions = { ...DEFAULT_EXPORT_OPTIONS, ...options };
  const { resolution } = opts;
  const { width, height } = RESOLUTION_DIMENSIONS[resolution];

  await fs.mkdir(outputDir, { recursive: true });

  const tempDir = path.join(outputDir, 'temp');
  await fs.mkdir(tempDir, { recursive: true });

  try {
    // Render slides
    const imagePaths: string[] = [];
    for (let i = 0; i < slides.length; i++) {
      const imagePath = path.join(tempDir, `slide_${String(i).padStart(4, '0')}.png`);
      const pngBuffer = await renderSlideToPng(slides[i], resolution);
      await fs.writeFile(imagePath, pngBuffer);
      imagePaths.push(imagePath);
    }

    // Create concat file
    const concatFilePath = path.join(tempDir, 'concat.txt');
    const lines: string[] = [];

    for (let i = 0; i < slides.length; i++) {
      const duration = (durations[i] || 5000) / 1000;
      lines.push(`file '${imagePaths[i]}'`);
      lines.push(`duration ${duration}`);
    }

    if (imagePaths.length > 0) {
      lines.push(`file '${imagePaths[imagePaths.length - 1]}'`);
    }

    await fs.writeFile(concatFilePath, lines.join('\n'));

    // Encode
    const filename = `slides_${Date.now()}.mp4`;
    const outputPath = path.join(outputDir, filename);
    const totalDuration = durations.reduce((sum, d) => sum + d, 0) / 1000;
    const { crf, preset } = QUALITY_PRESETS[opts.quality];

    const args = [
      '-y',
      '-f', 'concat',
      '-safe', '0',
      '-i', concatFilePath,
    ];

    if (audioPath) {
      args.push('-i', audioPath, '-map', '0:v', '-map', '1:a');
    }

    args.push(
      '-c:v', 'libx264',
      '-crf', String(crf),
      '-preset', preset,
      '-pix_fmt', 'yuv420p',
      '-vf', `scale=${width}:${height}`,
      '-r', String(opts.fps),
      '-t', String(totalDuration),
    );

    if (audioPath) {
      args.push('-c:a', 'aac', '-b:a', '128k');
    }

    args.push('-movflags', '+faststart', outputPath);

    await new Promise<void>((resolve, reject) => {
      const ffmpeg = spawn(FFMPEG_PATH, args);
      let stderr = '';

      ffmpeg.stderr.on('data', (data) => {
        stderr += data.toString();
      });

      ffmpeg.on('close', (code) => {
        if (code === 0) resolve();
        else reject(new Error(`FFmpeg failed: ${stderr}`));
      });

      ffmpeg.on('error', reject);
    });

    const videoInfo = await getVideoInfo(outputPath);

    // Clean up
    for (const p of imagePaths) {
      try { await fs.unlink(p); } catch {}
    }
    try { await fs.unlink(concatFilePath); } catch {}
    try { await fs.rmdir(tempDir); } catch {}

    return {
      path: outputPath,
      filename,
      size: videoInfo.size,
      duration: videoInfo.duration,
      width: videoInfo.width,
      height: videoInfo.height,
      fps: videoInfo.fps,
      bitrate: videoInfo.bitrate,
    };
  } catch (err) {
    try { await fs.rm(tempDir, { recursive: true, force: true }); } catch {}
    throw err;
  }
}

export { getVideoInfo };
