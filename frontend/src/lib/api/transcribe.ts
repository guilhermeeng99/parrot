import { apiJson, apiPost, apiUrl } from "./client";
import type { TranscribeDownloadEvent, TranscribeResult, TranscribeStatus } from "./types";

// Reference transcription (ipc-contract §6A). Mirrors setup.ts: a status fetch, a
// JSON-body download trigger, an SSE progress subscription, plus the multipart
// transcribe call that fills ref_text for cloning.

export const getTranscribeStatus = () => apiJson<TranscribeStatus>("/transcribe/status");

export const startTranscribeDownload = (model: string) =>
  apiPost<{ status: string; model: string }>("/transcribe/download", { model });

/** Subscribe to the SSE whisper-model download stream. Returns an unsubscribe fn. */
export async function subscribeTranscribeDownload(
  onEvent: (e: TranscribeDownloadEvent) => void,
  onError?: (err: Event) => void,
): Promise<() => void> {
  const url = await apiUrl("/transcribe/download-stream");
  const es = new EventSource(url);
  es.onmessage = (msg) => {
    try {
      onEvent(JSON.parse(msg.data) as TranscribeDownloadEvent);
    } catch {
      // ignore malformed payloads
    }
  };
  if (onError) es.onerror = onError;
  return () => es.close();
}

export interface TranscribeInput {
  audio: Blob;
  filename?: string;
  model: string;
  language?: string;
}

/** Transcribe a captured reference clip into a ref_text candidate. */
export function transcribeReference(input: TranscribeInput): Promise<TranscribeResult> {
  const fd = new FormData();
  fd.set("ref_audio", input.audio, input.filename ?? "reference.wav");
  fd.set("model", input.model);
  fd.set("language", input.language ?? "Auto");
  return apiPost<TranscribeResult>("/transcribe", fd);
}
