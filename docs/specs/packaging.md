# Packaging & Distribution

How Parrot is built into per-OS installers, how the Python voice engine is shipped invisibly inside the bundle, what is bundled vs. downloaded on first run, how builds are signed/notarized, and how the auto-updater is wired to Parrot's own GitHub releases. See [../../CLAUDE.md](../../CLAUDE.md) for the project constraints this spec inherits (cross-platform parity, local-first, no telemetry).

Parrot is a focused fork of OmniVoice Studio: it keeps the voice-cloning engine and strips dubbing, ASR/dictation, the gallery, batch, the multi-engine picker, and the C++ GGUF backend. The packaging surface shrinks accordingly — one engine (`omnivoice`, pure-Python via `transformers`), no C++ binary, smaller installers.

---

## Entity Contract

The packaging configuration is data, not code. These are the artifacts and identifiers the build pipeline produces and consumes.

```text
BundleConfig (frontend/src-tauri/tauri.conf.json → "bundle")
  productName       : "Parrot"
  identifier        : "studio.parrot.app"        # reverse-DNS, MUST differ from OmniVoice's
                                                 #   com.debpalash.omnivoice-studio
  version           : SemVer, single source of truth, matches release tag (no leading "v")
  targets           : ["dmg", "app", "msi", "deb", "appimage"]
  createUpdaterArtifacts : true                  # emits *.sig + latest.json for the updater
  externalBin       : ["binaries/uv"]            # ffmpeg/ffprobe DROPPED (no dub/ASR path)
  resources         : ["../../pyproject.toml",
                       "../../uv.lock",
                       "../../README.md",
                       "../../sidecar"]           # the Python engine source tree (was "backend"+"omnivoice")
  icon              : [32, 128, 128@2x, icns, ico]

UpdaterConfig (tauri.conf.json → "plugins.updater")
  active            : true
  endpoints         : ["https://github.com/<parrot-owner>/Parrot/releases/latest/download/latest.json"]
  pubkey            : <Parrot's own minisign public key>   # MUST be regenerated; NOT OmniVoice's
  dialog            : false                       # Svelte UI renders the update prompt, not Tauri

ReleaseArtifact (uploaded to GitHub Releases per tag)
  per-OS installer  : Parrot_<version>_<arch>.{dmg,msi,deb,AppImage}
  updater bundle    : <installer>.{tar.gz|zip} + .sig    # signed with the minisign private key
  latest.json       : { version, notes, pub_date, platforms{ <target-triple>{ signature, url } } }
```

**Invariants**

```text
- identifier and updater pubkey are Parrot's own; reusing OmniVoice's would
  cross-wire updates between two apps and hijack each other's releases.
- bundle.version == git tag == pyproject.toml [project].version. CI fails on mismatch.
- No model weights, no GPU/CUDA libraries, and no C++ GGUF binary appear in any artifact.
- A signed update bundle's .sig MUST verify against the configured pubkey or the
  client rejects it (Tauri updater enforces this; do not disable signature checks).
```

---

## Business Rules

