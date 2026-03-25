/**
 * Audio Collector - Collects and merges TTS audio for video export
 *
 * Collects audio files from scenes and merges them into a single track.
 * Uses FFmpeg for audio processing.
 */

import { promises as fs } from 'fs';
import path from 'path';
import { spawn } from 'child_process';
import type { Scene } from '@/lib/types/stage';
import type { SpeechAction } from '@/lib/types/action';
import type { AudioSegment, AudioTrack } from './types';
import { createLogger } from '@/lib/logger';

// Path to FFmpeg/FFprobe binaries
const FFMPEG_PATH = process.env.FFMPEG_PATH || 'ffmpeg';
const FFPROBE_PATH = process.env.FFPROBE_PATH || 'ffprobe';

const log = createLogger('AudioCollector');

// Audio settings
const SAMPLE_RATE = 44100;
const CHANNELS = 2;

/**
 * Download audio from URL to local file
 */
async function downloadAudio(url: string, outputPath: string): Promise<void> {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Failed to download audio: ${response.status}`);
  }
  const buffer = await response.arrayBuffer();
  await fs.writeFile(outputPath, Buffer.from(buffer));
}

/**
 * Get audio duration using ffprobe
 */
async function getAudioDuration(filePath: string): Promise<number> {
  return new Promise((resolve, reject) => {
    const ffprobe = spawn(FFPROBE_PATH, [
      '-v', 'error',
      '-show_entries', 'format=duration',
      '-of', 'default=noprint_wrappers=1:nokey=1',
      filePath,
    ]);

    let output = '';
    ffprobe.stdout.on('data', (data) => {
      output += data.toString();
    });

    ffprobe.on('close', (code) => {
      if (code === 0) {
        const duration = parseFloat(output.trim());
        resolve(Math.round(duration * 1000)); // Convert to ms
      } else {
        reject(new Error(`ffprobe failed with code ${code}`));
      }
    });

    ffprobe.on('error', reject);
  });
}

/**
 * Collect audio segments from scenes
 */
export async function collectAudioSegments(
  scenes: Scene[],
  tempDir: string,
  baseUrl?: string,
): Promise<AudioSegment[]> {
  const segments: AudioSegment[] = [];
  let currentTime = 0;

  // Sort scenes by order
  const sortedScenes = [...scenes].sort((a, b) => a.order - b.order);

  for (let i = 0; i < sortedScenes.length; i++) {
    const scene = sortedScenes[i];
    const actions = scene.actions || [];
    const speechActions = actions.filter((a): a is SpeechAction => a.type === 'speech');

    for (const speech of speechActions) {
      if (!speech.audioUrl && !speech.audioId) {
        // No audio for this speech, skip
        log.debug(`No audio for speech in scene ${scene.id}`);
        continue;
      }

      // Construct audio URL
      let audioUrl = speech.audioUrl;
      if (!audioUrl && speech.audioId && baseUrl) {
        audioUrl = `${baseUrl}/api/classroom-media/${scene.stageId}/audio/${speech.audioId}.mp3`;
      }

      if (!audioUrl) {
        continue;
      }

      // Download audio to temp file
      const audioPath = path.join(tempDir, `speech_${scene.id}_${speech.audioId || Date.now()}.mp3`);

      try {
        await downloadAudio(audioUrl, audioPath);
        const duration = await getAudioDuration(audioPath);

        segments.push({
          sceneIndex: i,
          audioUrl,
          audioId: speech.audioId,
          startTime: currentTime,
          duration,
          text: speech.text,
        });

        currentTime += duration;
      } catch (err) {
        log.warn(`Failed to process audio for scene ${scene.id}:`, err);
      }
    }
  }

  return segments;
}

/**
 * Merge audio segments into a single track using FFmpeg
 */
export async function mergeAudioSegments(
  segments: AudioSegment[],
  outputPath: string,
  tempDir: string,
): Promise<AudioTrack> {
  if (segments.length === 0) {
    // Create silent audio
    await createSilentAudio(outputPath, 1000); // 1 second of silence
    return {
      segments: [],
      totalDuration: 1000,
      sampleRate: SAMPLE_RATE,
      channels: CHANNELS,
    };
  }

  // Calculate total duration
  const totalDuration = segments.reduce((sum, s) => {
    const endTime = s.startTime + s.duration;
    return Math.max(sum, endTime);
  }, 0);

  // Download all audio files
  const audioFiles: string[] = [];
  for (let i = 0; i < segments.length; i++) {
    const segment = segments[i];
    const audioPath = path.join(tempDir, `segment_${i}.mp3`);
    await downloadAudio(segment.audioUrl, audioPath);
    audioFiles.push(audioPath);
  }

  // Create FFmpeg concat file
  const concatFilePath = path.join(tempDir, 'concat.txt');
  const concatContent = audioFiles.map((f) => `file '${f}'`).join('\n');
  await fs.writeFile(concatFilePath, concatContent);

  // Merge using FFmpeg
  await new Promise<void>((resolve, reject) => {
    const ffmpeg = spawn(FFMPEG_PATH, [
      '-y',
      '-f', 'concat',
      '-safe', '0',
      '-i', concatFilePath,
      '-c:a', 'aac',
      '-b:a', '128k',
      '-ar', String(SAMPLE_RATE),
      '-ac', String(CHANNELS),
      outputPath,
    ]);

    let stderr = '';
    ffmpeg.stderr.on('data', (data) => {
      stderr += data.toString();
    });

    ffmpeg.on('close', (code) => {
      if (code === 0) {
        resolve();
      } else {
        reject(new Error(`FFmpeg failed: ${stderr}`));
      }
    });

    ffmpeg.on('error', reject);
  });

  // Clean up temp files
  for (const file of audioFiles) {
    try {
      await fs.unlink(file);
    } catch {}
  }
  try {
    await fs.unlink(concatFilePath);
  } catch {}

  return {
    segments,
    totalDuration,
    sampleRate: SAMPLE_RATE,
    channels: CHANNELS,
  };
}

/**
 * Create silent audio file
 */
async function createSilentAudio(outputPath: string, durationMs: number): Promise<void> {
  const duration = durationMs / 1000;

  await new Promise<void>((resolve, reject) => {
    const ffmpeg = spawn(FFMPEG_PATH, [
      '-y',
      '-f', 'lavfi',
      '-i', `anullsrc=r=${SAMPLE_RATE}:cl=stereo`,
      '-t', String(duration),
      '-c:a', 'aac',
      '-b:a', '128k',
      outputPath,
    ]);

    let stderr = '';
    ffmpeg.stderr.on('data', (data) => {
      stderr += data.toString();
    });

    ffmpeg.on('close', (code) => {
      if (code === 0) {
        resolve();
      } else {
        reject(new Error(`FFmpeg failed: ${stderr}`));
      }
    });

    ffmpeg.on('error', reject);
  });
}

/**
 * Build audio track from scenes
 */
export async function buildAudioTrack(
  scenes: Scene[],
  tempDir: string,
  outputDir: string,
  baseUrl?: string,
): Promise<{ track: AudioTrack; audioPath: string }> {
  await fs.mkdir(tempDir, { recursive: true });
  await fs.mkdir(outputDir, { recursive: true });

  const segments = await collectAudioSegments(scenes, tempDir, baseUrl);
  const audioPath = path.join(outputDir, 'audio.aac');
  const track = await mergeAudioSegments(segments, audioPath, tempDir);

  return { track, audioPath };
}

/**
 * Get audio durations map from scenes
 */
export async function getAudioDurationsMap(
  scenes: Scene[],
  tempDir: string,
  baseUrl?: string,
): Promise<Map<string, number>> {
  const durations = new Map<string, number>();

  await fs.mkdir(tempDir, { recursive: true });

  for (const scene of scenes) {
    const actions = scene.actions || [];
    const speechActions = actions.filter((a): a is SpeechAction => a.type === 'speech');

    for (const speech of speechActions) {
      if (!speech.audioId) continue;

      let audioUrl = speech.audioUrl;
      if (!audioUrl && baseUrl) {
        audioUrl = `${baseUrl}/api/classroom-media/${scene.stageId}/audio/${speech.audioId}.mp3`;
      }

      if (!audioUrl) continue;

      const audioPath = path.join(tempDir, `duration_${speech.audioId}.mp3`);

      try {
        await downloadAudio(audioUrl, audioPath);
        const duration = await getAudioDuration(audioPath);
        durations.set(speech.audioId, duration);

        // Clean up
        try {
          await fs.unlink(audioPath);
        } catch {}
      } catch (err) {
        log.warn(`Failed to get duration for audio ${speech.audioId}:`, err);
      }
    }
  }

  return durations;
}
