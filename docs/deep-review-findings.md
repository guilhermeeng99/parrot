# Deep Review — Findings & Fixes (2026-05-29)

Second-pass whole-project audit of the `deep-review-fixes` branch, focused on the
uncommitted **synthesis-progress** work-in-progress (real per-step SSE progress
bar) plus a sweep of docs / tests / cleanliness / dependencies across all three
stacks (Python sidecar, Rust shell, Svelte/TS frontend).

**Outcome:** 27 findings → 3 confirmed high/medium + 22 medium/low actioned; all
applied and verified. Two breaking Rust dependency bumps deliberately deferred.

## Verification (post-fix, run together)

| Suite | Result |
| --- | --- |
| `uv run pytest -q` (sidecar) | ✅ 96 passed (was 89 + 7 new) |
| `bun run check` (svelte-check) | ✅ 0 errors / 0 warnings (394 files) |
| `bun run test` (vitest) | ✅ 42/42 (was 1 red) |
| `cargo clippy --all-targets` | ✅ 0 warnings |
| `cargo test` | ✅ 19 passed (was 16 + 3 new) |

## Two adversarial-verification notes (so the record is honest)

- ✅ **The step bar tracks diffusion steps correctly.** A reviewer claimed the
  top-level forward-hook can't fire per step; a verifier read the pinned
  `omnivoice 0.1.5` source (`_generate_iterative` calls `self(...)` once per
  `num_step`) and confirmed the hook is attached at the right level. No change
  needed — design is sound.
- ⚠️ **The concurrency hazard was real** (a verifier had refuted it on a false
  premise — it claimed `tts_backend` hard-codes `progress_cb=None`; the code
  actually passes `_on_step`, so the hook *is* live). Fixed via A1 below.

---

## Fixed — Python sidecar

- 🟡 **A1 · Progress-bus concurrency** (`tts_backend.py`): the single global
  progress bus + the shared singleton model's forward-hook assumed one generation
  at a time, but the WS conversational path and a multi-worker GPU pool could
  overlap and scramble the bar. **Fix:** a module-level `asyncio.Lock` serializes
  `begin → infer → finish` across both POST and WS. OOM→flush→`ServiceError`
  preserved. Regression test proven to fail without the lock.
- 🟡 **A2/A3 · Duplication + thread-safety** (`core/sse_broadcast.py` NEW):
  extracted the SSE broadcaster that `generation_progress` and `setup_manager`
  each had a near-verbatim copy of. Subscriber set guarded by a `threading.Lock`;
  per-subscriber queues **bounded** (maxsize 256, drop-oldest) so a stalled
  consumer can't grow memory per-step.
- 🟡 **A4 · Loopback gate** (`routers/generate.py`, `routers/setup.py`): added
  `Depends(require_loopback)` to `/generate/progress-stream` and
  `/setup/download-stream`, matching the sibling engine endpoint.
- 🔵 **A5 · Terminal close**: `progress_stream()` now returns after a `done`/`error`
  event instead of looping forever (no leaked generator on a stale client).
- 🔵 **A6 · Carry total**: `finish()`/`fail()` now publish the last-known
  `total`/`step` instead of `0,0`.
- 🟡 **A7/A8/A9 · Test coverage**: `FakeBackend.synthesize` now drives
  `progress_cb`; added async tests (threaded publish, SSE framing + terminal
  close + unsubscribe, multi/zero subscriber, bounded-queue overflow), a
  start→step→done POST test, and an overlap regression test.

## Fixed — Rust shell

- 🟡 **B1 · `has_nvidia_gpu` no longer blocks** (`supervisor.rs`): `nvidia-smi` is
  now spawned + bounded-polled (5 s) against `shutting_down` instead of a blocking
  `.status()`, restoring `ensure_venv`'s "never blocks app-quit" invariant.
- 🟡 **B2 · `uv` output captured**: `uv venv`/`uv sync` stdout+stderr now go to
  `<log_dir>/uv_bootstrap.log`, so a failed `--extra cu124` resolve is diagnosable
  in a windowless release build (Settings → Logs).
