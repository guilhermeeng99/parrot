# First-Run Setup

The make-or-break path. From a fresh install, Parrot must reach a state where the user can clone a voice and speak text — without hitting a wall — and then run fully offline forever. This spec defines the boot ordering, the model-download gate, HF token handling, and every failure the path must survive. See [../../CLAUDE.md](../../CLAUDE.md) for the local-first constraints this spec implements.

First run is the only run that needs the network. Everything after it is offline.

## 1 — Boot Ordering (the contract)

The three processes hand off in a strict order. Each step is observable so the splash UI can render exactly where boot is.

1. **Rust supervisor spawns the sidecar.** On launch the supervisor first probes `127.0.0.1:3900` (`GET /healthz`). If something already answers as Parrot's engine, it attaches and skips spawning. Otherwise it bootstraps the Python venv (step 2), then launches the sidecar via the venv Python.
2. **Sidecar bootstraps the venv (first run only).** If no venv exists, the supervisor uses the bundled `uv` to create a Python 3.11 venv at `parrot_data/.venv` and run `uv sync` to install dependencies (PyTorch + transformers). This is the longest step on first run (multiple minutes). On every later launch the venv already exists and this is skipped.
3. **Sidecar starts and reports `models_ready=false`.** Once `GET /healthz` succeeds (returning `{"status":"ok"}`), the supervisor stops blocking the window. The UI calls `GET /setup/status`; on a fresh install the model weights are not cached, so `models_ready` is `false`.
4. **UI shows the download gate.** The user is held on a setup screen (cannot clone or speak yet) and a single primary action downloads the model.
5. **Model downloads from Hugging Face.** The UI opens the SSE progress stream (`GET /setup/download-stream`) and the sidecar pulls the engine weights (hundreds of MB) into the HF cache. Progress is reported live.
6. **`models_ready=true` → app usable.** Once the cache contains the model, `GET /setup/status` flips `models_ready` to `true`, the gate clears, and the user lands on the clone/speak surface.
7. **Offline forever after.** Subsequent launches re-detect the cached model, skip the gate, and never touch the network. No accounts, no telemetry, no required cloud calls.

> Boot stage (steps 1–2, owned by the Rust supervisor) and download stage (steps 3–6, owned by the sidecar) are distinct progress surfaces. The supervisor's stages are venv/process lifecycle; the sidecar's stages are model presence on disk.

## 2 — Entity Contract

### Setup status (read model)

`GET /setup/status` is a stateless snapshot computed each call from the HF cache and the volume's free space. There is no persisted "setup complete" flag — readiness is derived from whether the required model is on disk.

```
SetupStatus
  models_ready  : bool        # true iff every required model is cached with size_on_disk > 0
  missing       : [{repo_id: str, label: str}]   # required models not yet cached (empty when ready)
  hf_cache_dir  : str         # absolute path the weights download into (see §7)
  disk_free_gb  : float       # free GB on the volume holding hf_cache_dir, rounded to 2dp
  min_free_gb   : int         # minimum free space the download needs (= 10)
  enough_disk   : bool        # disk_free_gb >= min_free_gb
```

Invariants:
- `models_ready == (len(missing) == 0)`.
- `enough_disk == (disk_free_gb >= min_free_gb)`.
- `disk_free_gb` is best-effort: if the cache path doesn't exist yet, the check walks up to the nearest existing ancestor volume; on any probe error it reports `0.0` (and `enough_disk` is therefore `false`).
- A model counts as cached only when its on-disk size is `> 0` — a half-written or zero-byte snapshot is treated as **not** ready, so an interrupted download re-gates the user instead of letting a corrupt model through.
- `repo_id` values in `missing` reference the OmniVoice model repo (the concrete repository id is configuration, not part of this contract).

### HF token (optional)

The token is needed **only** for gated model repos. The default Parrot engine is ungated, so first run succeeds with no token at all. When present, the token resolves from two sources in priority order:

```
hf_token resolution (highest → lowest)
  1. app : in-app encrypted setting in the settings table (key = "hf_token", Fernet-encrypted at rest, per-install key) — the documented default path
  2. env : HF_TOKEN environment variable — a documented power-user override
```

