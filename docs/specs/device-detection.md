# Device Detection

How the Parrot Python sidecar picks the compute device it loads the voice-cloning model onto — **CUDA (NVIDIA)** or **CPU** — and how that choice is sized into a worker pool and surfaced to the Svelte UI. Parrot is Windows-only (see [../../CLAUDE.md](../../CLAUDE.md) §Platform Scope), so the device set is just those two.

Device selection runs entirely inside the **Python FastAPI sidecar** (it is the only process that touches PyTorch). The Tauri shell and the Svelte UI never probe hardware; they only read the result over the IPC contract. See [architecture.md](./architecture.md) for the surrounding sidecar/supervisor architecture.

---

## Entity Contract

Device detection produces three values that the rest of the sidecar and the UI consume. None are persisted to the database — they are derived from the runtime at model-load time and recomputed on demand.

```text
device : str
    The torch device string the model is loaded onto.
    One of: "cuda" | "cpu"  (the only values exposed).

gpu_workers : int   (1..16)
    Size of the GPU ThreadPoolExecutor that runs synthesize jobs.
    Lazily computed once on first model access, then cached for the
    process lifetime.

cpu_workers : int   (1..8 by default; 1..16 when overridden)
    Size of the CPU ThreadPoolExecutor used for non-GPU work.
    = PARROT_CPU_POOL env override (clamped to 1..16),
      else min(8, os.cpu_count() or 4) — so the default heuristic
      never exceeds 8, but an explicit override can raise it to 16.
```

Tuning constants (sidecar-internal, not user-facing):

```text
GPU_VRAM_PER_JOB_GB = 2.5   # budgeted free VRAM per concurrent synthesize job
GPU_WORKER_CAP      = 4     # hard ceiling on GPU workers regardless of VRAM
```

**Invariants**

- `device` is a non-empty string in every code path; detection never raises and never returns `None` (worst case `"cpu"`).
- `device` ∈ {`"cuda"`, `"cpu"`} — there are exactly these two reportable values; no other accelerator string is ever produced.
- `gpu_workers >= 1` always — a failed probe degrades to a single worker, never zero.
- `device == "cpu"` ⇒ `gpu_workers == 1` (the "GPU" pool still exists; it just runs CPU inference single-threaded).
- The detected device is stable for the process lifetime. A driver appearing/disappearing mid-run is not handled; the user restarts the app.

---

## Business Rules

1. **Priority order.** Detection picks the first available device in this order: **CUDA → CPU**. This is the complete supported set on Windows — Parrot supports exactly CUDA and CPU; there is no other accelerator branch.
2. **CUDA detection** = `torch.cuda.is_available()`.
3. **CPU is the universal fallback** and is always reachable — if the CUDA probe fails or throws, detection returns `"cpu"`.
4. **GPU worker sizing.** Worker count is resolved in this order:
   1. `PARROT_GPU_WORKERS` env var, if set and an integer, clamped to `1..16`.
   2. CUDA: `free_workers = floor(free_VRAM_GB / 2.5)`, clamped to `1..4`. Free VRAM comes from `torch.cuda.mem_get_info()`.
   3. CPU / unknown: **1** worker.
