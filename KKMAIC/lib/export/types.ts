/**
 * Video Export Types
 *
 * Type definitions for the server-side video export pipeline.
 */

import type { Scene, Stage } from '@/lib/types/stage';
import type { Slide, PPTElement } from '@/lib/types/slides';
import type { Action, SpeechAction } from '@/lib/types/action';

// ==================== Export Options ====================

export type VideoQuality = 'low' | 'medium' | 'high';
export type VideoResolution = '720p' | '1080p' | '4k';

export interface ExportOptions {
  /** Frames per second, default 30 */
  fps: number;
  /** Video quality preset */
  quality: VideoQuality;
  /** Output resolution */
  resolution: VideoResolution;
  /** Include TTS audio */
  includeAudio: boolean;
  /** Transition duration between slides (ms), 0 for no transition */
  transitionDuration: number;
}

export const DEFAULT_EXPORT_OPTIONS: ExportOptions = {
  fps: 30,
  quality: 'medium',
  resolution: '1080p',
  includeAudio: true,
  transitionDuration: 500,
};

export const RESOLUTION_DIMENSIONS: Record<VideoResolution, { width: number; height: number }> = {
  '720p': { width: 1280, height: 720 },
  '1080p': { width: 1920, height: 1080 },
  '4k': { width: 3840, height: 2160 },
};

export const QUALITY_PRESETS: Record<VideoQuality, { crf: number; preset: string }> = {
  low: { crf: 28, preset: 'veryfast' },
  medium: { crf: 23, preset: 'medium' },
  high: { crf: 18, preset: 'slow' },
};

// ==================== Timeline ====================

export type TimelineEventType =
  | 'slide_change'
  | 'speech_start'
  | 'speech_end'
  | 'spotlight'
  | 'laser'
  | 'whiteboard_open'
  | 'whiteboard_close'
  | 'whiteboard_draw';

export interface TimelineEvent {
  type: TimelineEventType;
  startTime: number; // milliseconds from video start
  duration: number; // milliseconds
  sceneIndex: number;
  actionIndex?: number;
  data?: unknown;
}

export interface SpeechTimelineEvent extends TimelineEvent {
  type: 'speech_start';
  text: string;
  audioUrl?: string;
  audioId?: string;
}

export interface SlideTimelineEvent extends TimelineEvent {
  type: 'slide_change';
  slide: Slide;
  sceneId: string;
}

export interface SceneTimeline {
  sceneId: string;
  sceneIndex: number;
  startTime: number;
  duration: number;
  slide: Slide;
  speechActions: SpeechAction[];
  audioDuration: number; // total audio duration for this scene
}

export interface VideoTimeline {
  /** Total video duration in milliseconds */
  duration: number;
  /** All timeline events sorted by startTime */
  events: TimelineEvent[];
  /** Per-scene timeline data */
  scenes: SceneTimeline[];
  /** Source stage info */
  stage: Stage;
}

// ==================== Frame Rendering ====================

export interface RenderContext {
  width: number;
  height: number;
  scale: number;
  viewportSize: number;
  viewportRatio: number;
}

export interface FrameInfo {
  frameIndex: number;
  timestamp: number; // milliseconds
  sceneIndex: number;
  slide: Slide;
  renderContext: RenderContext;
}

export interface FrameSequence {
  frames: FrameInfo[];
  totalDuration: number;
  fps: number;
}

// ==================== Export Job ====================

export type ExportJobStatus = 'pending' | 'processing' | 'completed' | 'failed';

export interface ExportJob {
  id: string;
  classroomId: string;
  status: ExportJobStatus;
  progress: number; // 0-100
  currentStep: string;
  outputPath?: string;
  outputUrl?: string;
  error?: string;
  createdAt: number;
  updatedAt: number;
  options: ExportOptions;
}

export interface ExportProgress {
  step: 'initializing' | 'rendering_slides' | 'collecting_audio' | 'encoding_video' | 'finalizing';
  progress: number; // 0-100
  message: string;
  currentScene?: number;
  totalScenes?: number;
}

// ==================== Audio ====================

export interface AudioSegment {
  sceneIndex: number;
  audioUrl: string;
  audioId?: string;
  startTime: number; // in the final audio track
  duration: number;
  text: string;
}

export interface AudioTrack {
  segments: AudioSegment[];
  totalDuration: number;
  sampleRate: number;
  channels: number;
}

// ==================== Video Output ====================

export interface VideoOutput {
  path: string;
  filename: string;
  size: number; // bytes
  duration: number; // milliseconds
  width: number;
  height: number;
  fps: number;
  bitrate: number;
}

// ==================== Element Rendering ====================

export interface ElementRenderContext {
  ctx: unknown; // CanvasRenderingContext2D (from @napi-rs/canvas)
  element: PPTElement;
  slide: Slide;
  renderContext: RenderContext;
}

// Re-export types from other modules for convenience
export type { Scene, Stage, Slide, PPTElement, Action, SpeechAction };
