'use client';

import {
  Settings,
  Sun,
  Moon,
  Monitor,
  ArrowLeft,
  Loader2,
  Download,
  FileDown,
  Package,
  Video,
} from 'lucide-react';
import { useI18n } from '@/lib/hooks/use-i18n';
import { useTheme } from '@/lib/hooks/use-theme';
import { useState, useEffect, useRef, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { SettingsDialog } from './settings';
import { cn } from '@/lib/utils';
import { useSettingsStore } from '@/lib/store/settings';
import { useStageStore } from '@/lib/store/stage';
import { useMediaGenerationStore } from '@/lib/store/media-generation';
import { useExportPPTX } from '@/lib/export/use-export-pptx';
import { db } from '@/lib/utils/database';

interface HeaderProps {
  readonly currentSceneTitle: string;
  readonly isRecordingScreen?: boolean;
  readonly onToggleScreenRecording?: () => void;
  readonly className?: string;
}

export function Header({ currentSceneTitle, isRecordingScreen, onToggleScreenRecording, className }: HeaderProps) {
  const { t, locale, setLocale } = useI18n();
  const { theme, setTheme } = useTheme();
  const router = useRouter();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [languageOpen, setLanguageOpen] = useState(false);
  const [themeOpen, setThemeOpen] = useState(false);

  // Model setup state
  const currentModelId = useSettingsStore((s) => s.modelId);
  const needsSetup = !currentModelId;

  // Export
  const { exporting: isExporting, exportPPTX, exportResourcePack } = useExportPPTX();
  const [exportMenuOpen, setExportMenuOpen] = useState(false);
  const exportRef = useRef<HTMLDivElement>(null);
  const scenes = useStageStore((s) => s.scenes);
  const generatingOutlines = useStageStore((s) => s.generatingOutlines);
  const failedOutlines = useStageStore((s) => s.failedOutlines);
  const mediaTasks = useMediaGenerationStore((s) => s.tasks);

  // Video export state
  const [videoExporting, setVideoExporting] = useState(false);
  const [videoExportProgress, setVideoExportProgress] = useState<string | null>(null);

  const canExport =
    scenes.length > 0 &&
    generatingOutlines.length === 0 &&
    failedOutlines.length === 0 &&
    Object.values(mediaTasks).every((task) => task.status === 'done' || task.status === 'failed');

  // Video export function
  const exportVideo = useCallback(async () => {
    const stageId = useStageStore.getState().stage?.id;
    const scenes = useStageStore.getState().scenes;
    const stage = useStageStore.getState().stage;
    if (!stageId || !stage) return;

    setVideoExporting(true);
    setVideoExportProgress('Saving classroom...');

    try {
      // First, persist classroom to server storage
      const persistRes = await fetch('/api/classroom', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ stage, scenes }),
      });

      if (!persistRes.ok) {
        const err = await persistRes.json();
        throw new Error(err.error || 'Failed to save classroom');
      }

      // Collect audio IDs from scenes
      const audioIds: string[] = [];
      for (const scene of scenes) {
        const speechActions = (scene.actions || []).filter((a) => a.type === 'speech');
        for (const action of speechActions) {
          if (action.audioId && !audioIds.includes(action.audioId)) {
            audioIds.push(action.audioId);
          }
        }
      }

      // Upload audio files from IndexedDB to server
      if (audioIds.length > 0) {
        setVideoExportProgress('Uploading audio files...');
        
        for (const audioId of audioIds) {
          try {
            const audioRecord = await db.audioFiles.get(audioId);
            if (audioRecord?.blob) {
              const formData = new FormData();
              formData.append('classroomId', stageId);
              formData.append('audioId', audioId);
              formData.append('file', audioRecord.blob, `${audioId}.mp3`);

              const uploadRes = await fetch('/api/classroom-media', {
                method: 'POST',
                body: formData,
              });

              if (!uploadRes.ok) {
                console.warn(`Failed to upload audio ${audioId}`);
              }
            }
          } catch (uploadErr) {
            console.warn(`Error uploading audio ${audioId}:`, uploadErr);
          }
        }
      }

      setVideoExportProgress('Starting export...');

      // Start export job
      const startRes = await fetch('/api/export-video', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ classroomId: stageId }),
      });

      if (!startRes.ok) {
        const err = await startRes.json();
        throw new Error(err.error || 'Failed to start export');
      }

      const { jobId } = await startRes.json();

      // Poll for progress
      let completed = false;
      while (!completed) {
        await new Promise((r) => setTimeout(r, 2000));
        const statusRes = await fetch(`/api/export-video?jobId=${jobId}`);
        const status = await statusRes.json();

        if (status.job) {
          setVideoExportProgress(`${status.job.currentStep} (${status.job.progress}%)`);

          if (status.job.status === 'completed') {
            completed = true;
            // Download the video
            if (status.job.outputUrl) {
              const downloadRes = await fetch(status.job.outputUrl);
              const blob = await downloadRes.blob();
              const url = URL.createObjectURL(blob);
              const a = document.createElement('a');
              a.href = url;
              a.download = `classroom-${stageId}.mp4`;
              document.body.appendChild(a);
              a.click();
              document.body.removeChild(a);
              URL.revokeObjectURL(url);
            }
          } else if (status.job.status === 'failed') {
            throw new Error(status.job.error || 'Export failed');
          }
        }
      }
    } catch (err) {
      console.error('Video export error:', err);
      alert(err instanceof Error ? err.message : 'Export failed');
    } finally {
      setVideoExporting(false);
      setVideoExportProgress(null);
    }
  }, []);

  const languageRef = useRef<HTMLDivElement>(null);
  const themeRef = useRef<HTMLDivElement>(null);

  // Close dropdown when clicking outside
  const handleClickOutside = useCallback(
    (e: MouseEvent) => {
      if (languageOpen && languageRef.current && !languageRef.current.contains(e.target as Node)) {
        setLanguageOpen(false);
      }
      if (themeOpen && themeRef.current && !themeRef.current.contains(e.target as Node)) {
        setThemeOpen(false);
      }
      if (exportMenuOpen && exportRef.current && !exportRef.current.contains(e.target as Node)) {
        setExportMenuOpen(false);
      }
    },
    [languageOpen, themeOpen, exportMenuOpen],
  );

  useEffect(() => {
    if (languageOpen || themeOpen || exportMenuOpen) {
      document.addEventListener('mousedown', handleClickOutside);
      return () => document.removeEventListener('mousedown', handleClickOutside);
    }
  }, [languageOpen, themeOpen, exportMenuOpen, handleClickOutside]);

  return (
    <>
      <header
        className={cn(
          'h-20 px-8 flex items-center justify-between z-10 bg-transparent gap-4',
          className,
        )}
      >
        <div className="flex items-center gap-3 min-w-0 flex-1">
          <button
            onClick={() => router.push('/')}
            className="shrink-0 p-2 rounded-lg text-gray-400 dark:text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800 hover:text-gray-700 dark:hover:text-gray-300 transition-colors"
            title={t('generation.backToHome')}
          >
            <ArrowLeft className="w-5 h-5" />
          </button>
          <div className="flex flex-col min-w-0">
            <span className="text-[10px] uppercase tracking-widest font-bold text-gray-400 dark:text-gray-500 mb-0.5">
              {t('stage.currentScene')}
            </span>
            <h1
              className="text-xl font-bold text-gray-800 dark:text-gray-200 tracking-tight truncate"
              suppressHydrationWarning
            >
              {currentSceneTitle || t('common.loading')}
            </h1>
          </div>
        </div>

        <div className="flex items-center gap-4 bg-white/60 dark:bg-gray-800/60 backdrop-blur-md px-2 py-1.5 rounded-full border border-gray-100/50 dark:border-gray-700/50 shadow-sm shrink-0">
          {/* Language Selector */}
          <div className="relative" ref={languageRef}>
            <button
              onClick={() => {
                setLanguageOpen(!languageOpen);
                setThemeOpen(false);
              }}
              className="flex items-center gap-1 px-3 py-1.5 rounded-full text-xs font-bold text-gray-500 dark:text-gray-400 hover:bg白 dark:hover:bg-gray-700 hover:text-gray-800 dark:hover:text-gray-200 hover:shadow-sm transition-all"
            >
              {locale === 'zh-CN' ? 'CN' : 'EN'}
            </button>
            {languageOpen && (
              <div className="absolute top-full mt-2 right-0 bg白 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-lg overflow-hidden z-50 min-w-[120px]">
                <button
                  onClick={() => {
                    setLocale('zh-CN');
                    setLanguageOpen(false);
                  }}
                  className={cn(
                    'w-full px-4 py-2 text-left text-sm hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors',
                    locale === 'zh-CN' &&
                      'bg-purple-50 dark:bg-purple-900/20 text-purple-600 dark:text-purple-400',
                  )}
                >
                  简体中文
                </button>
                <button
                  onClick={() => {
                    setLocale('en-US');
                    setLanguageOpen(false);
                  }}
                  className={cn(
                    'w-full px-4 py-2 text-left text-sm hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors',
                    locale === 'en-US' &&
                      'bg-purple-50 dark:bg-purple-900/20 text-purple-600 dark:text-purple-400',
                  )}
                >
                  English
                </button>
              </div>
            )}
          </div>

          <div className="w-[1px] h-4 bg-gray-200 dark:bg-gray-700" />

          {/* Theme Selector */}
          <div className="relative" ref={themeRef}>
            <button
              onClick={() => {
                setThemeOpen(!themeOpen);
                setLanguageOpen(false);
              }}
              className="p-2 rounded-full text-gray-400 dark:text-gray-500 hover:bg白 dark:hover:bg-gray-700 hover:text-gray-800 dark:hover:text-gray-200 hover:shadow-sm transition-all group"
            >
              {theme === 'light' && <Sun className="w-4 h-4" />}
              {theme === 'dark' && <Moon className="w-4 h-4" />}
              {theme === 'system' && <Monitor className="w-4 h-4" />}
            </button>
            {themeOpen && (
              <div className="absolute top-full mt-2 right-0 bg白 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-lg overflow-hidden z-50 min-w-[140px]">
                <button
                  onClick={() => {
                    setTheme('light');
                    setThemeOpen(false);
                  }}
                  className={cn(
                    'w-full px-4 py-2 text-left text-sm hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors flex items-center gap-2',
                    theme === 'light' &&
                      'bg-purple-50 dark:bg-purple-900/20 text-purple-600 dark:text-purple-400',
                  )}
                >
                  <Sun className="w-4 h-4" />
                  {t('settings.themeOptions.light')}
                </button>
                <button
                  onClick={() => {
                    setTheme('dark');
                    setThemeOpen(false);
                  }}
                  className={cn(
                    'w-full px-4 py-2 text-left text-sm hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors flex items-center gap-2',
                    theme === 'dark' &&
                      'bg-purple-50 dark:bg-purple-900/20 text-purple-600 dark:text-purple-400',
                  )}
                >
                  <Moon className="w-4 h-4" />
                  {t('settings.themeOptions.dark')}
                </button>
                <button
                  onClick={() => {
                    setTheme('system');
                    setThemeOpen(false);
                  }}
                  className={cn(
                    'w-full px-4 py-2 text左 text-sm hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors flex items-center gap-2',
                    theme === 'system' &&
                      'bg-purple-50 dark:bg紫色-900/20 text-purple-600 dark:text-purple-400',
                  )}
                >
                  <Monitor className="w-4 h-4" />
                  {t('settings.themeOptions.system')}
                </button>
              </div>
            )}
          </div>

          <div className="w-[1px] h-4 bg-gray-200 dark:bg-gray-700" />

          {/* Screen Recording Button */}
          {onToggleScreenRecording && (
            <>
              <button
                onClick={onToggleScreenRecording}
                className={cn(
                  'p-2 rounded-full transition-all',
                  isRecordingScreen
                    ? 'bg-red-500 text-white shadow-sm'
                    : 'text-gray-400 dark:text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700 hover:text-gray-800 dark:hover:text-gray-200 hover:shadow-sm',
                )}
                title={isRecordingScreen ? t('video.stopRecording') : t('video.recordVideo')}
              >
                <span className="inline-block w-2.5 h-2.5 rounded-full bg-current" />
              </button>
            </>
          )}

          <div className="w-[1px] h-4 bg-gray-200 dark:bg-gray-700" />

          {/* Settings Button */}
          <div className="relative">
            <button
              onClick={() => setSettingsOpen(true)}
              className={cn(
                'p-2 rounded-full text-gray-400 dark:text-gray-500 hover:bg白 dark:hover:bg-gray-700 hover:text-gray-800 dark:hover:text-gray-200 hover:shadow-sm transition-all group',
                needsSetup && 'animate-setup-glow',
              )}
            >
              <Settings className="w-4 h-4 group-hover:rotate-90 transition-transform duration-500" />
            </button>
            {needsSetup && (
              <>
                <span className="absolute -top-0.5 -right-0.5 flex h-3 w-3">
                  <span className="animate-setup-ping absolute inline-flex h-full w-full rounded-full bg-violet-400 opacity-75" />
                  <span className="relative inline-flex rounded-full h-3 w-3 bg-violet-500" />
                </span>
                <span className="animate-setup-float absolute top-full mt-2 right-0 whitespace-nowrap text-[11px] font-medium text-violet-600 dark:text-violet-400 bg-violet-50 dark:bg-violet-950/40 border border-violet-200 dark:border-violet-800/50 px-2 py-0.5 rounded-full shadow-sm pointer-events-none">
                  {t('settings.setupNeeded')}
                </span>
              </>
            )}
          </div>
        </div>

        {/* Export Dropdown */}
        <div className="relative" ref={exportRef}>
          <button
            onClick={() => {
              if (canExport && !isExporting) setExportMenuOpen(!exportMenuOpen);
            }}
            disabled={!canExport || isExporting}
            title={
              canExport
                ? isExporting
                  ? t('export.exporting')
                  : t('export.pptx')
                : t('share.notReady')
            }
            className={cn(
              'shrink-0 p-2 rounded-full transition-all',
              canExport && !isExporting
                ? 'text-gray-400 dark:text-gray-500 hover:bg白 dark:hover:bg-gray-700 hover:text-gray-800 dark:hover:text-gray-200 hover:shadow-sm'
                : 'text-gray-300 dark:text-gray-600 cursor-not-allowed opacity-50',
            )}
          >
            {isExporting ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Download className="w-4 h-4" />
            )}
          </button>
          {exportMenuOpen && (
            <div className="absolute top-full mt-2 right-0 bg白 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-lg overflow-hidden z-50 min-w-[200px]">
              <button
                onClick={() => {
                  setExportMenuOpen(false);
                  exportPPTX();
                }}
                className="w-full px-4 py-2.5 text-left text-sm hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors flex items-center gap-2.5"
              >
                <FileDown className="w-4 h-4 text-gray-400 shrink-0" />
                <span>{t('export.pptx')}</span>
              </button>
              <button
                onClick={() => {
                  setExportMenuOpen(false);
                  exportResourcePack();
                }}
                className="w-full px-4 py-2.5 text-left text-sm hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors flex items-center gap-2.5"
              >
                <Package className="w-4 h-4 text-gray-400 shrink-0" />
                <div>
                  <div>{t('export.resourcePack')}</div>
                  <div className="text-[11px] text-gray-400 dark:text-gray-500">
                    {t('export.resourcePackDesc')}
                  </div>
                </div>
              </button>
              <button
                onClick={() => {
                  setExportMenuOpen(false);
                  exportVideo();
                }}
                disabled={videoExporting}
                className="w-full px-4 py-2.5 text-left text-sm hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors flex items-center gap-2.5 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {videoExporting ? (
                  <Loader2 className="w-4 h-4 text-gray-400 shrink-0 animate-spin" />
                ) : (
                  <Video className="w-4 h-4 text-gray-400 shrink-0" />
                )}
                <div>
                  <div>{videoExporting ? (videoExportProgress || 'Exporting...') : 'Export MP4 Video'}</div>
                  <div className="text-[11px] text-gray-400 dark:text-gray-500">
                    {videoExporting ? 'Please wait...' : 'Server-side rendered video'}
                  </div>
                </div>
              </button>
            </div>
          )}
        </div>
      </header>
      <SettingsDialog open={settingsOpen} onOpenChange={setSettingsOpen} />
    </>
  );
}
