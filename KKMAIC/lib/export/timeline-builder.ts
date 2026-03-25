/**
 * Timeline Builder - Constructs video timeline from scene actions
 *
 * Parses Scene.actions[] and builds a timeline for video rendering.
 * Calculates timing for slides, speech, and other actions.
 */

import type { Scene, Stage, SceneContent, SlideContent } from '@/lib/types/stage';
import type { Action, SpeechAction } from '@/lib/types/action';
import type { Slide } from '@/lib/types/slides';
import type {
  VideoTimeline,
  SceneTimeline,
  TimelineEvent,
  SlideTimelineEvent,
  SpeechTimelineEvent,
} from './types';
import { createLogger } from '@/lib/logger';

const log = createLogger('TimelineBuilder');

// Default reading speed when no audio is available
// CJK: ~150ms/char, English: ~240ms/word
const CJK_MS_PER_CHAR = 150;
const ENGLISH_MS_PER_WORD = 240;
const MIN_SPEECH_DURATION = 2000; // 2 seconds minimum

/**
 * Check if text is primarily CJK
 */
function isCJKText(text: string): boolean {
  const cjkCount = (text.match(/[\u4e00-\u9fff\u3400-\u4dbf\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]/g) || []).length;
  return cjkCount > text.length * 0.3;
}

/**
 * Estimate speech duration when no audio is available
 */
function estimateSpeechDuration(text: string): number {
  const trimmed = text.trim();
  if (!trimmed) return MIN_SPEECH_DURATION;

  if (isCJKText(trimmed)) {
    return Math.max(MIN_SPEECH_DURATION, trimmed.length * CJK_MS_PER_CHAR);
  } else {
    const wordCount = trimmed.split(/\s+/).filter(Boolean).length;
    return Math.max(MIN_SPEECH_DURATION, wordCount * ENGLISH_MS_PER_WORD);
  }
}

/**
 * Extract slide from scene content
 */
function extractSlideFromScene(scene: Scene): Slide | null {
  if (scene.type !== 'slide') return null;

  const content = scene.content as SlideContent;
  if (!content?.canvas) return null;

  return content.canvas;
}

/**
 * Build video timeline from scenes
 */
export function buildTimeline(
  stage: Stage,
  scenes: Scene[],
  audioDurations?: Map<string, number>, // audioId -> duration in ms
): VideoTimeline {
  const events: TimelineEvent[] = [];
  const sceneTimelines: SceneTimeline[] = [];
  let currentTime = 0;

  // Sort scenes by order
  const sortedScenes = [...scenes].sort((a, b) => a.order - b.order);

  for (let i = 0; i < sortedScenes.length; i++) {
    const scene = sortedScenes[i];
    const slide = extractSlideFromScene(scene);

    if (!slide) {
      log.warn(`Scene ${scene.id} has no slide content, skipping`);
      continue;
    }

    const sceneStartTime = currentTime;
    const actions = scene.actions || [];

    // Collect speech actions for this scene
    const speechActions: SpeechAction[] = actions.filter(
      (a): a is SpeechAction => a.type === 'speech',
    );

    // Calculate scene duration based on speech actions
    let sceneDuration = 0;
    for (const speech of speechActions) {
      let duration: number;

      if (speech.audioId && audioDurations?.has(speech.audioId)) {
        duration = audioDurations.get(speech.audioId)!;
      } else if (speech.audioUrl) {
        // Estimate from URL (will be updated when audio is fetched)
        duration = estimateSpeechDuration(speech.text);
      } else {
        duration = estimateSpeechDuration(speech.text);
      }

      sceneDuration += duration;
    }

    // Minimum scene duration
    if (sceneDuration < MIN_SPEECH_DURATION) {
      sceneDuration = MIN_SPEECH_DURATION;
    }

    // Add slide change event
    const slideEvent: SlideTimelineEvent = {
      type: 'slide_change',
      startTime: sceneStartTime,
      duration: sceneDuration,
      sceneIndex: i,
      slide,
      sceneId: scene.id,
    };
    events.push(slideEvent);

    // Add speech events
    let speechTime = sceneStartTime;
    for (const speech of speechActions) {
      let duration: number;

      if (speech.audioId && audioDurations?.has(speech.audioId)) {
        duration = audioDurations.get(speech.audioId)!;
      } else {
        duration = estimateSpeechDuration(speech.text);
      }

      const speechEvent: SpeechTimelineEvent = {
        type: 'speech_start',
        startTime: speechTime,
        duration,
        sceneIndex: i,
        text: speech.text,
        audioUrl: speech.audioUrl,
        audioId: speech.audioId,
      };
      events.push(speechEvent);

      speechTime += duration;
    }

    // Add other action events (simplified - no timing for now)
    for (let j = 0; j < actions.length; j++) {
      const action = actions[j];
      if (action.type === 'speech') continue;

      // These events happen during the scene
      const event: TimelineEvent = {
        type: mapActionToEventType(action.type),
        startTime: sceneStartTime, // Simplified timing
        duration: 0,
        sceneIndex: i,
        actionIndex: j,
        data: action,
      };
      events.push(event);
    }

    // Create scene timeline
    const sceneTimeline: SceneTimeline = {
      sceneId: scene.id,
      sceneIndex: i,
      startTime: sceneStartTime,
      duration: sceneDuration,
      slide,
      speechActions,
      audioDuration: sceneDuration,
    };
    sceneTimelines.push(sceneTimeline);

    currentTime += sceneDuration;
  }

  // Sort events by start time
  events.sort((a, b) => a.startTime - b.startTime);

  return {
    duration: currentTime,
    events,
    scenes: sceneTimelines,
    stage,
  };
}

