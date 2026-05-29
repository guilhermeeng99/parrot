# Packaging & Distribution

How Parrot is built into a **Windows MSI installer**, how the Python voice engine is shipped invisibly inside the bundle, what is bundled vs. downloaded on first run, how builds are code-signed, and how the auto-updater is wired to Parrot's own GitHub releases. Parrot is Windows-only (Windows 10/11, x64 — see [../../CLAUDE.md](../../CLAUDE.md) §Platform Scope); there are no macOS or Linux artifacts. See [../../CLAUDE.md](../../CLAUDE.md) for the project constraints this spec inherits (local-first, no telemetry).

Parrot is a focused fork of OmniVoice Studio: it keeps the voice-cloning engine and strips dubbing, ASR/dictation, the gallery, batch, the multi-engine picker, and the C++ GGUF backend. The packaging surface shrinks accordingly — one engine (`omnivoice`, pure-Python via `transformers`), no C++ binary, a smaller installer.

---

## Entity Contract

The packaging configuration is data, not code. These are the artifacts and identifiers the build pipeline produces and consumes.

```text
BundleConfig (frontend/src-tauri/tauri.conf.json → "bundle")
  productName       : "Parrot"
  identifier        : "com.guilhermeeng99.parrot"  # reverse-DNS, MUST differ from OmniVoice's
                                                 #   com.debpalash.omnivoice-studio
  version           : SemVer, matches release tag (no leading "v"); currently 0.0.1, declared
                      in 4 manifests (see Invariants — consolidation is a TODO)
  targets           : ["msi"]                    # Windows MSI only (no dmg/app/deb/appimage)
  createUpdaterArtifacts : true                  # Phase-3; emits *.sig + latest.json for the updater
  externalBin       : ["binaries/uv.exe"]        # Phase-3; ffmpeg/ffprobe DROPPED (no dub/ASR path)
  resources         : ["../../sidecar/pyproject.toml",
                       "../../sidecar/uv.lock",
                       "../../README.md",
                       "../../sidecar"]           # the Python engine source tree (was "backend"+"omnivoice")
  icon              : [32, 128, 128@2x, icns, ico]

UpdaterConfig (tauri.conf.json → "plugins.updater")
  active            : true
  endpoints         : ["https://github.com/<parrot-owner>/Parrot/releases/latest/download/latest.json"]
  pubkey            : <Parrot's own minisign public key>   # MUST be regenerated; NOT OmniVoice's
  dialog            : false                       # Svelte UI renders the update prompt, not Tauri

ReleaseArtifact (uploaded to GitHub Releases per tag)
  installer         : Parrot_<version>_x64_en-US.msi
  updater bundle    : <installer>.zip + .sig             # signed with the minisign private key
  latest.json       : { version, notes, pub_date, platforms{ <target-triple>{ signature, url } } }
```

**Invariants**

```text
- identifier and updater pubkey are Parrot's own; reusing OmniVoice's would
  cross-wire updates between two apps and hijack each other's releases. The chosen
  identifier is "com.guilhermeeng99.parrot" (committed in tauri.conf.json).
- version is currently 0.0.1, declared in 4 manifests that must stay in lockstep:
  sidecar/pyproject.toml, frontend/package.json, frontend/src-tauri/Cargo.toml, and
  frontend/src-tauri/tauri.conf.json. There is no single source of truth yet;
  consolidating these (so bundle.version == git tag is CI-enforced) is a TODO.
- No model weights, no GPU/CUDA libraries, and no C++ GGUF binary appear in any artifact.
- A signed update bundle's .sig MUST verify against the configured pubkey or the
  client rejects it (Tauri updater enforces this; do not disable signature checks).
```

> **Status.** `tauri.conf.json` now declares `targets: ["msi"]`, `externalBin: ["binaries/uv"]`,
> the sidecar source `resources` map, `createUpdaterArtifacts: true`, and the `plugins.updater`
> block. Two release-gating placeholders remain (cannot be done headlessly):
> the `uv` externalBin (`frontend/src-tauri/binaries/uv-<triple>.exe`) is the dev machine's `uv`
> copy — re-pin a known version for release builds — and `plugins.updater.pubkey` is the literal
> `"PLACEHOLDER_REGENERATE_BEFORE_RELEASE"`, which **must** be replaced with a freshly generated
> minisign public key (with the private key kept as a CI secret) before any signed release.

---

## Business Rules

