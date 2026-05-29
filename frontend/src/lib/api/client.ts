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

/** Resolve a full URL for a sidecar path (used by EventSource, which is sync). */
export async function apiUrl(path: string): Promise<string> {
  return `${await apiBase()}${path}`;
}

/** Test-only: drop the cached base URL so the next `apiBase()` re-resolves. */
export function __resetApiBase(): void {
  basePromise = null;
}

export class ApiError extends Error {
  status: number;
  detail?: unknown;

  constructor(public path: string, status: number, detail?: unknown) {
    const tail = typeof detail === "string" && detail ? ` — ${detail}` : "";
    super(`${path} → HTTP ${status}${tail}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

/** Parse the sidecar error envelope: `{ detail }` | `{ error }` | raw text. */
async function readError(res: Response): Promise<unknown> {
  try {
    const body = await res.clone().json();
    if (body && typeof body === "object") {
      if ("detail" in body) return (body as { detail: unknown }).detail;
      if ("error" in body) return (body as { error: unknown }).error;
    }
  } catch {
    // not JSON — fall back to text
  }
  try {
    return await res.text();
  } catch {
    return undefined;
  }
}

/** Fetch a sidecar path, throwing `ApiError` (with parsed `detail`) on !ok. */
export async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  const base = await apiBase();
  const res = await fetch(`${base}${path}`, init);
  if (!res.ok) throw new ApiError(path, res.status, await readError(res));
  return res;
}

/** GET `path` on the sidecar and parse JSON, or throw `ApiError`. */
export async function apiJson<T>(path: string): Promise<T> {
  const res = await apiFetch(path);
  return (await res.json()) as T;
}

/** POST a body (FormData passes through; objects are JSON-encoded) → parsed JSON. */
export async function apiPost<T>(path: string, body?: FormData | object): Promise<T> {
  const res = await apiPostRaw(path, body);
  return (await res.json()) as T;
}

/** POST and return the raw Response (callers read `X-*` headers / stream body). */
export async function apiPostRaw(
  path: string,
  body?: FormData | object,
  init?: RequestInit,
): Promise<Response> {
  const isForm = body instanceof FormData;
  return apiFetch(path, {
    method: "POST",
    body: isForm ? body : body !== undefined ? JSON.stringify(body) : undefined,
    headers: isForm || body === undefined ? undefined : { "Content-Type": "application/json" },
    ...init,
  });
}

export async function apiPut<T>(path: string, body: object): Promise<T> {
  const res = await apiFetch(path, {
    method: "PUT",
    body: JSON.stringify(body),
    headers: { "Content-Type": "application/json" },
  });
  return (await res.json()) as T;
}

export async function apiDelete<T>(path: string): Promise<T> {
  const res = await apiFetch(path, { method: "DELETE" });
  return (await res.json()) as T;
}

/** User-facing message for any thrown error — prefers the sidecar `detail`. */
export function errMsg(e: unknown): string {
  if (e instanceof ApiError && typeof e.detail === "string" && e.detail) return e.detail;
  if (e instanceof Error) return e.message;
  return String(e);
}
