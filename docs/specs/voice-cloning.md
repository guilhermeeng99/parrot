# Voice Cloning

Capturing a short reference sample of a speaker and turning it into a reusable **VoiceProfile** that Parrot can speak any typed text in. Cloning is the first of Parrot's two jobs (the second, turning a profile into audio, lives in [synthesis.md](./synthesis.md)). A profile is created once and reused for every subsequent generation.

This spec owns the **capture flow only**: the two capture paths (record in-app, or upload a file), reference-audio normalization, `ref_text` guidance, and the create→profile state machine plus its failure modes. It summarizes the VoiceProfile entity for context but does **not** re-specify the `/profiles` endpoint contract — the full CRUD, lock/unlock, audio, and usage surface is owned by [voice-profiles.md](./voice-profiles.md). When this spec needs an endpoint, it links there. See [../../CLAUDE.md](../../CLAUDE.md) for cross-cutting conventions.

---

## 1 — Entity Summary (full contract in voice-profiles.md)

A VoiceProfile is one row in the `voice_profiles` table plus its reference audio file on disk under `parrot_data/voices/`. The columns below are reproduced for capture-flow context only; [voice-profiles.md](./voice-profiles.md) is the authoritative owner of the entity and its lifecycle.

```text
voice_profiles
  id                TEXT  PK     -- uuid4()[:8], e.g. "a3f9c1d2"
  name              TEXT  NOT NULL
  ref_audio_path    TEXT         -- filename only (relative to voices/), e.g. "a3f9c1d2.wav"
  ref_text          TEXT  DEFAULT ''
  language          TEXT  DEFAULT 'Auto'
  instruct          TEXT  DEFAULT ''   -- optional style; de-emphasized in UI (see §6)
  locked_audio_path TEXT  DEFAULT ''   -- set when a generated take is locked as the new reference
  seed              INTEGER NULL       -- locked seed, if any
  is_locked         INTEGER DEFAULT 0  -- 0 | 1
  created_at        REAL              -- epoch seconds
```

