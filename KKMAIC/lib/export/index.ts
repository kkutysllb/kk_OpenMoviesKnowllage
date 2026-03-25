/**
 * Video Export Module
 *
 * Server-side video export pipeline for OpenMAIC classrooms.
 */

// Types
export type {
  ExportOptions,
  VideoQuality,
  VideoResolution,
  VideoTimeline,
  SceneTimeline,
  TimelineEvent,
  SlideTimelineEvent,
  SpeechTimelineEvent,
  FrameInfo,
  FrameSequence,
  RenderContext,
  ExportJob,
  ExportJobStatus,
  ExportProgress,
  AudioSegment,
  AudioTrack,
  VideoOutput,
} from './types';

export {
  DEFAULT_EXPORT_OPTIONS,
  RESOLUTION_DIMENSIONS,
  QUALITY_PRESETS,
} from './types';

// Slide renderer
export { renderSlideToPng } from './slide-renderer';

// Timeline builder
export {
  buildTimeline,
  buildSimpleTimeline,
  getTotalDuration,
  getSlideAtTime,
  getSceneIndexAtTime,
  calculateFrameTimestamps,
} from './timeline-builder';

// Audio collector
export {
  collectAudioSegments,
  mergeAudioSegments,
  buildAudioTrack,
  getAudioDurationsMap,
} from './audio-collector';

// Video encoder
export {
  exportVideo,
  exportSlidesToVideo,
  isFFmpegAvailable,
  getVideoInfo,
} from './video-encoder';
