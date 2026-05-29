# Settings

The minimal configuration surface for Parrot. There are exactly three groups: **Appearance** (theme + UI zoom, persisted locally on the frontend), **Engine status** (read-only — the single `omnivoice` backend plus the detected compute device), and an optional **Hugging Face token** (encrypted at rest, used solely to download the gated voice model on first run). Everything beyond these three is explicitly out of scope (see [Non-goals](#non-goals)).

Appearance is owned by the frontend prefs store and never touches the sidecar. The HF token and engine status are owned by the Python sidecar; the UI reads them over the loopback REST surface. See [../../CLAUDE.md](../../CLAUDE.md) for project-wide conventions, [device-detection.md](./device-detection.md) for how the device string is computed, and [first-run-setup.md](./first-run-setup.md) for the model-download flow the token feeds.

## Entity Contract

Settings live in two places with deliberately different durability guarantees.

### 1. Sidecar — `settings` table (SQLite, authoritative for secrets)

The canonical key/value store described in [../../CLAUDE.md](../../CLAUDE.md). Created idempotently on boot and alembic-migrated; survives `parrot_data/` upgrades with no manual migration.

```text
settings
  key         TEXT PRIMARY KEY
  value       TEXT NOT NULL      -- ciphertext for secrets; raw text for non-secrets
  updated_at  REAL NOT NULL      -- epoch seconds of last write

Rows Parrot uses:
  hf_token          -- Fernet ciphertext of the HF access token (NEVER plaintext)
  _secret_key_salt  -- 16 random bytes, base64; per-install KDF salt (engine-managed)
```

Invariants:

- `hf_token.value` is **always** Fernet ciphertext. No code path writes a raw token to this row. A misrouted plaintext write through the non-secret helper must be rejected, not silently stored.
- The Fernet key is derived per-install via scrypt over `(OS machine-id, _secret_key_salt)`. It is **not** at-rest portable: copying `parrot.db` to another machine yields a key that cannot decrypt `hf_token`.
- `_secret_key_salt` is written inside the same transaction as the first `hf_token` write — no torn state where ciphertext exists without its salt.
- Clearing the token deletes the `hf_token` row but **preserves** `_secret_key_salt`, so a re-save by the same user on the same machine round-trips correctly.
- Parrot does **not** persist theme, zoom, or any non-secret config in this table. The only sidecar-owned setting is the token.

### 2. Frontend — prefs store (local, authoritative for appearance)

Appearance preferences are UI-only and live in a Svelte prefs store backed by WebView `localStorage`, never sent to the sidecar.

```ts
interface AppearancePrefs {
  theme: 'light' | 'dark';   // default 'dark'
  zoom: number;              // UI scale, default 1.0; clamped to [0.8, 1.5]
}
```

Invariants:

- `theme` and `zoom` are readable and applied **before** the sidecar is reachable — appearance must never block on a healthy backend.
- `zoom` is clamped on read and on write; an out-of-range or non-numeric stored value falls back to `1.0`.
- These prefs are independent of `parrot_data/` and do not require the DB, the `settings` table, or any sidecar IPC.

### 3. Engine status (read-only, derived — not stored)

Engine status is computed on demand, never persisted. Parrot ships exactly one engine.

```ts
interface EngineStatus {
  active: 'omnivoice';   // always; single-engine build
  device: string;        // detected compute device ∈ {"cuda","mps","rocm","cpu"}
}
```

### HF token cascade shape (read model)

The token read API returns a masked source descriptor. Parrot resolves the token in a fixed order: first the in-app encrypted setting in the `settings` table (the documented default path), then the `HF_TOKEN` environment variable (a documented power-user override read at engine import time). The read model never reveals the raw token.

```ts
interface TokenSource {
  source: 'app' | 'env';   // 'env' shown read-only when HF_TOKEN is exported
  set: boolean;            // a token exists for this source
  masked: string | null;   // "hf_…<last 3 chars>" — never the full token
  whoami_user: string | null; // HF username if validated, else null
  whoami_ok: boolean;      // true iff huggingface_hub.whoami() succeeded
}

interface TokenState {
  active: 'app' | 'env' | null; // highest-priority source that validated
  sources: TokenSource[];
}
```

## Business Rules

1. **Single engine, no picker.** `EngineStatus.active` is always `"omnivoice"`. There is no engine-selection write path; the engine-status endpoint is strictly read-only.
2. **Token is optional for synthesis.** Cloning and speaking with an already-downloaded model never require a token. The token is used **only** to download the gated model on first run; if the model is already present, an absent or invalid token must not block synthesis.
3. **Token is never displayed in plaintext.** Every read returns at most `masked` (`hf_…<last 3>`). No endpoint, log line, or error message echoes the full token.
4. **Token stored encrypted at rest.** A set token is Fernet-encrypted with the per-install key before it touches disk. A copied `parrot.db` cannot decrypt it on another machine; that case degrades to "no token", never to an error that blocks the UI.
5. **Set validates and reports.** Saving a token persists the ciphertext, then re-reports the cascade state including a fresh `whoami` result so the UI can show `whoami_ok` / `whoami_user` immediately.
6. **Clear is idempotent and safe.** Clearing removes the `hf_token` row, keeps the salt, and invalidates the validation cache. Clearing when no token is set is a successful no-op.
7. **Invalid token is surfaced, not swallowed.** A token whose `whoami` fails is still stored (the user may be offline) but is reported `set: true, whoami_ok: false, whoami_user: null` so the UI can warn.
8. **Validation is cached.** `whoami` results are cached ~300 s per token so repeated Settings reads do not hammer the HF API. Set and clear both invalidate the cache; a "Test now" action invalidates and re-validates.
9. **Appearance is sidecar-independent.** Theme and zoom apply purely on the frontend, persist locally in `localStorage`, and work with the sidecar down or starting (Rule applies to first-paint, see [Edge Cases](#edge-cases)).
10. **Theme default is dark; zoom default is 1.0.** Missing/corrupt prefs fall back to these without error.
11. **Loopback-only.** All sidecar settings endpoints (token read/write, engine status) are loopback-gated: a non-loopback origin gets `403` before the handler runs. The UI only ever calls them over `http://127.0.0.1:3900`.
12. **`updated_at` advances on every write.** Both set and the salt write stamp `updated_at` with the current epoch seconds.

## IPC Contract

All REST endpoints live on the Python sidecar at `http://127.0.0.1:3900` and are loopback-gated. Routes are unprefixed (no `/api/` prefix), consistent with the rest of the surface.

### Token

#### `GET /settings/hf-token`

Returns the masked token cascade for the Settings panel.

- **Returns** `200` → `TokenState` (see [read model](#hf-token-cascade-shape-read-model)). Never includes the raw token; only the `masked` descriptor.
- **Errors:** non-loopback origin → `403`. The handler is read-only and must not throw on a missing/undecryptable token — it reports `set: false` / `whoami_ok: false` instead.

#### `POST /settings/hf-token`

Persist (encrypt + store) a token and return the refreshed state.

- **Body** (JSON): `{ "token": string }` — `min_length: 1`, trimmed server-side.
- **Behavior:** encrypts and writes the `hf_token` row, populates the HF canonical credential file via `huggingface_hub.login(add_to_git_credential=False)` (best-effort; failure is non-fatal because the encrypted store already holds the token), invalidates the validation cache, then re-validates.
- **Returns** `200` → updated `TokenState`.
- **Errors:** empty/whitespace token → `400`; persistence failure → `500`; non-loopback origin → `403`.

#### `DELETE /settings/hf-token`

Clear the stored token.

- **Behavior:** deletes the `hf_token` row, preserves `_secret_key_salt`, invalidates the validation cache.
- **Returns** `200` → updated `TokenState` (with the app source now `set: false`).
- **Errors:** clear failure → `500`; non-loopback origin → `403`. Clearing when nothing is set returns `200`.

### Engine status

#### `GET /engine/status`

Read-only single-engine status. This is the only place the device is reported to the UI.

- **Returns** `200` → `{ "active": "omnivoice", "device": "<detected device>" }`, where `device ∈ {"cuda","mps","rocm","cpu"}`. The `device` value is the same string described in [device-detection.md](./device-detection.md).
- **Errors:** non-loopback origin → `403`. Must not throw — on detection failure it returns `device: "cpu"`.

> Dropped vs. OmniVoice: there is no engines-list family, no engine-select route, no per-engine `/health` test button in the Settings UI (the supervisor's `GET /healthz` covers liveness — see [architecture.md](./architecture.md)), and no license-acceptance, performance-toggle, logs, or model-store routes.

### Appearance

Appearance has **no IPC**. Theme and zoom are read/written entirely through the frontend prefs store (`localStorage`). Applying zoom or theme may use a Tauri command for native window chrome, but no sidecar call is involved and the `settings` table is never touched.

## State Machines

### Appearance store (frontend)

```text
                 hydrate (read local prefs, clamp zoom)
   [uninitialized] ───────────────────────────────────► [ready]
        │                                                  │
        │ no/corrupt prefs                                 │ setTheme / setZoom
        ▼                                                  ▼
   [ready: defaults]  ◄─────────────────────────────  [ready: persisting]
   (theme=dark, zoom=1.0)        write local + apply
```

- `hydrate` runs at app start, independent of sidecar health, so first paint already has the right theme.
- `setTheme` / `setZoom` apply immediately to the DOM/window, then persist to `localStorage`. Persistence failure logs and keeps the in-memory value (appearance still works for the session).

### HF token store (frontend)

```text
   [idle] ──load──► [loading] ──ok──► [resolved] (state from GET /settings/hf-token)
                        │                  │
                     error            ┌────┴───────────────┐
                        ▼          submit                 clear
                   [error]      [saving] ──► [resolved]  [clearing] ──► [resolved]
                                   │                          │
                               500/400                    500
                                   ▼                          ▼
                                [error]                    [error]

   [resolved] sub-states reflect the active source:
     • valid    → active != null && whoami_ok
     • invalid  → set==true && whoami_ok==false   (banner: "Token saved but not valid")
     • absent   → set==false                       (banner: "No token — gated download disabled")
```

- `saving` and `clearing` both transition through the server-returned `TokenState`, so the cascade table re-renders with fresh `whoami` results without a second round-trip.
- A "Test now" action forces a reload (server invalidates its validation cache first), moving `resolved → loading → resolved`.

## Edge Cases

- **Token set but invalid.** `whoami` returns 401/403 (revoked/typo) or the network is down. The token is still stored; state reports `set: true, whoami_ok: false`. The UI shows a non-blocking "saved but not valid" banner. Synthesis with an already-downloaded model is unaffected; only first-run gated download is blocked (see [first-run-setup.md](./first-run-setup.md)).
- **Token offline at save time.** `whoami` can't reach HF. Treat identically to "invalid" (`whoami_ok: false`) — the negative result is cached for ~300 s, so a later "Test now" recovers once connectivity returns.
- **`parrot.db` copied across machines.** The per-install Fernet key no longer matches; decrypting `hf_token` raises `InvalidToken`. The read path logs a warning and reports `set: false` (degrade to "no token"), never a `500` that blocks the panel. The user simply re-enters the token.
- **Theme applied before sidecar ready.** Appearance hydrates from local prefs at first paint; it must not await `/healthz`, `/engine/status`, or any sidecar call. The engine-status section may render a "starting…" placeholder while the rest of Settings is fully interactive.
- **Settings write while sidecar down.** A `POST`/`DELETE` to the token endpoints fails at the transport layer (connection refused). The token store enters `error` and the UI shows "Engine not running — can't save token" with a retry; no partial write occurs. Appearance writes are unaffected because they never reach the sidecar.
- **Engine status when device detection fails.** `/engine/status` returns `device: "cpu"` rather than throwing, matching Rule 2's "synthesis still works" guarantee (CPU is slower, see the `device-detection.md` CPU notes).
- **Corrupt / unreadable local prefs.** Non-JSON or out-of-range values fall back to `theme=dark`, `zoom=1.0`; a clamped zoom is re-persisted on next write.
- **Misrouted plaintext write to `hf_token`.** Any attempt to write the `hf_token` key through the non-secret text path is rejected with an error — the encrypted row can never be overwritten with plaintext.
- **Concurrent set + clear.** Both serialize through the single SQLite writer (WAL); `INSERT OR REPLACE` and `DELETE` are atomic. Last write wins; the validation cache is invalidated by whichever completes last, so the next state read is consistent.

## Non-goals

These are present in OmniVoice's settings surface and are **deliberately removed** from Parrot. None should be documented as present:

- Multi-engine picker / engine-select / per-engine health-test buttons (Parrot ships one engine).
- Performance-tuning panels (e.g. `torch.compile` toggle, idle-timeout, CPU-pool config).
- Model store / download manager UI beyond first-run (see [first-run-setup.md](./first-run-setup.md)).
- Logs tab / log streaming / log clearing in Settings.
- License-acceptance dialogs.
- Capture / global-shortcut rebinding.
- Any cloud, account, or telemetry settings.

## Data

| Store | Location | Read | Written |
|-------|----------|------|---------|
| `settings` table — `hf_token` | `parrot_data/parrot.db` (SQLite, WAL) | `GET /settings/hf-token` | `POST`/`DELETE /settings/hf-token` |
| `settings` table — `_secret_key_salt` | `parrot_data/parrot.db` | engine KDF (internal) | first token set (engine-managed) |
| Appearance prefs (`theme`, `zoom`) | frontend `localStorage` (WebView, via Svelte prefs store) | Appearance store hydrate | `setTheme` / `setZoom` |
| Engine status (`active`, `device`) | derived at request time — **not stored** | `GET /engine/status` | n/a |

- The `hf_token` row is the only sidecar-owned Parrot setting; it survives `parrot_data/` upgrades via the idempotent + alembic-migrated `settings` table with no manual migration.
- Appearance prefs are independent of `parrot_data/`, live in WebView `localStorage`, and require no migration.
- The HF model weights downloaded using the token are cached in the HF cache dir (see [first-run-setup.md](./first-run-setup.md)), not in `parrot_data/`.
