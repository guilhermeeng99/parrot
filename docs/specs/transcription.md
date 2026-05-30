# Reference Transcription (ASR)

Auto-transcribing a voice-clone **reference sample** into its `ref_text` so the user doesn't have to type it. This is the *only* speech-to-text in Parrot, and it exists for exactly one reason: an accurate `ref_text` measurably sharpens a clone ([voice-cloning.md](./voice-cloning.md) BR-4). The user attaches a clip, Parrot transcribes it with a high-fidelity Whisper model, and drops the result into the editable transcript field — "better empty than wrong" becomes "filled and right" without manual typing.

> **Scope carve-out (read this first).** General dictation / ASR is a documented **non-goal** ([CLAUDE.md §Scope](../../CLAUDE.md)). This feature is a deliberately narrow exception: transcription is wired **only** into the clone capture flow to fill `ref_text`. There is **no** standalone transcribe screen, no file→subtitle tool, no live dictation, no SRT/VTT export. If a request would grow this past "fill the reference transcript," it is declined or it changes [CLAUDE.md](../../CLAUDE.md) first. The carve-out is recorded in CLAUDE.md §Scope and [../ROADMAP.md](../ROADMAP.md).

The engine rationale and the alternatives weighed (faster-whisper, Rust+whisper.cpp) are in §3 below.

Related specs: [voice-cloning.md](./voice-cloning.md) · [ipc-contract.md](./ipc-contract.md) · [first-run-setup.md](./first-run-setup.md) · [device-detection.md](./device-detection.md) · [packaging.md](./packaging.md).

---

## 1 — What this is (and is not)

```text
IS    a clone-time helper: decode the reference clip → Whisper → put the transcript
      in the (still editable) ref_text field, before the profile is saved.
IS    fully local: the model downloads once from the public Whisper CDN at the
      user's request; after that, transcription is offline. The audio never leaves
      the machine — it is decoded in-process and handed to Whisper as samples.
IS    opt-in by model: nothing downloads until the user picks a model and clicks
      download. The clone flow works with transcription absent (manual ref_text).

NOT   a dictation feature, a subtitle/caption tool, or a general audio→text utility.
NOT   wired into Speak, History, or anywhere outside the Clone capture panel.
NOT   stored as its own entity: the output is just a string that lands in ref_text
      (voice_profiles.ref_text). No new table, no new column, no migration.
```

---

## 2 — Model catalog

Parrot exposes a curated subset of openai-whisper models, fidelity-first (tiny/base are omitted as too low-fidelity for a clone reference). Sizes are the on-disk `.pt` weights.

```text
id                label                          size_mb   default
small             Small                          ~470
medium            Medium                         ~1500
large-v3-turbo    Large v3 Turbo                 ~1600
large-v3          Large v3 (max fidelity)        ~3100      ✓
```

```text
CAT-1  `large-v3` is the default (max fidelity). Speed is explicitly NOT a priority
       here — the maintainer's call is "high fidelity, slow is fine." The user can
       pick a lighter model for speed/disk, but the default optimizes accuracy.
CAT-2  The catalog (id/label/size_mb/downloaded) is served WITHOUT importing the
       engine — it is a static list plus a file-presence stat, so `/transcribe/status`
       answers on a torch-less boot exactly like `/setup/status` does.
CAT-3  The download URL + sha256 are read from openai-whisper's own `_MODELS` map at
       download time (authoritative, version-matched) — they are NOT hardcoded here.
CAT-4  Weights cache at `parrot_data/whisper_models/<id>.pt` — NOT the HF cache.
       openai-whisper ships single-file `.pt` checkpoints with their own naming, so
       they live beside (not inside) the HF snapshot cache the OmniVoice gate uses.
```

---

## 3 — Engine (the one-paragraph rationale)

