import { apiBase, apiDelete, apiFetch, apiJson } from "./client";
import type { HistoryRow } from "./types";

export const listHistory = () => apiJson<HistoryRow[]>("/history");

export const clearHistory = () => apiDelete<{ cleared: true }>("/history");

export const deleteHistory = (id: string) => apiDelete<{ deleted: true }>(`/history/${id}`);

/** Full URL to a history row's generated WAV (for an <audio> element). */
export const historyAudioUrl = (id: string) =>
  apiBase().then((base) => `${base}/history/${id}/audio`);

/** The raw WAV bytes for a history row (GET /history/{id}/audio), via apiFetch so
 *  a missing/failed row throws ApiError instead of yielding a half-baked Blob.
 *  Used to export a past generation to disk through the native save dialog. */
export async function historyAudioBytes(id: string): Promise<Uint8Array> {
  const res = await apiFetch(`/history/${id}/audio`);
  return new Uint8Array(await res.arrayBuffer());
}
