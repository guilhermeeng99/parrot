import { apiJson, apiPost, apiUrl } from "./client";
import type { DownloadEvent, SetupStatus } from "./types";

export const getSetupStatus = () => apiJson<SetupStatus>("/setup/status");

export const startDownload = (repoId: string) =>
  apiPost<{ status: string; repo_id: string }>("/setup/download", { repo_id: repoId });

/** Subscribe to the SSE download-progress stream. Returns an unsubscribe fn.
 *  Comment lines (`: keepalive`) carry no `data:` and are ignored by EventSource. */
export async function subscribeDownload(
  onEvent: (e: DownloadEvent) => void,
  onError?: (err: Event) => void,
): Promise<() => void> {
  const url = await apiUrl("/setup/download-stream");
  const es = new EventSource(url);
  es.onmessage = (msg) => {
    try {
      onEvent(JSON.parse(msg.data) as DownloadEvent);
    } catch {
      // ignore malformed payloads
    }
  };
  if (onError) es.onerror = onError;
  return () => es.close();
}