Invariants:
- Exactly one place in the backend reads tokens: the resolver. No bare `os.environ["HF_TOKEN"]` reads elsewhere.
- The `app` source is encrypted with a **per-install** Fernet key. A `parrot_data/` copied to another machine fails to decrypt; the resolver logs a warning and falls through to the `HF_TOKEN` env var rather than erroring.
- The two sources are read at HF-library import time; on conflict the in-app encrypted setting wins (it is the documented default path), and `HF_TOKEN` is the documented power-user override used when no in-app token is set.
- Any value matching `hf_[A-Za-z0-9]{30,}` is redacted to `hf_***REDACTED***` before any log handler formats it. The token never appears in `backend.log`, the splash log panel, an SSE event, or an error surfaced to the UI.
- Read APIs only ever expose a masked form `hf_…<last 3 chars>`; the raw token is never returned.

## 3 — Business Rules

1. **The gate is hard.** While `models_ready == false`, the clone and speak surfaces are unreachable. The only allowed action on the setup screen is to start (or retry) the model download.
2. **Readiness is derived, never persisted.** Deleting the model from disk (or a corrupt/zero-byte snapshot) MUST re-gate the user on next launch. There is no "I already set up" flag to go stale.
3. **No token is required for the default engine.** A first run with no network credentials and an ungated default model MUST complete end-to-end. A token is requested only when a download returns a gated/401/403 error.
4. **Download is resumable and idempotent.** Re-running the download after an interruption resumes from the HF cache rather than restarting from zero. Starting a download for a model already fully cached is a no-op that immediately reports done.
5. **Disk is checked before and gated during.** `setup/status` exposes `enough_disk`; the UI MUST warn (and SHOULD block) the download when `enough_disk == false` (`< 10 GB` free), because a mid-download `ENOSPC` corrupts the snapshot.
6. **Offline after first run.** Once `models_ready == true`, no setup-path code may make a network call. Re-launching with the network off MUST still reach the usable app.
7. **The default path stays robust on Windows.** The default download path, gate, and offline behavior are defined for Windows 10/11 (x64). The Windows cache-path workaround (§7) and region mirror selection (§6, edge cases) are implementation details that keep that default path working; they do not change user-visible default behavior.
8. **A failed download cools down.** After a download error for a repo, an immediate retry of the **same** repo within the cooldown window is rejected with a clear "retry in N s" message, so a user mashing the button can't stampede a flaky network.
9. **Token writes never leak to git.** Saving an in-app token persists it encrypted and also primes the canonical HF file via `login()`, but MUST pass `add_to_git_credential=False` so the token is never written to the user's global git credential helper.

## 4 — IPC Contract

All endpoints are on the sidecar at `http://127.0.0.1:3900`. Routes are unprefixed (no `/api/` prefix).

