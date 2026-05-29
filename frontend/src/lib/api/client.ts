// Typed IPC client — the only place the UI knows the sidecar's address.
// The sidecar is loopback-only. In a packaged Tauri build the Rust supervisor
// chooses the port at RUNTIME (PARROT_PORT) and exposes it via the
// `backend_port` command, so the UI must ask for it rather than hardcode 3900 —
// otherwise a PARROT_PORT override would leave the webview talking to the wrong
// port. Outside Tauri (`bun run dev` in a browser) we fall back to the
// build-time VITE_PARROT_PORT, then to 3900. See ../../../docs/specs/ipc-contract.md.

/** True when running inside the Tauri webview (vs. a plain dev browser). */
export function inTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

async function resolvePort(): Promise<string> {
  if (inTauri()) {
    try {
      const { invoke } = await import("@tauri-apps/api/core");
      return String(await invoke<number>("backend_port"));
    } catch {
      // Fall through to the build-time/default port if the command is missing.
    }
  }
  return import.meta.env.VITE_PARROT_PORT ?? "3900";
}

let basePromise: Promise<string> | null = null;

/** The sidecar's base URL, resolved once at runtime and cached. */
export function apiBase(): Promise<string> {
  if (!basePromise) {
    const p = resolvePort().then((port) => `http://127.0.0.1:${port}`);
    // Don't permanently cache a failure — let a later call retry.
    p.catch(() => {
      if (basePromise === p) basePromise = null;
    });
    basePromise = p;
  }
  return basePromise;
}

/** Test-only: drop the cached base URL so the next `apiBase()` re-resolves. */
export function __resetApiBase(): void {
  basePromise = null;
}

export class ApiError extends Error {
  constructor(
    public path: string,
    public status: number,
  ) {
    super(`${path} → HTTP ${status}`);
    this.name = "ApiError";
  }
}

/** GET `path` on the sidecar and parse JSON, or throw `ApiError`. */
export async function apiJson<T>(path: string): Promise<T> {
  const base = await apiBase();
  const res = await fetch(`${base}${path}`);
  if (!res.ok) throw new ApiError(path, res.status);
  return (await res.json()) as T;
}