1. **Bundle targets are fixed per OS.** `bundle.targets` is `["dmg", "app", "msi", "deb", "appimage"]`. macOS produces a `.app` and a `.dmg`; Windows produces a `.msi`; Linux produces a `.deb` and an AppImage. CI runs the matching `tauri build` on each host OS — there is no cross-compilation of the native shell.
2. **The Python engine ships as source + a `uv` bootstrap, not as a frozen binary, by default.** The sidecar source tree (`sidecar/`), `pyproject.toml`, and `uv.lock` are declared as Tauri `resources`, and the `uv` binary is the only `externalBin`. On first launch the Rust supervisor materializes a virtualenv from the bundled lockfile (see Rule 4). This is the default packaging mode; the PyInstaller-frozen alternative is documented below as a trade-off, not the default.
3. **Model weights are downloaded on first run, never bundled.** No `.safetensors`/checkpoint files appear in any installer. The OmniVoice model (24 kHz output) is fetched from the OmniVoice model repo on Hugging Face into the HF cache on first synthesize/first-run setup, surfaced through the setup-status + SSE progress stream. This keeps installers small and is the single biggest size lever inherited from the source (excluding model weights + CUDA wheels is what kept OmniVoice's `.deb`/`.msi` under the 2 GB GitHub Releases asset cap).
4. **First-launch venv bootstrap is idempotent and host-resolved.** On first run with no usable venv, the supervisor runs `uv venv` (Python 3.11+) then `uv sync` against the bundled `uv.lock`, into `parrot_data/.venv` (a writable location under `parrot_data/`, never inside the read-only app bundle). A completed venv is detected and reused on subsequent launches; the engine is only started after the venv exists and `GET /healthz` returns healthy. This mirrors the install/run split in `scripts/install.sh` (`uv venv` + `uv sync`) and `scripts/run.sh` (start → poll health) but runs invisibly inside the Tauri process. The bootstrapped venv location is stated identically in [architecture.md](./architecture.md).
5. **`ffmpeg`/`ffprobe` are not shipped.** OmniVoice bundled them as `externalBin` for video dubbing and demucs. Parrot has no dub/ASR/media-extraction path, so those binaries are removed from `externalBin`, shrinking every installer.
6. **CUDA/NVIDIA wheels are excluded; inference defaults to CPU, GPU is opportunistic.** The packaged dependency set excludes `nvidia.*`, `triton`, and `flash_attn` (as the source `backend.spec` does). GPU acceleration (CUDA/MPS/ROCm) is used only when a user-installed driver is detected at runtime; it is never bundled. See [device-detection.md](./device-detection.md).
7. **The auto-updater points at Parrot's own releases.** `plugins.updater.endpoints` and `plugins.updater.pubkey` MUST be repointed to Parrot's GitHub repo and a freshly generated minisign keypair. Shipping with OmniVoice's endpoint or pubkey is a release-blocking bug — it would pull OmniVoice's `latest.json` and could not verify Parrot's signatures.
8. **Updates are client-rendered.** `dialog: false` — the Svelte UI owns the "update available / downloading / restart" prompt via the typed updater client in `frontend/src/lib/api/`; Tauri does not draw its own dialog.
9. **Code-signing is required for distributed builds, optional for local dev builds.** Release artifacts uploaded to GitHub MUST be signed/notarized per OS (see IPC/Signing below). Unsigned dev builds are allowed locally but MUST surface the OS warning honestly (see Edge Cases) rather than instruct users to disable security.
10. **Default first-run behavior is identical across macOS/Windows/Linux.** Per the project's strict default-parity rule, the install-and-first-run experience (download installer → launch → venv bootstrap → model download → working clone/speak) must behave the same on all three platforms. Any platform that cannot do this in default mode is a P0: fix it on that platform or move the divergent piece behind explicit opt-in. No third option.
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

- `GET /engine/status` → `{"active":"omnivoice","device":"<id>"}` where `device` ∈ `{"cuda","mps","rocm","cpu"}` (an optional human label may be added as `device_label`). Read-only. This is the single place device is reported to the UI. Parrot ships exactly one engine; there is no picker and no switch endpoint. The packaged dependency set contains only the `omnivoice` (transformers) backend.

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

- **Unsigned-build warning.** A locally built or community-built unsigned artifact triggers Gatekeeper (macOS: "Parrot can't be opened because Apple cannot check it"), SmartScreen (Windows: "Windows protected your PC"), or an AppImage exec-bit/permissions prompt (Linux). Release builds MUST be signed to avoid this; for unsigned dev builds, docs explain the warning honestly and the legitimate override path, and never tell users to globally disable Gatekeeper/SmartScreen.
- **Venv bootstrap offline.** First launch with no network and no `uv` cache cannot run `uv sync` and stalls in `bootstrapping_venv`. The UI must report "first launch needs internet to set up the engine," not hang. Mitigations available to power users mirror the source's restricted-network guidance in [../../CLAUDE.md](../../CLAUDE.md) (`UV_PYTHON_INSTALL_MIRROR`, `UV_PYTHON_PREFERENCE=only-system`, bumped `UV_HTTP_TIMEOUT`/`UV_HTTP_RETRIES`).
- **Antivirus false-positive on the sidecar.** The bundled `uv` binary and the spawned Python process can trip heuristic AV (unsigned native exe spawning a child, listening on `127.0.0.1:3900`). Signing the installer reduces this; docs list the data dir and the loopback-only port so users can allowlist Parrot. The sidecar binds `127.0.0.1` only — never `0.0.0.0` — so it is not a network-exposed service.
- **First-run model download interrupted.** Partial HF cache from a dropped download must resume or re-verify, not corrupt. `/setup/status` stays `models_ready: false` until the full weight set is present; the SSE stream surfaces failure and the UI offers retry. No partial model is ever marked ready.
- **Updater pubkey/endpoint not repointed.** If a build accidentally ships OmniVoice's `pubkey` or `endpoints`, signature verification of Parrot's own artifacts fails (wrong key) or the wrong `latest.json` is fetched. CI MUST assert the configured identifier, endpoint host, and pubkey are Parrot's before publishing.
- **Disk full / read-only volume during venv bootstrap.** macOS apps launched from a read-only DMG, or low-disk conditions, break `uv sync`. The venv MUST target a writable path under `parrot_data/` (`parrot_data/.venv`), never inside the mounted/read-only bundle; the UI reports the specific failure (out of space / read-only) rather than a generic crash.
- **Stale venv after a dependency bump.** A Parrot update may change `uv.lock`. On launch the supervisor compares the bundled lock against the existing venv's resolved state and re-runs `uv sync` if they diverge, so an upgraded app never runs against a stale environment.
- **GitHub Releases 2 GB asset cap.** Excluding model weights, CUDA/NVIDIA wheels, the C++ GGUF binary, and `ffmpeg`/`ffprobe` keeps each Parrot artifact comfortably under the per-asset limit. A build that exceeds it indicates an excludes regression (e.g., CUDA wheels leaking back in).

---

## Data

| Path / file | Role in packaging |
|---|---|
| `frontend/src-tauri/tauri.conf.json` | Bundle targets, `identifier`, `version`, `externalBin`, `resources`, updater endpoint + pubkey. Primary file to repoint from OmniVoice to Parrot. |
| `frontend/src-tauri/binaries/uv` | The only `externalBin`. Drives first-launch venv bootstrap. (`ffmpeg`/`ffprobe` removed.) |
| `pyproject.toml`, `uv.lock` | Bundled as `resources`; the lockfile is the source of truth for the first-run `uv sync`. Single source of truth for `version`. |
| `sidecar/` | Python FastAPI engine source, bundled as a `resource` and run from the bootstrapped venv. (Replaces OmniVoice's `backend` + `omnivoice` resource entries.) |
| `backend.spec` (PyInstaller) | The *alternative* frozen-bundle path. Trade-off: a self-contained, no-bootstrap binary (works fully offline after install, no first-run `uv sync`) but a much larger installer, heavier signing/notarization surface, and the `collect_all` ML-dependency fragility documented in the spec. The default `uv`-bootstrap path keeps installers small at the cost of a one-time online first run; PyInstaller is the option to reach for only if offline-install becomes a hard requirement. Parrot's frozen spec drops the dub/ASR/`mlx`/`ffmpeg` collection entirely. |
| `latest.json` + `*.sig` (per release) | Updater manifest + minisign signatures, emitted by `createUpdaterArtifacts: true`, uploaded to Parrot's GitHub Releases. |
| `parrot_data/` (user machine) | Voice profiles, generated audio, SQLite DB (WAL, `foreign_keys=ON`), settings, and the bootstrapped venv at `parrot_data/.venv`. Outside the bundle; survives installs/updates with no manual migration; never deleted or relocated by installers. |

### Signing / notarization notes

- **macOS:** Developer ID Application signing of the `.app`, then notarization + stapling of the `.dmg`. The bundled `uv` binary and any native libs inside the venv must be covered by the signed/notarized envelope (or the venv lives outside the bundle and is exempted, since it is generated post-install in `parrot_data/.venv`). `minimumSystemVersion` follows the source baseline (`12.0`). Unnotarized builds hit Gatekeeper (see Edge Cases).
- **Windows:** Authenticode-sign the `.msi` (and ideally the bundled `uv.exe`) with an OV/EV certificate. EV certs avoid SmartScreen reputation warm-up; OV certs accrue reputation over downloads. Unsigned MSIs trigger SmartScreen.
- **Linux:** No OS-level code signing. `.deb` integrity comes from the package/repo; AppImages may be GPG-signed and should ship with their `.sig`. Document the AppImage exec-bit step.
- **Updater signing (all OSes):** independent of OS code signing. Every updater artifact is minisign-signed with Parrot's private key; clients verify against `plugins.updater.pubkey`. Keep the private key out of the repo (CI secret); rotating it requires shipping a build with the new `pubkey` before old clients can verify new releases.
