import { apiBase, apiDelete, apiFetch, apiJson } from "./client";
import type { HistoryRow } from "./types";

export const listHistory = () => apiJson<HistoryRow[]>("/history");

export const clearHistory = () => apiDelete<{ cleared: true }>("/history");

export const deleteHistory = (id: string) => apiDelete<{ deleted: true }>(`/history/${id}`);

/** Full URL to a history row's generated WAV (for an <audio> element). */
export const historyAudioUrl = (id: string) =>
  apiBase().then((base) => `${base}/history/${id}/audio`);

/** Full URL to a history row's clip re-encoded as MP3 (the export-as-mp3 path). */
export const historyAudioMp3Url = (id: string) =>
  apiBase().then((base) => `${base}/history/${id}/audio.mp3`);

/** The clip re-encoded as MP3 bytes (GET /history/{id}/audio.mp3), via apiFetch so
 *  a missing/failed row throws ApiError instead of yielding a half-baked Blob.
 *  Used to export a generation to disk (MP3) through the native save dialog. */
export async function historyAudioMp3Bytes(id: string): Promise<Uint8Array> {
  const res = await apiFetch(`/history/${id}/audio.mp3`);
  return new Uint8Array(await res.arrayBuffer());
}