1. **The bundle target is the Windows MSI.** `bundle.targets` is `["msi"]` — `tauri build` on a Windows host produces a single `.msi`. There are no macOS/Linux artifacts (out of scope). The committed `tauri.conf.json` already sets `["msi"]`.
2. **Phase-3 target — the Python engine ships as source + a `uv` bootstrap, not as a frozen binary, by default.** The sidecar source tree (`sidecar/`), `sidecar/pyproject.toml`, and `sidecar/uv.lock` will be declared as Tauri `resources`, and the `uv.exe` binary will be the only `externalBin`. On first launch the Rust supervisor materializes a virtualenv from the bundled lockfile (see Rule 4). This is the planned default packaging mode; the PyInstaller-frozen alternative is documented below as a trade-off, not the default. *(`tauri.conf.json` now declares the `uv` `externalBin` and the sidecar-source `resources` map.)*
3. **Model weights are downloaded on first run, never bundled.** No `.safetensors`/checkpoint files appear in any installer. The OmniVoice model (24 kHz output) is fetched from the OmniVoice model repo on Hugging Face into the HF cache on first synthesize/first-run setup, surfaced through the setup-status + SSE progress stream. This keeps the installer small and is the single biggest size lever inherited from the source (excluding model weights + CUDA wheels is what kept the installer under the 2 GB GitHub Releases asset cap).
4. **First-launch venv bootstrap is idempotent and host-resolved.** On first run with no usable venv, the supervisor runs `uv venv` (Python 3.11+) then `uv sync --no-dev --extra engine` against the bundled `uv.lock`. `--no-dev` keeps the test-only `dev` group (pytest/httpx) out of the shipped runtime venv; `--extra engine` pulls the PyTorch ML stack (torch/torchaudio/transformers/pedalboard) that lives in the `engine` optional-dependency group — kept out of the default/test sync (and this repo's CI) because torch is multi-GB and the model boundary is mocked in tests. The vendored Apache-2.0 `omnivoice` model lib is resolved at runtime by `model_manager` (import path unchanged; see [../LICENSING.md](../LICENSING.md)) and is not pinned in `uv.lock`. A completed venv is detected and reused on subsequent launches; the engine is only started after the venv exists and `GET /healthz` returns healthy. The install (`uv venv` + `uv sync`) and run (start → poll health) logic lives in the Rust sidecar supervisor and runs invisibly inside the Tauri process — there are no standalone `scripts/install.sh`/`scripts/run.sh` helpers. The bootstrapped venv location is stated identically in [architecture.md](./architecture.md).
5. **`ffmpeg`/`ffprobe` are not shipped.** OmniVoice bundled them as `externalBin` for video dubbing and demucs. Parrot has no dub/ASR/media-extraction path, so those binaries are removed from `externalBin`, shrinking every installer.
6. **CUDA/NVIDIA wheels are excluded; inference defaults to CPU, GPU is opportunistic.** The packaged dependency set excludes `nvidia.*`, `triton`, and `flash_attn` (as the source `backend.spec` does). GPU acceleration (CUDA) is used only when a user-installed NVIDIA driver is detected at runtime; it is never bundled. See [device-detection.md](./device-detection.md).
7. **The auto-updater points at Parrot's own releases.** `plugins.updater.endpoints` and `plugins.updater.pubkey` MUST be repointed to Parrot's GitHub repo and a freshly generated minisign keypair. Shipping with OmniVoice's endpoint or pubkey is a release-blocking bug — it would pull OmniVoice's `latest.json` and could not verify Parrot's signatures.
8. **Updates are client-rendered.** `dialog: false` — the Svelte UI owns the "update available / downloading / restart" prompt via the typed updater client in `frontend/src/lib/api/`; Tauri does not draw its own dialog.
9. **Code-signing is required for distributed builds, optional for local dev builds.** The release `.msi` uploaded to GitHub MUST be Authenticode-signed (see Signing notes below). Unsigned dev builds are allowed locally but MUST surface the SmartScreen warning honestly (see Edge Cases) rather than instruct users to disable security.
10. **First-run behavior must be solid on Windows 10 and 11 (x64).** The install-and-first-run experience (download MSI → launch → venv bootstrap → model download → working clone/speak) must work on both Windows 10 and Windows 11. A first-run/default feature that fails on either is a P0.
11. **App data survives upgrades with no manual migration.** `parrot_data/` (voice profiles, generated audio, SQLite DB, settings) lives outside the bundle and is untouched by installers/updates. Schema changes ship as alembic migrations applied by the engine on startup; the DB is created idempotently with WAL and `foreign_keys=ON`. An installer or updater MUST NOT delete or relocate `parrot_data/`.

---

## IPC Contract

Packaging touches three runtime contracts: the supervisor health gate, the first-run setup surface, and the engine-status stub. These are the only endpoints the packaging/bootstrap path depends on; full route shapes live in the engine specs.

**Supervisor → sidecar (lifecycle gate)**

- `GET /healthz` — Rust supervisor readiness probe. The supervisor spawns the venv'd sidecar (`uv run` the FastAPI app on `127.0.0.1:3900`), then polls `/healthz` until healthy before the UI is allowed to call the engine. On repeated failure the supervisor tears the process down and reports a bootstrap error to the UI.
  - Returns: `200 {"status":"ok"}` — liveness only, fast, no device field.
  - Error cases: connection refused (process not up yet) → keep polling within deadline; non-200 past deadline → supervisor kills + restarts once, then surfaces failure.

**First-run setup (packaging-visible)**

- `GET /setup/status` → `{"models_ready": bool, ...}` — whether the on-disk model weights are present. `false` immediately after install (nothing bundled); flips `true` once the first-run download completes.
- `POST /setup/download` — starts the first-run model download.
- `GET /setup/download-stream` — Server-Sent Events progress stream while model weights download on first run. The Svelte setup view subscribes and renders a progress bar; on completion `/setup/status` returns `models_ready: true`.
  - Error cases: offline / HF unreachable → stream surfaces an error; UI shows a retry affordance and the actionable "download requires internet on first run" message (see Edge Cases).

**Engine status (single-engine stub)**

- `GET /engine/status` → `{"active":"omnivoice","device":"<id>"}` where `device` ∈ `{"cuda","cpu"}` (an optional human label may be added as `device_label`). Read-only. This is the single place device is reported to the UI. Parrot ships exactly one engine; there is no picker and no switch endpoint. The packaged dependency set contains only the `omnivoice` (transformers) backend.

**Updater (Tauri plugin, not an HTTP route)**

- The updater fetches `endpoints[0]` (`.../releases/latest/download/latest.json`), compares `version` to the running `bundle.version`, verifies the artifact `.sig` against `pubkey`, downloads, and stages the update. The Svelte UI drives check/download/install via the updater client (`dialog: false`).
  - Error cases: signature verification fail → update rejected, no install, error surfaced to UI; endpoint 404/unreachable → "couldn't check for updates" (non-fatal, app continues running); version not newer → no-op.

---

## State Machines

### Frontend: first-launch / bootstrap store (`frontend/src/lib/stores/bootstrap.ts`)

Drives the splash/setup screen the user sees before the clone/speak UI is usable.

```text
states: spawning → bootstrapping_venv → starting_engine → downloading_models → ready
        (any) → error

spawning            : Tauri supervisor launching uv-backed sidecar
  → bootstrapping_venv  when first run detects no usable venv (uv venv + uv sync)
  → starting_engine     when a completed venv already exists (reuse)
  → error               supervisor failed to spawn

bootstrapping_venv  : uv materializing parrot_data/.venv from bundled uv.lock
  → starting_engine     venv sync succeeded
  → error               uv sync failed (offline w/o cache, disk full, locked file)

starting_engine     : polling GET /healthz
  → downloading_models  healthy AND /setup/status models_ready == false
  → ready               healthy AND models_ready == true
  → error               health deadline exceeded

downloading_models  : subscribed to GET /setup/download-stream SSE progress stream
  → ready               download complete, models_ready == true
  → error               download failed / offline

ready               : clone + speak UI enabled
error               : actionable message + retry; never a dead end
```

### Frontend: updater store (`frontend/src/lib/stores/updater.ts`)

```text
states: idle → checking → up_to_date
                       → available → downloading → ready_to_restart → (relaunch)
        (checking|downloading) → error

checking          → up_to_date          remote version not newer
                  → available           remote version newer, sig pubkey configured
                  → error               endpoint unreachable (non-fatal)
available         → downloading         user accepts (UI prompt; dialog:false)
downloading       → ready_to_restart    artifact downloaded + signature verified
                  → error               signature verification failed → discard
ready_to_restart  → relaunch            user confirms; app restarts onto new version
```

---

## Edge Cases

- **Unsigned-build warning.** A locally built or community-built unsigned `.msi` triggers Windows SmartScreen ("Windows protected your PC"). Release builds MUST be Authenticode-signed to avoid this; for unsigned dev builds, docs explain the warning honestly and the legitimate "More info → Run anyway" path, and never tell users to globally disable SmartScreen.
- **Venv bootstrap offline.** First launch with no network and no `uv` cache cannot run `uv sync` and stalls in `bootstrapping_venv`. The UI must report "first launch needs internet to set up the engine," not hang. Mitigations available to power users mirror the source's restricted-network guidance in [../../CLAUDE.md](../../CLAUDE.md) (`UV_PYTHON_INSTALL_MIRROR`, `UV_PYTHON_PREFERENCE=only-system`, bumped `UV_HTTP_TIMEOUT`/`UV_HTTP_RETRIES`).
- **Antivirus false-positive on the sidecar.** The bundled `uv` binary and the spawned Python process can trip heuristic AV (unsigned native exe spawning a child, listening on `127.0.0.1:3900`). Signing the installer reduces this; docs list the data dir and the loopback-only port so users can allowlist Parrot. The sidecar binds `127.0.0.1` only — never `0.0.0.0` — so it is not a network-exposed service.
- **First-run model download interrupted.** Partial HF cache from a dropped download must resume or re-verify, not corrupt. `/setup/status` stays `models_ready: false` until the full weight set is present; the SSE stream surfaces failure and the UI offers retry. No partial model is ever marked ready.
- **Updater pubkey/endpoint not repointed.** If a build accidentally ships OmniVoice's `pubkey` or `endpoints`, signature verification of Parrot's own artifacts fails (wrong key) or the wrong `latest.json` is fetched. CI MUST assert the configured identifier, endpoint host, and pubkey are Parrot's before publishing.
- **Disk full / read-only location during venv bootstrap.** Low-disk conditions, or installing into a read-only/locked location, break `uv sync`. The venv MUST target a writable path under `parrot_data/` (`parrot_data/.venv`), never inside the installed program-files bundle; the UI reports the specific failure (out of space / read-only) rather than a generic crash.
- **Stale venv after a dependency bump.** A Parrot update may change `uv.lock`. On launch the supervisor compares the bundled lock against the existing venv's resolved state and re-runs `uv sync` if they diverge, so an upgraded app never runs against a stale environment.
- **GitHub Releases 2 GB asset cap.** Excluding model weights, CUDA/NVIDIA wheels, the C++ GGUF binary, and `ffmpeg`/`ffprobe` keeps each Parrot artifact comfortably under the per-asset limit. A build that exceeds it indicates an excludes regression (e.g., CUDA wheels leaking back in).

---

## Data

| Path / file | Role in packaging |
|---|---|
| `frontend/src-tauri/tauri.conf.json` | Bundle targets, `identifier`, `version`, `externalBin`, `resources`, updater endpoint + pubkey. Primary file to repoint from OmniVoice to Parrot. |
| `frontend/src-tauri/binaries/uv` | The only `externalBin`. Drives first-launch venv bootstrap. (`ffmpeg`/`ffprobe` removed.) |
| `sidecar/pyproject.toml`, `sidecar/uv.lock` | Bundled as `resources`; the lockfile is the source of truth for the first-run `uv sync`. The `version` (currently 0.0.1) is duplicated across `sidecar/pyproject.toml`, `frontend/package.json`, `frontend/src-tauri/Cargo.toml`, and `frontend/src-tauri/tauri.conf.json` — consolidating to a single source is a TODO. |
| `sidecar/` | Python FastAPI engine source, bundled as a `resource` and run from the bootstrapped venv. (Replaces OmniVoice's `backend` + `omnivoice` resource entries.) |
| `backend.spec` (PyInstaller) | The *alternative* frozen-bundle path. Trade-off: a self-contained, no-bootstrap binary (works fully offline after install, no first-run `uv sync`) but a much larger installer, heavier code-signing surface, and the `collect_all` ML-dependency fragility documented in the spec. The default `uv`-bootstrap path keeps the installer small at the cost of a one-time online first run; PyInstaller is the option to reach for only if offline-install becomes a hard requirement. Parrot's frozen spec drops the dub/ASR/`ffmpeg` collection entirely. |
| `latest.json` + `*.sig` (per release) | Updater manifest + minisign signatures, emitted by `createUpdaterArtifacts: true`, uploaded to Parrot's GitHub Releases. |
| `parrot_data/` (user machine) | Voice profiles, generated audio, SQLite DB (WAL, `foreign_keys=ON`), settings, and the bootstrapped venv at `parrot_data/.venv`. Outside the bundle; survives installs/updates with no manual migration; never deleted or relocated by installers. |

### Signing notes

- **Windows (Authenticode):** Sign the `.msi` (and ideally the bundled `uv.exe`) with an OV/EV certificate. EV certs avoid SmartScreen reputation warm-up; OV certs accrue reputation over downloads. Unsigned MSIs trigger SmartScreen (see Edge Cases). This is the only OS-level signing Parrot does (Windows-only).
- **Updater signing:** independent of Authenticode. Every updater artifact is minisign-signed with Parrot's private key; clients verify against `plugins.updater.pubkey`. Keep the private key out of the repo (CI secret); rotating it requires shipping a build with the new `pubkey` before old clients can verify new releases.