- 🔵 **B3 · Sync-completion sentinel**: a `.parrot-sync-complete` marker is written
  only after a clean `uv sync`; the fast-path requires both `python.exe` **and**
  the sentinel, so a partial/interrupted CUDA install re-syncs instead of booting
  a torch-less venv.
- 🔵 **B4 · No silent-audio** (`native.rs`): the audio device is probed in
  `get_or_init`; on failure it stores `None` so `play_audio` surfaces the typed
  "No audio output device" error instead of returning `Ok` with no sound.
- 🔵 **B5 · Robust health check**: `is_ok_health_body` parses JSON and asserts the
  top-level `status == "ok"` instead of a substring match (squatter guard).
- 🔵 **B6 · Monitor closure split**: the ~160-line `start()` monitor closure was
  mechanically extracted into single-responsibility helpers (behavior-identical;
  all tests green).

## Fixed — Svelte/TS frontend

- 🟠 **C1 · The red test** (`stores/synthesis.ts`): the progress subscription is
  now fire-and-forget (not awaited), so `generateSpeech` is reached with the
  original microtask timing — vitest back to 42/42. A `settled` guard closes the
  EventSource even if the subscribe resolves after the request settled (no leak).
- 🔵 **C2 · Monotonic bar**: non-`done` events use `Math.max`; `error` defers to
  the catch handler (no snap-back to 0).
- 🔵 **C3 · Dead `onError`** (`api/generate.ts`): removed the unused param;
  corrected the doc comment (connect failure is non-observable; surfaces via
  `es.onerror`).
- 🟡 **C4 · Typed-client rule** (`api/history.ts`, `SpeakScreen.svelte`): the raw
  `fetch()` in the component moved behind `historyAudioBytes(id)`; removed the
  dead empty `Uint8Array`.
- 🔵 **C5 · a11y**: progress bar uses `role="progressbar"` + `aria-valuenow`;
  per-step text is no longer re-announced by screen readers.
- 🔵 **C6 · Render churn**: history rows memoize their audio URL instead of
  re-resolving per render.

## Fixed — Docs

- 🔵 **D1**: removed the "focused fork" framing from `packaging.md` +
  `architecture.md` (Path B: independent app, not a code fork).
- 🔵 **D2**: `synthesis.md` state machine reconciled to the real store
  (`idle/submitting/done/error` + `progress`).
- 🔵 **D3**: `architecture.md` §3.4 softened (supervisor emits a failure count,
  not a stderr tail).
- 🔵 **D4**: `ipc-contract.md` documents `GET /generate/progress-stream` and its
  `{phase,step,total,pct}` event; `ui-ux.md` reconciled to the four-state store.

## Dependencies

- **JS:** `bun outdated` empty, `bun audit` clean — nothing to do.
- **Python:** added `pip-audit` to the dev group (a CI Python security audit was
  missing); transitive `pydantic-core`/`typer` minor bumps move with their parents.
- **Rust audit:** 17 `cargo audit` warnings, all unmaintained/unsound with **no
  fixable vuln** — the GTK chain is Linux-only (not in the Windows MSI), the rest
  are build-time, pinned by the Tauri 2.x tree. No action.

## Deferred (deliberate)

- **`rodio` 0.20 → 0.22** (direct, minor but 0.x semver-breaking Source/OutputStream
  rework) and **`ureq` 2 → 3** (direct, full API rewrite). Both touch native audio
  playback / the loopback HTTP probe and **can't be validated headlessly** on
  Windows. Bump them in a dedicated change where playback + the health probe can
  be exercised on a real machine.
- **`supervisor.rs` file size** (~917 production LOC > the ~600 guideline). The B6
  refactor improved per-function discipline but the file still drifts. A future
  split of the first-run bootstrap into `bootstrap.rs` would resolve it; not done
  now to avoid churn.
- **`pip-audit` run**: wired into the dev group but not executed here (needs
  network) — to run in CI.
