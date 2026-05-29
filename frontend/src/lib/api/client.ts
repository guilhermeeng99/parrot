// Typed IPC client — the only place the UI knows the sidecar's address.
// The sidecar is loopback-only on port 3900 (override with VITE_PARROT_PORT for
// dev against a sidecar on a different port). See ../../../docs/specs/ipc-contract.md.

const PORT = import.meta.env.VITE_PARROT_PORT ?? "3900";

export const API_BASE = `http://127.0.0.1:${PORT}`;

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
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) throw new ApiError(path, res.status);
  return (await res.json()) as T;
}