### `GET /setup/status`
- **Returns** the `SetupStatus` shape from §2. `200` always (it's a snapshot, not an action).
- **Errors:** none in normal operation; a sidecar that isn't up yet simply fails the supervisor's `/healthz` probe and the UI stays on the boot splash.

### `POST /setup/download`
- **Body:** `{ "repo_id": str }` — the OmniVoice model repo to fetch (validated against the known-models catalog).
- **Behavior:** starts the snapshot download in the background; progress flows over `/setup/download-stream`. Internally retries transient network/`OSError` failures with exponential backoff before emitting `install_error`. On Windows it disables symlinks for the snapshot (see §7). Starting a download for a model already fully cached is a no-op that immediately reports done (Rule 4).
- **Returns:** `{ "status": "download_started", "repo_id": str }`.
- **Errors:**
  - `400` — `repo_id` is not in the known-models catalog.
  - `429` — the same `repo_id` failed recently and is in cooldown; detail says how many seconds remain.

### `GET /setup/download-stream`  (SSE)
- **Returns** `text/event-stream`. Each `data:` line is one JSON progress event:
  ```
  { repo_id, filename, downloaded, total, pct, phase, rate? }
  ```
  - `pct` is `0.0–1.0`. `phase ∈ { install_start, resolving, progress, install_retry, install_done, install_error }`.
  - `resolving` heartbeats fire (~every 2 s) while repo metadata resolves, before byte-level bars appear, so the UI shows motion instead of a frozen 0%.
  - `install_retry` carries `attempt` and a redacted `error`; `install_error` carries the final redacted `error`.
- Sends `: keepalive` comments on idle (~30 s) so proxies don't drop the stream.
- **Headers:** `Cache-Control: no-cache, no-transform`, `X-Accel-Buffering: no`.
- **Errors:** the stream itself doesn't fail download work; download failures arrive as `install_error` events on this channel. A client that disconnects mid-stream is logged server-side; no status is returned to the gone client.

### `GET /engine/status`  (read-only)
- **Returns** `{ "active": "omnivoice", "device": "<id>" }` where `device ∈ { "cuda", "cpu" }` (a human label may be added as `device_label`). Parrot ships exactly one fixed engine; this exists so the UI doesn't special-case a missing picker and can show the active compute device. There is no engine-selection action and no `backends` array.

### `GET /healthz`  (supervisor readiness probe)
- **Returns** `{ "status": "ok" }` with `200` once the FastAPI app is serving. Used **only** by the Rust supervisor to decide the sidecar is up and to leave the boot splash. Fast, no torch import, no `device` field. It does **not** imply `models_ready` — that's a separate `/setup/status` call.
- **Errors:** connection refused / timeout while the sidecar is still starting; the supervisor polls until healthy or its boot timeout elapses (see §6 edge case "sidecar fails to start").

### Token endpoints (only relevant when a gated model is requested)
The HF token is set, read, and cleared through three unprefixed endpoints backed by the encrypted `settings` table:
- `GET /settings/hf-token` — returns the token in **masked** form (`hf_…<last 3 chars>`) plus whether a value is set; never returns the raw token.
- `POST /settings/hf-token` — sets the in-app token. Persists it encrypted in the `settings` table and primes the canonical HF file via `login(add_to_git_credential=False)` (Rule 9).
- `DELETE /settings/hf-token` — clears the in-app token from the `settings` table.

These only intersect first-run when a download hits a gated repo; the default ungated engine never requires them. The full settings contract lives in [settings.md](settings.md).

## 5 — State Machines

### Supervisor boot stage (Rust → splash UI, `bootstrap-log` + stage events)

```
checking
  → downloading_uv      (only if no usable uv binary cached)
  → creating_venv       (first run only; venv at parrot_data/.venv)
  → installing_deps     (first run / repair; uv sync, the long pole)
  → starting_backend    (spawned uvicorn; polling /healthz)
  → ready               (/healthz OK → splash dismissed)

any → failed { message }    (carries reason + tail of backend stderr)
```
- `failed` is recoverable: the UI offers **Retry** (re-run from `checking`) and **Clean & Retry** (wipe the bootstrapped project dir, kill any stale process on the port, then retry).
- On every non-first launch the venv already exists, so `creating_venv`/`installing_deps` are skipped and boot goes `checking → starting_backend → ready`.

### Setup-gate store (Svelte, `frontend/src/lib/stores/setup.ts`)

```
checking          → poll GET /setup/status
  models_ready=true                       → ready          (gate clears, app usable)
  models_ready=false                      → needs_download

needs_download    → user clicks Download → downloading      (POST /setup/download + open SSE)
downloading
  SSE phase=progress/resolving            → downloading      (update %)
  SSE phase=install_done                  → verifying        (re-poll /setup/status)
  SSE phase=install_error                 → download_failed  (show redacted reason)
  401/403 / gated signal                  → needs_token

verifying
  models_ready=true                       → ready
  models_ready=false (corrupt/partial)    → download_failed

needs_token       → user saves token, retries → downloading
download_failed   → user clicks Retry (after cooldown) → downloading
ready             → terminal for this launch; offline from here
```
- Transitions are **driven by `/setup/status`, not by the SSE `install_done` event alone.** `install_done` only moves the store to `verifying`; `ready` requires a fresh status snapshot confirming the cache (defends against a "done" event for a snapshot that didn't actually land — Rule 2/4).
- Cross-tab/state refresh, where it matters, is a plain re-GET of `/setup/status` after a mutation — there is no event bus or push channel for setup state.

## 6 — Edge Cases

- **No network on first run.** `setup/status` returns `models_ready=false`; `POST /setup/download` retries with backoff then emits `install_error`. The gate shows an offline message pointing at connectivity/VPN/firewall (`huggingface.co:443`). The app does **not** crash — it stays on the gate. Once back online, Retry resumes from the cache.
- **Download interrupted / resumed.** A kill, crash, or dropped connection mid-download leaves a partial snapshot. Because readiness requires `size_on_disk > 0` for the *complete* model and the HF download is resumable, re-running `POST /setup/download` continues from cached bytes rather than restarting. A zero-byte/torn snapshot keeps the user gated (Rule 2).
- **Disk full.** `enough_disk=false` (`< 10 GB`) is surfaced before download; the UI blocks/warns. A mid-download `ENOSPC` surfaces as `install_error` and the partial snapshot is treated as not-ready, re-gating cleanly after the user frees space and retries.
- **Restricted-network mirror fallback.** When the user's region is `china`, the supervisor points the venv bootstrap at a PyPI mirror (`UV_DEFAULT_INDEX`) and sets `HF_ENDPOINT=https://hf-mirror.com` for the sidecar so model downloads route through the mirror. `HF_ENDPOINT` set in the environment always wins. This is region implementation, not a default-behavior change (Rule 7).
- **Gated model needs a token.** A download against a gated repo returns 401/403/"gated"; the store moves to `needs_token`. The user supplies a token (in-app encrypted field via `POST /settings/hf-token`, **or** the `HF_TOKEN` env var), the resolver validates it via `whoami`, and the download retries. All error text is token-redacted (§2). The default engine being ungated means most users never see this path.
- **Sidecar fails to start.** If the spawned process exits early or `/healthz` never answers within the supervisor's boot timeout, the supervisor moves to `failed { message }`, attaching the tail of the sidecar's stderr log so the user (or a bug report) has a real cause. Retry / Clean & Retry re-attempt; a stale process holding the port is killed before retrying so the supervisor doesn't "attach" to a zombie.
- **Port already owned.** If `127.0.0.1:3900` already answers as Parrot's engine (e.g. a dev sidecar), the supervisor attaches instead of spawning a duplicate. If the port is held by a non-Parrot process, it takes ownership (kills the orphan) before spawning.
- **Token copied across machines.** An encrypted `hf_token` from another install fails to decrypt (per-install key); the resolver warns once and falls through to the `HF_TOKEN` env var. Setup is unaffected for the ungated default model.
- **Cooldown stampede.** Rapidly retrying a just-failed repo returns `429` with a remaining-seconds hint rather than launching overlapping downloads.

## 7 — Data

| Touched | What | Notes |
|---|---|---|
| HF cache dir | Downloaded model weights (the engine snapshot) | Resolved as `HF_HUB_CACHE` → `HUGGINGFACE_HUB_CACHE` → `HF_HOME` → `~/.cache/huggingface`. Reported to the UI as `hf_cache_dir`. |
| `parrot_data/` | The bootstrapped venv (`parrot_data/.venv`), user voices, generated audio, the SQLite DB, settings | Created idempotently on startup. Must survive upgrades with no manual migration. Distinct from the HF cache. |
| `settings` table | `key="hf_token"` (Fernet-encrypted `value`), `updated_at` | The in-app token source. Per-install encryption key; secrets never stored in plaintext. Not used for appearance settings. |
| `backend.log` / supervisor stderr log | Boot + download diagnostics | Token-redacted by the logging filter. The tail is what a `failed` boot surfaces to the user. |

### Windows HF-cache path-length workaround
On Windows the default HF layout (`…/models--org--name/snapshots/<hash>/<file>`) routinely blows past the legacy `MAX_PATH` (260-char) limit on NTFS, causing `FileNotFoundError` or truncated/torn downloads on first install. Parrot mitigates this at sidecar startup, before any HF import reads the cache location:

- Redirect the HF cache to a short path: `%LOCALAPPDATA%\Parrot\hf_cache` (~40 chars), keeping even the deepest blob path well under the limit. This sets both `HF_HOME` and `HF_HUB_CACHE`.
- **Respect explicit overrides.** If the user already set `HF_HOME`, `HF_HUB_CACHE`, or Parrot's own cache-dir override, the redirect is a no-op.
- The supervisor additionally sets `HF_HUB_DISABLE_SYMLINKS=1` for the sidecar and the install path forces `local_dir_use_symlinks=False`, so the cache works on filesystems/accounts without symlink privileges.

This is a Windows implementation detail behind the same user-visible default (Rule 7): the user still just clicks Download and gets a working model.
