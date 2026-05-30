import { get, writable } from "svelte/store";
import {
  type TranscribeDownloadEvent,
  type TranscribeModel,
  type TranscribeStatus,
  errMsg,
  getTranscribeStatus,
  startTranscribeDownload,
  subscribeTranscribeDownload,
  transcribeReference,
} from "$lib/api";
import { toasts } from "./toasts";

// Reference-transcription store (transcription.md §5). Two small machines:
//  - download: idle → downloading(pct) → verifying → idle (model now `downloaded`)
//  - transcription: idle → transcribing → done|error (fires on a fresh capture)
// Transcription is OPTIONAL: a failed status load is non-fatal — the clone flow
// still works with a manually typed ref_text.
type DownloadState = "idle" | "downloading" | "verifying" | "failed";
type TxState = "idle" | "transcribing" | "done" | "error";

interface State {
  status?: TranscribeStatus;
  selectedModel: string; // "" until status loads, then the default (or user choice)
  download: {
    state: DownloadState;
    pct: number | null; // null = indeterminate (resolving)
    filename?: string;
    message?: string;
    attempt?: number;
  };
  transcription: { state: TxState; text?: string; message?: string };
}

const store = writable<State>({
  selectedModel: "",
  download: { state: "idle", pct: null },
  transcription: { state: "idle" },
});
export const transcribe = { subscribe: store.subscribe };

// Only the download SSE needs an unsubscribe handle (like the setup store).
let unsubscribe: (() => void) | null = null;

export async function loadTranscribeStatus(): Promise<void> {
  try {
    const status = await getTranscribeStatus();
    store.update((s) => ({ ...s, status, selectedModel: s.selectedModel || status.default_model }));
  } catch (e) {
    // Non-fatal — transcription is opt-in. Surface it, keep the clone flow usable.
    toasts.error(`Couldn't load transcription models: ${errMsg(e)}`);
  }
}

export function selectModel(id: string): void {
  store.update((s) => ({ ...s, selectedModel: id, download: { state: "idle", pct: null } }));
}

export function modelById(id: string): TranscribeModel | undefined {
  return get(store).status?.models.find((m) => m.id === id);
}

export function isModelReady(id: string): boolean {
  return !!modelById(id)?.downloaded;
}

export async function downloadModel(): Promise<void> {
  const { selectedModel } = get(store);
  if (!selectedModel) return;
  store.update((s) => ({ ...s, download: { state: "downloading", pct: null } }));

  unsubscribe?.();
  unsubscribe = await subscribeTranscribeDownload(handleEvent, () => {
    /* transient EventSource errors are non-fatal; the download keeps running */
  });

  try {
    await startTranscribeDownload(selectedModel);
  } catch (e) {
    // Tear the stream down here: the POST rejected (e.g. 429 cooldown), so no
    // terminal event will arrive to run verify()'s cleanup — leaving the stream
    // open would leak it AND let a stray event resurrect this failed machine.
    unsubscribe?.();
    unsubscribe = null;
    store.update((s) => ({
      ...s,
      download: { ...s.download, state: "failed", message: errMsg(e) },
    }));
  }
}

function handleEvent(ev: TranscribeDownloadEvent) {
  if (ev.phase === "install_error") {
    store.update((s) => ({
      ...s,
      download: { ...s.download, state: "failed", message: ev.error ?? "Download failed." },
    }));
    return;
  }
  if (ev.phase === "install_done") {
    void verify();
    return;
  }
  store.update((s) => ({
    ...s,
    download: {
      state: "downloading",
      pct: ev.phase === "progress" ? ev.pct : null,
      filename: ev.filename || s.download.filename,
      attempt: ev.attempt,
    },
  }));
}

// Readiness is confirmed by a fresh status snapshot, not the SSE done alone
// (mirrors the setup gate): the model is `downloaded` only once status says so.
async function verify(): Promise<void> {
  store.update((s) => ({ ...s, download: { ...s.download, state: "verifying" } }));
  try {
    const status = await getTranscribeStatus();
    // Gate success on the fresh snapshot (like the setup gate): install_done with
    // the file actually missing (sha mismatch / rename race / disk full) must show
    // a failure, not silently drop to idle and re-offer the Download button.
    const ready = status.models.find((m) => m.id === get(store).selectedModel)?.downloaded;
    store.update((s) => ({
      ...s,
      status,
      download: ready
        ? { state: "idle", pct: null }
        : {
            ...s.download,
            state: "failed",
            message: "Download finished but the model file is missing — try again.",
          },
    }));
  } catch (e) {
    store.update((s) => ({
      ...s,
      download: { ...s.download, state: "failed", message: errMsg(e) },
    }));
  } finally {
    unsubscribe?.();
    unsubscribe = null;
  }
}

/** Auto-fire transcription for a captured clip. Returns the transcript ("" is a
 *  valid no-speech result), or null when the chosen model isn't ready / it failed. */
export async function runTranscription(
  audio: Blob,
  filename: string,
  language: string,
): Promise<string | null> {
  const { selectedModel } = get(store);
  if (!selectedModel || !isModelReady(selectedModel)) return null;
  store.update((s) => ({ ...s, transcription: { state: "transcribing" } }));
  try {
    const res = await transcribeReference({ audio, filename, model: selectedModel, language });
    store.update((s) => ({ ...s, transcription: { state: "done", text: res.text } }));
    return res.text;
  } catch (e) {
    const message = errMsg(e);
    store.update((s) => ({ ...s, transcription: { state: "error", message } }));
    toasts.error(message);
    return null;
  }
}

export function resetTranscription(): void {
  store.update((s) => ({ ...s, transcription: { state: "idle" } }));
}

/** Close the download SSE (e.g. on unmount) so a backgrounded Clone tab doesn't
 *  leak an open EventSource. Drops a mid-flight download back to idle so the
 *  picker re-offers it on return (the server dedupes a re-trigger by model id). */
export function cancelDownload(): void {
  unsubscribe?.();
  unsubscribe = null;
  store.update((s) =>
    s.download.state === "downloading" || s.download.state === "verifying"
      ? { ...s, download: { state: "idle", pct: null } }
      : s,
  );
}

/** SM-3 no-clobber: seed ref_text from a fresh transcript ONLY when the field is
 *  empty or still holds the previous auto-fill — never overwrite a hand-edit. */
export function shouldSeedTranscript(current: string, lastSeeded: string | null): boolean {
  return current === "" || current === lastSeeded;
}