5. **CPU pool sizing.** The CPU ThreadPoolExecutor is sized at startup to `PARROT_CPU_POOL` if set — clamped to `1..16` (a non-integer override is ignored with a warning and falls through to the heuristic) — else `min(8, os.cpu_count() or 4)`. So the *default heuristic* caps at 8, while an *explicit* override may raise the pool to 16. This pool is independent of the GPU pool and is not resized when a GPU is present.
6. **Fail-safe probing.** Any exception raised while probing (`mem_get_info` failure, driver crash, missing symbol) is caught and logged; worker sizing falls back to **1** and detection falls back to `"cpu"`. A hardware probe must never propagate an exception into model loading.
7. **Compute-capability check.** On CUDA, the sidecar compares the GPU's `sm_<major><minor>` tag against the PyTorch build's compiled arch list. A mismatch does **not** block loading — it logs a warning and still attempts the device; the failure (if any) surfaces later as a model-load error.
8. **Lazy + cached.** `torch` is imported lazily on first device access (it is a multi-second import). Device detection and GPU-pool sizing are computed on first model access and cached, so `GET /healthz` stays instant during cold start and `GET /engine/status` answers from the cached value once detection has run.
9. **Windows-only default.** Parrot auto-selects CUDA when present and falls back to CPU with no user action, on Windows 10/11 (x64). Power-user overrides (`PARROT_GPU_WORKERS`, `PARROT_CPU_POOL`, `CUDA_VISIBLE_DEVICES`) are explicit env-var opt-ins. There is no macOS/MPS or AMD/ROCm branch (out of scope — see [../../CLAUDE.md](../../CLAUDE.md) §Platform Scope).
10. **Install-time torch wheel selection (CPU vs CUDA).** Rule 2's `torch.cuda.is_available()` can only return `True` if a **CUDA-enabled torch wheel** was installed — PyPI's default Windows wheel is CPU-only and reports no CUDA even on an NVIDIA box. So the wheel variant is chosen once at first-run venv bootstrap by the **Rust supervisor**, not the Python side: it probes for an NVIDIA GPU (`nvidia-smi -L`) and syncs the matching extra — GPU present → `uv sync --no-dev --extra engine --extra cu124` (CUDA wheels from PyTorch's `cu124` index), otherwise → `--extra engine --extra cpu`. The two extras are declared mutually exclusive (`[tool.uv] conflicts` in `sidecar/pyproject.toml`) so a venv never mixes them. This install decision is upstream of and independent from the runtime probe (Rules 1–3), which stays authoritative: a false-positive detection that installs CUDA wheels on a machine whose driver is missing/broken still degrades to `"cpu"` at load time (Rule 3/6). See [packaging.md](./packaging.md) Rules 4 & 6.

---

## IPC Contract

The detected device is read-only from the UI's perspective; there is no "set device" endpoint. Parrot ships a single fixed engine, so device is reported alongside the engine identity on one endpoint.

### `GET /engine/status`

The single place the active device is reported to the UI. Parrot has exactly one TTS engine (`omnivoice`), so `active` is a fixed constant; `device` is the resolved compute device. Must never throw — on any internal error it returns safe defaults with `device: "cpu"`.

```jsonc
// 200 OK
{
  "active": "omnivoice",
  "device": "cuda",            // "cuda" | "cpu"
  "device_label": "GPU (CUDA)" // optional human label
}
```

- **Errors:** never 5xx. On failure returns the same shape with `device: "cpu"`.
- There is no engine picker and no `POST` to select an engine or device — engine identity is fixed and device is auto-detected. This is the **only** engine/device endpoint; there is no `/engine`, `/engines`, `/system/info`, or `/system/notifications`.

### CPU-running hint

When the resolved device is `"cpu"`, the UI shows an advisory hint ("Running on CPU — voice generation will be slower; if you have an NVIDIA GPU, check your CUDA drivers"). This is derived purely from `device == "cpu"` on the `GET /engine/status` response — there is no separate notifications endpoint and nothing is pushed from the sidecar. The hint is advisory only; CPU is a fully supported device and the hint never blocks generation.

### `GET /healthz` (supervisor probe — no device data)

Used by the Tauri supervisor to confirm the sidecar is alive. Returns liveness only:

```jsonc
// 200 OK
{ "status": "ok" }
```

It intentionally does **not** trigger torch import or device detection, carries no `device` (or any other) field, and stays fast during the multi-second cold start while the model loads.

---

## State Machines

The frontend models device awareness as derived state in a Svelte store (e.g. `frontend/src/lib/stores/`), fed by the typed IPC client in `frontend/src/lib/api/`. Device itself has no transitions inside the UI — it's a value read from `/engine/status` — but the *display* state does:

```text
deviceStore states:

  unknown ──(fetch /engine/status)──▶ resolving
  resolving ──(200, device == "cuda")─▶ accelerated   { label: "GPU (CUDA)" }
  resolving ──(200, device == "cpu")──▶ cpu_only       { hint: "Running on CPU" }
  resolving ──(network error / 5xx)───▶ resolving      (retry with backoff; treat as unknown until first success)

  accelerated ─┐
  cpu_only ────┴─(sidecar restart / app relaunch)──▶ unknown
```

- The store is populated once per sidecar lifetime; a device label change requires a sidecar restart (rule 8 — detection is computed once and cached). The UI does not poll for device changes; it re-fetches `/engine/status` on relaunch.
- `cpu_only` is a terminal-but-valid state, visually distinct from an error: generation is enabled, only a "slower on CPU" hint is shown.

---

## Edge Cases

- **No GPU at all.** The CUDA probe fails → `device = "cpu"`, `gpu_workers = 1`. Synthesis works, slower. The UI shows the "Running on CPU" hint (derived from `device == "cpu"`). (Default path on a generic Windows desktop/laptop with no discrete NVIDIA GPU.)
- **GPU present but CPU wheel installed.** If first-run NVIDIA detection (Rule 10) missed the GPU — `nvidia-smi` absent at bootstrap, driver installed afterward, etc. — the venv carries the CPU torch wheel, so `torch.cuda.is_available()` is `False` and `device = "cpu"` despite the hardware. The UI's "check your CUDA drivers" hint applies. Recovery is a venv rebuild against the `cu124` extra (delete `parrot_data/.venv` and relaunch, or a future "re-enable GPU" action) — not a runtime toggle, since the wheel set is fixed once installed.
- **GPU present but VRAM too small.** `floor(free_VRAM / 2.5)` can be `0`; the `1..4` clamp (Rule 4.2) forces `gpu_workers = 1` rather than zero. If the model itself can't fit, that surfaces later as a model-load error, not a detection error — detection still reports `"cuda"`.
- **Driver / arch mismatch.** GPU detected but the PyTorch build wasn't compiled for its compute capability (`sm_120` on an old wheel, etc.). Detection still returns `"cuda"` and logs a warning naming the device, its `sm_` tag, and the supported arch list. It does not silently fall back to CPU — the user gets an actionable message instead of a mystery slowdown.
- **Multiple GPUs.** Detection selects device index `0` only; there is no multi-GPU spread and no device picker. Free-VRAM sizing reads GPU 0's `mem_get_info()`. Users with a specific GPU preference set `CUDA_VISIBLE_DEVICES` before launch (env opt-in).
- **`mem_get_info()` raises.** Caught; `gpu_workers` falls back to `1`, device stays whatever the `is_available()` check returned (still `"cuda"`).
- **Bad worker override.** `PARROT_GPU_WORKERS=foo` (non-integer) is ignored with a warning and sizing proceeds to the VRAM heuristic. Integer values outside `1..16` are clamped, not rejected.
- **Cold-start race.** A device/engine-status read that lands before torch finishes importing must not block: `/healthz` answers liveness without forcing detection; `/engine/status` triggers the lazy import on the very first call and may take longer then, but still returns rather than 5xx (falling back to `device: "cpu"` on any internal error).

---

## Data

Device detection touches **no database tables and no files** — it is pure runtime introspection. For reference, the persistent user data it sits alongside lives under `parrot_data/` (voices, generated audio, the SQLite DB, settings); device choice is recomputed each process start and never written there.

| Surface | Read / Write | Notes |
|---|---|---|
| `torch.cuda` | read | Hardware probe (`is_available()`, `mem_get_info()`); lazily imported. |
| `os.environ` | read | `PARROT_GPU_WORKERS`, `PARROT_CPU_POOL`, `CUDA_VISIBLE_DEVICES`. |
| Sidecar log (`parrot_data/`) | write | Worker-count, device, and arch-mismatch warnings are written to the backend log (`backend.log`), surfaced via "View backend log" in Settings → Engine. |
| GPU `ThreadPoolExecutor` | derived | Sized once from `gpu_workers`; not persisted. |
| CPU `ThreadPoolExecutor` | derived | Sized once at startup from `cpu_workers`; not persisted. |