Parrot transcribes with **openai-whisper** running on the **torch + CUDA stack the sidecar already ships** (the `engine` extra). This is a deliberate choice over faster-whisper (CTranslate2 + cuDNN — a new native runtime with a real history of Windows DLL-load failures) and over a Rust + whisper.cpp + ffmpeg sidecar (which would re-introduce ffmpeg, a stated non-dependency, and a second model-download system). openai-whisper reuses the exact CUDA stack already proven to load on the user's machine; its worst case (VRAM OOM on a small GPU) **degrades gracefully to CPU**, which the maintainer accepted ("slow is fine") — whereas a missing cuDNN DLL is a hard wall, exactly the "first run hits a wall" failure Parrot's north star forbids. Audio is decoded with **PyAV (`av`)**, whose wheel bundles the ffmpeg libraries, so `webm` (the in-app recorder's format), `m4a`, `mp3`, `ogg`, `flac`, and `wav` all decode **without any system ffmpeg binary**.

```text
ENG-1  Device = device.detect_device() (CUDA → CPU; device-detection.md). fp16 on
       cuda, fp32 on cpu. ANY CUDA load/transcribe failure — OOM *or* an arch/driver
       mismatch (device.py can select 'cuda' for a GPU outside the torch wheel's
       arch list; it only warns) — falls back to CPU for that call rather than
       failing the clone. Never a first-run wall.
ENG-2  The Whisper model is loaded on demand, used, then FREED (VRAM released). It
       is never left co-resident with the OmniVoice TTS model beyond a transcription
       — transcription happens at clone time, generation at speak time, so peak VRAM
       is max(asr, tts), not the sum. Reload-per-clone is acceptable (one-shot, slow-ok).
ENG-3  Anti-hallucination (fixed, matching the Toolzy whisper.cpp config that Parrot
       is asked to mirror): temperature=0 (greedy, no temperature-fallback ladder)
       and condition_on_previous_text=False (no repetition drift). A short clean clip
       needs no separate VAD; Whisper's no-speech thresholds cover the silent case.
ENG-4  Weights load directly from the cached `.pt` PATH (whisper.load_model(path,…)),
       which skips openai-whisper's full-file sha re-hash on every load.
ENG-5  Everything torch/whisper/av is imported lazily inside `asr_manager` — the ONE
       ASR engine boundary, mirroring model_manager for TTS. The light app + the test
       suite never import it (the boundary is mocked), so pytest needs no GPU/weights.
```

---

## 4 — IPC Contract

All routes loopback-only and unprefixed, like the rest of the sidecar ([ipc-contract.md](./ipc-contract.md)). The typed client lives in `frontend/src/lib/api/transcribe.ts` (binding rule: this contract + that client move together).

| Method | Path | Body | Returns | Errors |
|--------|------|------|---------|--------|
| `GET` | `/transcribe/status` | — | `TranscribeStatus` | — |
| `POST` | `/transcribe/download` | json: `{ model }` | `{ status: "download_started", model }` | `400` unknown model; `429` within 60 s of a failed download |
| `GET` | `/transcribe/download-stream` | — | `text/event-stream` of `TranscribeDownloadEvent` | — |
| `POST` | `/transcribe` | form: `ref_audio`* (file), `model`, `language` | `{ text, language, model }` | `400` unknown model **or empty audio**; `409` model not downloaded; `415` unsupported audio ext; `500` decode/engine failure |

```ts
// frontend/src/lib/api/types.ts — mirror field-for-field.
interface TranscribeModel {
  id: string;          // "large-v3"
  label: string;       // "Large v3 (max fidelity)"
  size_mb: number;     // ~3100
  downloaded: boolean; // .pt present on disk
}
interface TranscribeStatus {
  models: TranscribeModel[];
  default_model: string;      // "large-v3"
  device: "cuda" | "cpu";     // resolved compute device (device-detection.md)
  device_label?: string;      // e.g. "GPU (CUDA) — RTX 4090"
  gpu: boolean;               // device === "cuda" — drives the "GPU acceleration on" badge
}
// Mirrors DownloadEvent (setup) but keyed by `model`, not `repo_id`.
interface TranscribeDownloadEvent {
  model: string;
  filename: string;
  downloaded: number;  // bytes
  total: number;       // bytes (0 while resolving)
  pct: number;         // 0.0–1.0
  phase: "install_start" | "resolving" | "progress"
       | "install_retry" | "install_done" | "install_error";
  error?: string;      // present on install_error / install_retry
  attempt?: number;    // present on install_retry
}
interface TranscribeResult {
  text: string;        // the transcript ("" when no speech was heard)
  language: string;    // detected (or echoed) language code, e.g. "pt"
  model: string;       // the model id used
}
```

- `POST /transcribe` is a **blocking** request (no per-step progress stream — the UI shows an indeterminate "Transcribing…" spinner). The %-bar is reserved for the multi-GB *download*, which is where progress actually matters. The route runs in a threadpool so the blocking Whisper call never stalls the event loop, and `asr_manager` serializes calls with a lock (Parrot is single-user).
- `language` accepts the same values as the clone Language picker (full English names + `"Auto"`); `"Auto"`/unknown → auto-detect. The service maps a known name to its ISO code before inference.

---

## 5 — State machines (frontend)

Two small machines in `frontend/src/lib/stores/transcribe.ts`, plus the clone screen's reaction to a fresh capture.

```text
Model/download machine (per selected model):
  unknown ──status──> not_downloaded | ready
  not_downloaded ──download──> downloading(pct) ──install_done──> verifying ──> ready
  downloading ──install_error/install_retry──> failed | downloading
  failed ──retry──> downloading           (60 s cooldown enforced server-side → 429)

Transcription machine (fires when a clip is captured AND the chosen model is ready):
  idle ──capture(model ready)──> transcribing ──200──> done(text)   ──> fills ref_text
  idle ──capture(model NOT ready)──> idle  (no fire; UI nudges "download a model first")
  transcribing ──500/decode error──> error (toast; ref_text left untouched/manual)
  done ──user edits ref_text──> (free-text; the transcript is a starting point, not a lock)
```

```text
SM-1  Auto-fire: on a new capture, IF the selected model is downloaded, transcription
      starts automatically (the user's explicit ask — "she just attaches the audio and
      it transcribes"). The ref_text field shows a spinner while it runs.
SM-2  The transcript is ALWAYS editable. It seeds ref_text; the user can correct or
      clear it. "Better empty than wrong" still holds (voice-cloning EDGE-8).
SM-3  Re-capturing (record again / pick a different file) re-fires transcription and
      replaces the field's seeded value — but only if the user hasn't hand-edited it,
      so a manual correction is never clobbered by a re-probe of the same clip.
SM-4  The selected model + the GPU badge persist across captures in the store; the
      download panel mirrors the first-run DownloadProgress component.
```

---

## 6 — Edge cases

```text
EDGE-T1  Silent / no speech. Whisper returns empty (or whitespace) text. NOT an
         error: the result is text:"" and the UI shows a gentle "We couldn't make
         out any speech — type it yourself or try a cleaner clip." ref_text stays
         empty/manual. (Mirrors voice-cloning EDGE-3's tone, without a 500.)
EDGE-T2  Model not downloaded. POST /transcribe → 409 with an actionable message
         ("Download a transcription model first."). The UI gates auto-fire on the
         `downloaded` flag, so this is a backstop, not the normal path.
EDGE-T3  Unknown model id. 400 (download and transcribe both validate against the
         catalog), same shape as the setup gate's unknown-repo 400.
EDGE-T4  Engine extra absent (no openai-whisper / av installed). 500 with the same
         "Voice engine is not installed — reinstall Parrot or run uv sync --extra
         engine" message family model_manager raises, never a raw ImportError.
EDGE-T5  Download retried within 60 s of a failure → 429 with seconds remaining
         (first-run-setup Rule 8 / setup_manager cooldown, reused verbatim).
EDGE-T6  CUDA failure (OOM, or an arch/driver/kernel-image mismatch on a GPU outside
         the shipped torch wheel's arch list). Caught; the call retries on CPU
         (slower but completes) rather than failing the clone. Logged. (ENG-1.)
EDGE-T7  Corrupt / undecodable bytes with an accepted extension. av raises → 500 with
         "Couldn't read that audio file." The clip is not transcribed; the user can
         still save the profile with a manual transcript (decode is re-attempted by
         the TTS engine at synthesis — see voice-cloning EDGE-5).
EDGE-T8  Long clip. Accepted; slower. No hard cap (a clone reference should be 3–10 s
         anyway; the clone UI already nudges toward that — voice-cloning BR-7).
EDGE-T9  Unsupported extension (outside wav/mp3/m4a/flac/ogg/webm). 415 before any
         decode, matching the profile-create extension gate (voice-cloning EDGE-5).
EDGE-T10 Language mismatch. If the user picked a language that isn't what was spoken,
         the transcript may be wrong — it is editable (SM-2), and "Auto" (the default)
         sidesteps this by detecting. The detected code is returned for display.
```

---

## 7 — Data

| Artifact | Path | Written by | Notes |
|---|---|---|---|
| Whisper weights | `parrot_data/whisper_models/<id>.pt` | `/transcribe/download` | Single-file checkpoints; sha256-verified, atomic `.part`→final rename. Survive upgrades (under `parrot_data/`, no migration). |
| Transcript | `voice_profiles.ref_text` (existing) | the clone save (unchanged) | The transcript is just the value the user saves into the existing field — this feature writes no new storage of its own. |

No SQLite change. No alembic migration. The reference audio is decoded **in memory** for transcription and is otherwise the same file the clone flow already stores ([voice-cloning.md §7](./voice-cloning.md)).

---

## 8 — Dependencies

Added to the `engine` optional-dependency extra (`sidecar/pyproject.toml`) — never the light default sync, so the test suite stays torch-free:

```text
openai-whisper   the ASR model + loader (imported as `whisper`); reuses the
                 already-present torch/CUDA. Pinned high enough to include
                 `large-v3-turbo`.
av (PyAV)        in-wheel ffmpeg libs for decoding webm/m4a/mp3/ogg/flac/wav to a
                 16 kHz mono float32 array — so no system ffmpeg binary is required.
```

Production/first-run installs these with the rest of the engine (`uv sync --no-dev --extra engine` + the `cpu`/`cu124` torch extra) — see [packaging.md](./packaging.md).
</content>
</invoke>
