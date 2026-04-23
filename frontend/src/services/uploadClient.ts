/**
 * REST client for the NW-1202 upload endpoint.
 *
 * Single call — `POST /upload` multipart — returning the metadata
 * the reducer parks on `state.uploadedVideo.metadata`. The blob
 * URL for playback is created at the call site (not here) so the
 * caller owns the File handle and can decide when to revoke.
 */

import { API_BASE } from '../config'
import type { UploadMetadata } from '../types'

export async function uploadVideo(file: File): Promise<UploadMetadata> {
  const form = new FormData()
  form.append('file', file)

  const res = await fetch(`${API_BASE}/upload`, {
    method: 'POST',
    body: form,
  })

  if (!res.ok) {
    // FastAPI returns JSON `{detail: string}` on HTTPException;
    // non-JSON bodies (network errors reaching us as ok=false) fall
    // back to the status line.
    let detail = `POST /upload → ${res.status}`
    try {
      const body = (await res.json()) as { detail?: string }
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      // not JSON; use the fallback
    }
    throw new Error(detail)
  }

  return (await res.json()) as UploadMetadata
}
