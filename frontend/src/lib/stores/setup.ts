import { writable } from "svelte/store";
import {
  type DownloadEvent,
  type SetupStatus,
  errMsg,
  getSetupStatus,
  startDownload as startDownloadApi,
  subscribeDownload,
} from "$lib/api";

// Setup-gate store (first-run-setup §5). Readiness is driven by /setup/status,
// not by the SSE `install_done` alone — `install_done` only moves us to
// `verifying`; `ready` requires a fresh status snapshot confirming the cache.
type GateState =
  | "checking"
  | "ready"
  | "needs_download"
  | "downloading"
  | "verifying"
  | "download_failed"
  | "needs_token";

interface State {
  state: GateState;
  status?: SetupStatus;
  pct: number | null; // null = indeterminate (resolving)
  filename?: string;
  message?: string;
  attempt?: number;
}

const store = writable<State>({ state: "checking", pct: null });
export const setup = { subscribe: store.subscribe };

let unsubscribe: (() => void) | null = null;

function closeDownloadStream(): void {
  unsubscribe?.();
  unsubscribe = null;
}

export async function checkSetup(): Promise<void> {
  store.update((s) => ({ ...s, state: "checking" }));
  try {
    const status = await getSetupStatus();
    store.set({ state: status.models_ready ? "ready" : "needs_download", status, pct: null });
  } catch (e) {
    store.set({ state: "download_failed", pct: null, message: errMsg(e) });
  }
}

function looksGated(text: string): boolean {
  return /gated|401|403|unauthorized|forbidden/i.test(text);
}

export async function startDownload(): Promise<void> {
  const repo = currentRepo();
  if (!repo) return;
  store.update((s) => ({ ...s, state: "downloading", pct: null, message: undefined }));

  try {
    closeDownloadStream();
    unsubscribe = await subscribeDownload(handleEvent, () => {
      /* transient EventSource errors are non-fatal; the download keeps running */
    });
    await startDownloadApi(repo);
  } catch (e) {
    closeDownloadStream();
    const message = errMsg(e);
    store.update((s) => ({
      ...s,
      state: looksGated(message) ? "needs_token" : "download_failed",
      message,
    }));
  }
}

function handleEvent(ev: DownloadEvent) {
  if (ev.phase === "install_error") {
    closeDownloadStream();
    store.update((s) => ({
      ...s,
      state: looksGated(ev.error ?? "") ? "needs_token" : "download_failed",
      message: ev.error ?? "Download failed.",
    }));
    return;
  }
  if (ev.phase === "install_done") {
    verify();
    return;
  }
  store.update((s) => ({
    ...s,
    state: "downloading",
    pct: ev.phase === "progress" ? ev.pct : null,
    filename: ev.filename || s.filename,
    attempt: ev.attempt,
  }));
}

async function verify(): Promise<void> {
  store.update((s) => ({ ...s, state: "verifying" }));
  try {
    const status = await getSetupStatus();
    store.set({ state: status.models_ready ? "ready" : "download_failed", status, pct: null });
  } catch (e) {
    store.update((s) => ({ ...s, state: "download_failed", message: errMsg(e) }));
  } finally {
    closeDownloadStream();
  }
}

function currentRepo(): string | undefined {
  let repo: string | undefined;
  store.subscribe((s) => (repo = s.status?.missing[0]?.repo_id))();
  return repo;
}
