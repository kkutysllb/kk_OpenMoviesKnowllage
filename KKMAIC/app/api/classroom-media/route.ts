/**
 * Classroom Media Upload API
 *
 * POST /api/classroom-media - Upload audio/media files for a classroom
 */

import { type NextRequest, NextResponse } from 'next/server';
import { promises as fs } from 'fs';
import path from 'path';
import { CLASSROOMS_DIR, isValidClassroomId } from '@/lib/server/classroom-storage';
import { apiError, apiSuccess } from '@/lib/server/api-response';

/**
 * POST - Upload media file for a classroom
 */
export async function POST(req: NextRequest) {
  try {
    const formData = await req.formData();
    const classroomId = formData.get('classroomId') as string;
    const audioId = formData.get('audioId') as string;
    const file = formData.get('file') as File | null;

    if (!classroomId || !audioId || !file) {
      return apiError('MISSING_REQUIRED_FIELD', 400, 'Missing required fields: classroomId, audioId, file');
    }

    if (!isValidClassroomId(classroomId)) {
      return apiError('INVALID_REQUEST', 400, 'Invalid classroom ID');
    }

    // Create audio directory
    const audioDir = path.join(CLASSROOMS_DIR, classroomId, 'audio');
    await fs.mkdir(audioDir, { recursive: true });

    // Save audio file
    const filePath = path.join(audioDir, `${audioId}.mp3`);
    const buffer = Buffer.from(await file.arrayBuffer());
    await fs.writeFile(filePath, buffer);

    return apiSuccess({ 
      audioId, 
      path: `/api/classroom-media/${classroomId}/audio/${audioId}.mp3` 
    });
  } catch (err) {
    console.error('Media upload error:', err);
    return apiError(
      'INTERNAL_ERROR',
      500,
      err instanceof Error ? err.message : 'Failed to upload media'
    );
  }
}