Invariants the capture flow must uphold (the entity's broader invariants live in voice-profiles.md):

```text
INV-1  id is unique, 8 hex chars, server-generated. Clients never supply it.
INV-2  name is non-empty after trim().
INV-3  ref_audio_path stores the FILENAME only, not an absolute path. The
       sidecar joins it against parrot_data/voices/ at read time. This keeps
       parrot_data/ relocatable across machines and upgrades.
INV-4  Exactly one DB row per profile, and at most two audio files per profile:
       <id><ext> (the reference) and optionally <id>_locked.wav.
INV-5  The audio file and the DB row are created together. If the INSERT fails,
       the orphaned audio file is deleted (no half-created profiles on disk).
INV-6  ref_text describes the WORDS spoken in ref_audio. It is not the text to
       synthesize. Empty string is valid (model can auto-handle), but a correct
       ref_text measurably improves clone quality (see §3, BR-4).
INV-7  When is_locked = 1, locked_audio_path is non-empty and points to a real
       file; effective reference resolution prefers it (see synthesis.md).
```

The on-the-wire shape returned by the IPC layer is the raw row (all columns above) plus nothing more — there is no computed/expanded field.

---

## 2 — Business Rules (capture)

These rules govern how a capture becomes a stored profile. Endpoint-level rules (validation responses, lock/unlock semantics, deletion cascade) are specified in [voice-profiles.md](./voice-profiles.md); they are referenced here only where the capture flow depends on them.

```text
BR-1   Creating a profile requires a name and exactly one reference audio file.
       Either capture path (record or upload) produces that one file.

BR-2   The reference file is written to parrot_data/voices/<id><ext>, where
       <ext> is taken from the uploaded/recorded filename (defaulting to .wav
       when absent). The DB row stores the bare filename.

BR-3   ref_text, language, instruct, and seed are optional at creation time and
       default to '', 'Auto', '', and NULL respectively. They can be edited
       later via the metadata-update endpoint owned by voice-profiles.md
       (name, ref_text, instruct, language only).

BR-4   ref_text, when supplied, should be the verbatim transcript of the
       reference audio. The model prepends it to the synthesis text so the
       reference tokens and text tokens are aligned; an accurate transcript
       sharpens prosody and timbre transfer. A wrong transcript degrades the
       clone — better empty than wrong.

BR-5   A profile is reusable: one capture, unlimited generations. Synthesis
       references the profile by profile_id (see synthesis.md), so re-cloning
       is never required to speak new text.

BR-6   Reference audio is resampled to the model's rate (24 kHz) and downmixed
       to mono before tokenization. Stereo, 44.1/48 kHz, and non-WAV inputs are
       all accepted and normalized — the user is not asked to pre-convert.

BR-7   Recommended reference length is 3–10 s of clean speech. Audio longer
       than ~20 s is accepted but logged as suboptimal (slower, more memory,
       degraded quality) and may be trimmed at the largest silence gap when no
       ref_text is provided (see §6, EDGE-2).

BR-8   Profile names are NOT required to be unique. Two profiles may share a
       name; they remain distinct rows with distinct ids. The UI disambiguates
       and SHOULD warn on duplicate names (see EDGE-7), but the backend does
       not reject them.

BR-9   Capture and cloning are local-only. No network call leaves the machine
       for cloning; the reference audio never leaves parrot_data/.
```

---

## 3 — How cloning works (high level)

Cloning does not train a per-speaker model. It converts the reference sample into a conditioning prompt that the synthesis pass attends to. The mechanism, grounded in the engine:

1. **Load + normalize.** The reference file is loaded (torchaudio, with a pydub/ffmpeg fallback for formats torchaudio can't decode), resampled to 24 kHz, and downmixed to mono → a `(1, T)` waveform.
2. **Level guard.** The waveform's RMS is measured. Very quiet samples (`0 < rms < 0.1`) are scaled up so the encoder sees usable signal; this `ref_rms` is also stored on the prompt and used to match the output's loudness back to the reference.
3. **Clean up (optional, default on).** Long samples are trimmed to a sensible window at the largest silence gap, and mid/edge silences are removed. If the sample is empty after silence removal, cloning fails fast (EDGE-3).
4. **Tokenize.** The cleaned waveform is encoded by the audio tokenizer into discrete reference audio tokens of shape `(C, T)` — the speaker's acoustic fingerprint in the model's codebook space.
5. **Bundle into a prompt.** The tokens, the (optionally auto-punctuated) `ref_text`, and `ref_rms` form a reusable voice-clone prompt.

At synthesis time the prompt is prepended to the target text so the model generates new speech conditioned on the reference voice. That conditioning + iterative decoding is **out of scope here** — see [synthesis.md](./synthesis.md). The contract this spec guarantees is: a stored VoiceProfile contains everything synthesis needs (reference audio file + ref_text + language), and the engine derives the tokens on demand.

> Implementation note: the engine exposes a `create_voice_clone_prompt(ref_audio, ref_text)` step that performs 1–5. Parrot does not persist the token tensor; it persists the **audio file** and re-derives tokens per generation. This keeps `parrot_data/` portable and backward-compatible (INV-3) and avoids invalidating stored profiles when the tokenizer changes.

### What `ref_text` is for

The model concatenates `ref_text` with the synthesis text into one stream, so the reference *audio* tokens line up with the reference *words*. With an accurate transcript the model has an explicit audio↔text anchor and clones prosody and timbre more faithfully. With no transcript it still works (and the engine can fall back to an internal estimate), but quality is more variable. Hence BR-4: an accurate transcript helps, a wrong one hurts.

---

## 4 — Capture Paths & IPC

Capture has exactly one creation entry point on the sidecar at `http://127.0.0.1:3900`, and it is owned by [voice-profiles.md](./voice-profiles.md). This spec describes how the two front-end capture paths produce the single multipart payload that endpoint expects; it does **not** re-specify the endpoint's request/response/error contract.

### The two capture paths converge on one payload

```text
Record path (in-app)
  - Web Audio MediaRecorder captures mic input into a blob.
  - On stop, the blob is held in memory (previewable) as the reference sample.

Upload path
  - User selects a file (wav/mp3/m4a/flac/ogg/webm).
  - The file is held in memory (previewable) as the reference sample.

Both paths produce the SAME captured artifact and the SAME save payload:
  multipart form:
    name       string  (required)  -> trimmed; must be non-empty
    ref_audio  file    (required)  -> the captured blob OR the uploaded file
    ref_text   string  (optional, default "")
    instruct   string  (optional, default "")     -- de-emphasized (§6)
    language   string  (optional, default "Auto")
    seed       integer (optional, default null)
```

Sending that payload to the profile-creation endpoint (full contract — fields, `200` shape, `400/415/500` error semantics, on-disk side effects, INSERT-failure cleanup) is documented in [voice-profiles.md](./voice-profiles.md). Note: the create path validates the audio **extension** only (it has no decoder), so format rejection is a `415` at create; an unreadable-bytes or all-silence failure only surfaces later as a synthesis error (see EDGE-3/EDGE-5). The error conditions the capture UI must surface are enumerated in §6 below.

### Endpoints the capture flow references (all owned by voice-profiles.md)

The capture flow reads and reacts to other profile endpoints, but does not own them. For their exact contracts see [voice-profiles.md](./voice-profiles.md):

- Profile creation (the multipart payload above).
- Profile read/list (to refresh the list after a successful save).
- Reference-audio fetch (to preview a saved profile's effective reference).
- Usage lookup (`/profiles/{id}/usage`) — returns the most-recent generation history for the profile plus a total. The recency cap is stated authoritatively in voice-profiles.md (see the PARROT canon: ≤20 most-recent rows in `synth_recent`, with `synth_total`). This spec does not restate the number.

Synthesis itself (`POST /generate`) is documented in [synthesis.md](./synthesis.md).

---

## 5 — State Machine (capture → profile)

Frontend store for the clone flow (Svelte store under `frontend/src/lib/stores/`; the typed IPC client lives in `frontend/src/lib/api/`). The store owns capture, not synthesis.

```text
states:
  idle
  recording          -- Web Audio MediaRecorder active (record path)
  captured           -- a blob/file is held in memory, previewable, not yet saved
  saving             -- profile-creation request in flight
  saved              -- 200 received; profile id known; flow resets to idle
  error              -- capture or save failed; message shown, retryable

transitions:
  idle       --startRecording-->  recording        (mic permission granted)
  idle       --selectFile------>  captured         (upload path)
  recording  --stopRecording-->   captured         (blob finalized)
  recording  --cancel--------->   idle
  captured   --rerecord------->   recording         (discard current blob)
  captured   --clear---------->   idle
  captured   --save(name)----->   saving
  saving     --200------------>   saved --> idle    (re-fetch the profiles list)
  saving     --4xx/5xx-------->   error
  error      --retry---------->   saving            (same payload)
  error      --dismiss------->    captured | idle

guards:
  - save is disabled until name is non-empty AND a capture exists (mirrors BR-1)
  - mic permission denied on startRecording -> error (EDGE-6), not recording
  - the record path and upload path converge on the SAME `captured` state and
    the SAME save payload, so downstream code never branches on capture source
```

On `saved`, the store SHOULD refresh the profiles list by re-fetching it (a plain re-GET of the list endpoint owned by voice-profiles.md) so the new voice appears immediately. There is no event bus or push channel; cross-tab freshness is achieved by re-fetching after a mutation.

---

## 6 — Edge Cases

```text
EDGE-1  Too short. A sub-1s sample technically clones but produces an unstable
        voice. The UI SHOULD warn below ~3 s. The backend does NOT reject on
        length alone — the only hard length failure is "empty after cleanup"
        (EDGE-3).

EDGE-2  Too long. Samples > ~20 s are accepted but suboptimal (slower, more
        memory, weaker clone). When ref_text is NOT provided, the engine may
        trim to a window at the largest silence gap. When ref_text IS provided,
        trimming is skipped — trimming would desync audio from its transcript.
        UI guidance: trim to 3–10 s of clean speech.

EDGE-3  Silent / all-silence. The create path has no audio decoder, so it
        cannot detect this — the clip is accepted at creation. The failure
        only surfaces at SYNTHESIS time: when the engine decodes the reference
        and finds it empty after silence removal, cloning fails as a synthesis
        error (500; see synthesis.md), not a 422 from the creation endpoint.
        Surface "We couldn't hear any speech in that clip — record again with
        the mic closer, or upload a clearer sample."

EDGE-4  Very quiet (low RMS). Not an error: the engine scales the waveform up
        before tokenizing and remembers the original level so output loudness
        matches. Quiet-but-audible samples clone fine.

EDGE-5  Unsupported / corrupt format. The CREATE endpoint validates the file
        EXTENSION only — it returns 415 (with the offending extension named;
        see voice-profiles.md) for anything outside the supported container
        set {wav,mp3,m4a,flac,ogg,webm}. The actual decode runs later, at
        synthesis: torchaudio is tried first, then a pydub/ffmpeg fallback for
        formats it can't decode. If both fail to decode the bytes (e.g. a
        corrupt file with an accepted extension), cloning fails as a synthesis
        error (500; see synthesis.md) — create does not catch corrupt bytes.
        Note: a successful clone normalizes channels and sample rate, so the
        user never has to convert mp3/m4a/flac/ogg/webm to wav themselves.

EDGE-6  Mic permission denied (record path). The Web Audio capture never
        starts; the store goes idle -> error with a permission-specific
        message and an "Upload a file instead" affordance. This is a
        frontend-only failure — no request is sent.

EDGE-7  Duplicate name. Allowed by the backend (BR-8). The UI SHOULD warn
        ("You already have a voice named 'Alex' — save anyway?") but must not
        block. The two profiles are distinguishable by id and created_at.

EDGE-8  Missing ref_text. Allowed (defaults to ''). The clone still works; the
        engine handles the empty-transcript case internally. Encourage, but
        never require, a transcript (BR-4). "Better empty than wrong."

EDGE-9  Reference file deleted out-of-band. If voices/<id><ext> is missing at
        read/synthesis time, the reference-audio fetch returns 404 ("Audio file
        missing") and synthesis fails clearly rather than silently producing a
        wrong voice. The DB row still lists the profile; re-clone to fix.
        (Endpoint behavior: see voice-profiles.md.)

EDGE-10 INSERT fails after the file is written. The just-written audio file is
        removed and the error re-raised, so a failed clone leaves no orphaned
        file and no phantom row (INV-5).

EDGE-11 Concurrent delete. A profile open in one window and deleted in another
        yields a 404 on the next read with the "deleted from another tab"
        message; the UI should drop it from the list when a re-fetch no longer
        returns it (plain re-GET, no event channel).

EDGE-12 instruct (de-emphasized). The column and form field exist for
        compatibility, but Parrot's cloning UI does NOT surface style presets.
        Sending instruct is permitted and passed through to synthesis; the
        default flow leaves it ''. Do not document personality/voice-design
        presets as a Parrot feature — they are out of scope.
```

---

## 7 — Data

Files and tables the capture flow touches. The endpoints listed under "Written by" are owned by [voice-profiles.md](./voice-profiles.md); they are named here only to show which artifact each path produces.

| Artifact | Path / table | Written by | Notes |
|---|---|---|---|
| Reference audio | `parrot_data/voices/<id><ext>` | profile creation | Filename stored in `ref_audio_path`; format preserved as uploaded/recorded. |
| Locked take | `parrot_data/voices/<id>_locked.wav` | profile lock | Always `.wav`; deleted on unlock/delete. Lock/unlock contract: voice-profiles.md. |
| Profile row | `voice_profiles` | profile create/update/lock | One row per profile (§1). |
| History FK | `generation_history.profile_id` | profile delete | Set NULL on delete; history rows survive. Cascade contract: voice-profiles.md. |
| DB file | `parrot_data/parrot.db` (WAL, `foreign_keys=ON`) | all of the above | Created idempotently; schema changes go through alembic with a tested upgrade path. |

`parrot_data/` must survive app upgrades with no manual migration. Because profiles persist the **audio file** (not derived tokens) and store filenames relatively (INV-3), an existing `parrot_data/voices/` directory keeps working across engine and tokenizer updates.