/**
 * Map action type to timeline event type
 */
function mapActionToEventType(actionType: string): TimelineEvent['type'] {
  switch (actionType) {
    case 'spotlight':
      return 'spotlight';
    case 'laser':
      return 'laser';
    case 'wb_open':
      return 'whiteboard_open';
    case 'wb_close':
      return 'whiteboard_close';
    case 'wb_draw_text':
    case 'wb_draw_shape':
    case 'wb_draw_chart':
    case 'wb_draw_latex':
    case 'wb_draw_table':
    case 'wb_draw_line':
      return 'whiteboard_draw';
    default:
      return 'slide_change';
  }
}

/**
 * Get total video duration
 */
export function getTotalDuration(timeline: VideoTimeline): number {
  return timeline.duration;
}

/**
 * Get slide for a given timestamp
 */
export function getSlideAtTime(timeline: VideoTimeline, timestamp: number): Slide | null {
  for (const scene of timeline.scenes) {
    if (timestamp >= scene.startTime && timestamp < scene.startTime + scene.duration) {
      return scene.slide;
    }
  }
  return null;
}

/**
 * Get scene index for a given timestamp
 */
export function getSceneIndexAtTime(timeline: VideoTimeline, timestamp: number): number {
  for (const scene of timeline.scenes) {
    if (timestamp >= scene.startTime && timestamp < scene.startTime + scene.duration) {
      return scene.sceneIndex;
    }
  }
  return -1;
}

/**
 * Calculate frame timestamps
 */
export function calculateFrameTimestamps(
  timeline: VideoTimeline,
  fps: number,
): number[] {
  const frames: number[] = [];
  const frameDuration = 1000 / fps; // ms per frame
  const totalDuration = timeline.duration;

  for (let t = 0; t < totalDuration; t += frameDuration) {
    frames.push(t);
  }

  // Add final frame
  if (frames[frames.length - 1] !== totalDuration) {
    frames.push(totalDuration);
  }

  return frames;
}

/**
 * Build a simplified timeline for video export
 * This version only considers slide changes and speech duration
 */
export function buildSimpleTimeline(
  stage: Stage,
  scenes: Scene[],
): VideoTimeline {
  return buildTimeline(stage, scenes, undefined);
}

export { buildTimeline as buildVideoTimeline };
