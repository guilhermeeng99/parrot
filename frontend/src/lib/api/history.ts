import { apiBase, apiDelete, apiJson } from "./client";
import type { HistoryRow } from "./types";

export const listHistory = () => apiJson<HistoryRow[]>("/history");

export const clearHistory = () => apiDelete<{ cleared: true }>("/history");

export const deleteHistory = (id: string) => apiDelete<{ deleted: true }>(`/history/${id}`);

/** Full URL to a history row's generated WAV (for an <audio> element). */
export const historyAudioUrl = (id: string) =>
  apiBase().then((base) => `${base}/history/${id}/audio`);
