# Voice Profiles (Library)

A **voice profile** is a reusable clone of a voice: a reference audio sample plus the metadata Parrot needs to speak new text in that voice. The library is the CRUD layer over the `voice_profiles` table — list, create, read, update, delete — plus the **lock/unlock** reproducibility feature that pins a generated output as the profile's deterministic reference. Profiles are the only first-class user-created entity in Parrot; everything in the [synthesis](./synthesis.md) flow resolves a voice through a profile (or an ad-hoc reference upload).

Conventions: [../../CLAUDE.md](../../CLAUDE.md).

---

## 1 — Entity Contract

```text
voice_profiles
  id                TEXT PRIMARY KEY      -- uuid4()[:8], app-generated, opaque
  name              TEXT NOT NULL         -- user-facing label; trimmed; non-empty
  ref_audio_path    TEXT                  -- filename only (not absolute), under parrot_data/voices/
  ref_text          TEXT DEFAULT ''       -- transcript of the reference clip (improves cloning)
  language          TEXT DEFAULT 'Auto'   -- 'Auto' or a model-supported language tag
  instruct          TEXT DEFAULT ''       -- optional style hint; de-emphasized in the Parrot UI
  locked_audio_path TEXT DEFAULT ''       -- filename only; set only while is_locked = 1
  seed              INTEGER NULL          -- pinned RNG seed; set on lock, cleared on unlock
  is_locked         INTEGER DEFAULT 0     -- 0 | 1
  created_at        REAL                  -- unix epoch seconds
```

Invariants:

- `id` is generated server-side as `uuid4()[:8]`. The client never supplies it.
- All `*_path` columns store a **bare filename**, never an absolute path. The on-disk location is derived at read time by joining with `parrot_data/voices/`. This keeps the DB portable across machines (paths are re-joined at read time).
- `name` is always stored trimmed and is never the empty string (enforced on create and update).
- `is_locked` and `locked_audio_path` move together: `is_locked = 1` ⟺ `locked_audio_path` is a non-empty filename; `is_locked = 0` ⟺ `locked_audio_path = ''`. No third state is valid.
- `seed` is non-null **only** while locked. Unlock clears it back to `NULL`.
- `language` defaults to `'Auto'` (model auto-detects across the ~600 zero-shot languages). It is stored trimmed.
- `instruct` is persisted for forward-compatibility but is not surfaced as a primary control in the Parrot UI.

> The legacy OmniVoice `personality` column and the `/personalities` preset endpoint are **out of scope** for Parrot (no voice-design / personality picker). They are not part of this contract and must not be re-introduced.

The table is created idempotently (`CREATE TABLE IF NOT EXISTS`, WAL, `foreign_keys = ON`) and kept current through alembic. Existing `parrot_data/` databases upgrade with no manual migration. See [architecture.md](./architecture.md).

---

## 2 — Business Rules

1. **Create requires a name and a reference clip.** `POST /profiles` rejects a missing/blank `name` and a missing `ref_audio` file. The uploaded audio is written to `parrot_data/voices/{id}{ext}`, where `ext` is taken from the upload's original filename (defaulting to `.wav`).
2. **Create is atomic against orphans.** If the audio file is written but the DB insert fails, the just-written file is deleted before the error propagates. A failed create leaves no file and no row.
3. **Read by id is the source of truth.** `GET /profiles/{id}` returns the full row. A missing id is a `404` with a message that names the likely cause (deleted from another tab/window).
4. **Update is a partial patch.** `PUT /profiles/{id}` changes only the fields present on the payload (`name`, `ref_text`, `instruct`, `language`). A patch with no editable fields is a `400`. `name` and `language` are trimmed before storing.
5. **A profile may not be renamed to empty.** A `name` consisting only of whitespace is rejected with `400` and the existing name is preserved.
6. **Reference audio is not editable via update.** `PUT` cannot change `ref_audio_path`. Replacing the reference clip means deleting and re-creating the profile (or, for reproducibility, using lock — see Rule 8).
7. **Audio fetch prefers the locked clip.** `GET /profiles/{id}/audio` serves `locked_audio_path` when present, otherwise `ref_audio_path`. Distinct `404`s distinguish "no such profile", "profile has no audio recorded", and "audio file is missing on disk".
8. **Lock pins a deterministic reference from history.** `POST /profiles/{id}/lock` takes a `history_id`, copies that generation's audio into `parrot_data/voices/{id}_locked.wav`, and writes back: `locked_audio_path`, the optional `seed`, `is_locked = 1`, and `ref_text` set to the first 100 characters of the history item's text. From then on, synthesis through this profile reproduces the pinned voice.
9. **Unlock reverts to the original clone.** `POST /profiles/{id}/unlock` deletes the locked WAV from disk (if present), and clears `locked_audio_path = ''`, `seed = NULL`, `is_locked = 0`. `ref_audio_path` and `name` are untouched, so the profile reverts to behaving like its pre-lock self.
10. **Lock only copies the WAV — it does not move history.** The source `generation_history` row is unchanged; lock duplicates its audio into the voices dir. Deleting that history row later does not break a locked profile (the copy is independent).
11. **Delete null-outs history, never cascades it.** `DELETE /profiles/{id}` removes both on-disk files (`ref_audio_path`, `locked_audio_path`) if they exist, sets `generation_history.profile_id = NULL` for every row referencing the profile, then deletes the row. History rows survive with `profile_id = NULL`. This is required because `foreign_keys = ON` would otherwise abort the delete.
12. **Cross-tab consistency is by re-fetch, not by push.** Parrot has no event bus or pub/sub channel. After a successful mutation (create / update / lock / unlock / delete), other open windows converge by plain re-fetch — they re-`GET /profiles` (on focus, or via a periodic background poll) and replace their list. A mutation never pushes a payload to other windows.
13. **Usage is read-only and capped.** `GET /profiles/{id}/usage` returns recent generations (most-recent first, capped at 20) plus a total count. It mutates nothing.

---

## 3 — IPC Contract

All endpoints are REST on the Python sidecar at `http://127.0.0.1:3900`. Profile id is the 8-char `uuid4()` slice. Bodies are JSON unless marked multipart form.

### `GET /profiles`
List all profiles, newest first (`ORDER BY created_at DESC`).
- **Returns** `200` — array of full profile rows (see Entity Contract).

### `POST /profiles` — multipart form
Create a profile from a reference clip.
- **Form fields:** `name` (required), `ref_audio` (file, required), `ref_text` (default `""`), `instruct` (default `""`), `language` (default `"Auto"`), `seed` (optional int).
- **Returns** `200` — `{ "id": "<8char>", "name": "<name>" }`.
- **Side effects:** writes `parrot_data/voices/{id}{ext}`; inserts row.
- **Errors:** `422` missing required form field; on DB-insert failure the orphan audio file is removed and the error propagates as `500`.

### `GET /profiles/{id}`
Full profile record (for the profile detail page).
- **Returns** `200` — full row.
- **Errors:** `404` — *"That voice profile doesn't exist. It may have been deleted from another tab."*

### `PUT /profiles/{id}` — JSON
Partial update.
- **Body:** any subset of `{ name?, ref_text?, instruct?, language? }`. Fields set to `null`/omitted are left unchanged.
- **Returns** `200` — the updated full row.
- **Errors:** `400` empty `name`; `400` no editable fields present in body; `404` no such id.

### `GET /profiles/{id}/audio`
Stream the profile's representative audio (`audio/wav`).
- **Resolution:** `locked_audio_path` if set, else `ref_audio_path`.
- **Returns** `200` — `FileResponse`, `media_type: audio/wav`.
- **Errors:** `404` "Profile not found"; `404` "No audio available" (neither path set); `404` "Audio file missing" (path set but file absent on disk).

### `GET /profiles/{id}/usage`
Where this voice has been used.
- **Returns** `200` —
  ```json
  {
    "synth_recent": [ { "id", "text", "audio_path", "created_at", "generation_time" } ],
    "synth_total": 0
  }
  ```
  `synth_recent` is the ≤20 most-recent generations for this profile (newest first); `synth_total` is the full count.

> The OmniVoice `usage` response also reported dub-project segment hits (`projects`, `project_total_segments`) by scanning `studio_projects.state_json`. Parrot has **no dubbing / projects**, so those keys are dropped — there is no `projects` field. Usage is purely the `generation_history` view.

### `POST /profiles/{id}/lock` — multipart form
Pin a generated output as the deterministic reference.
- **Form fields:** `history_id` (required), `seed` (optional int).
- **Effect:** copies `parrot_data/outputs/{history.audio_path}` → `parrot_data/voices/{id}_locked.wav`; sets `locked_audio_path`, `seed`, `is_locked = 1`, and `ref_text = history.text[:100]`.
- **Returns** `200` — `{ "locked": true, "profile_id": "<id>", "locked_audio_path": "{id}_locked.wav" }`.
- **Errors:** `404` profile not found; `404` history item not found or has no `audio_path`; `404` source audio missing on disk.

### `POST /profiles/{id}/unlock`
Clear the locked reference and revert to the original clone.
- **Effect:** deletes the locked WAV from disk if present; sets `locked_audio_path = ''`, `seed = NULL`, `is_locked = 0`.
- **Returns** `200` — `{ "unlocked": true, "profile_id": "<id>" }`.
- **Errors:** `404` profile not found. (A profile that is already unlocked unlocks again with no error — the operation is idempotent.)

### `DELETE /profiles/{id}`
Delete the profile and its files; preserve history.
- **Effect:** removes `ref_audio_path` and `locked_audio_path` files if they exist; `UPDATE generation_history SET profile_id = NULL WHERE profile_id = {id}`; `DELETE FROM voice_profiles WHERE id = {id}`.
- **Returns** `200` — `{ "deleted": "<id>" }`.
- **Errors:** deleting a non-existent id is a no-op success (no row removed, files absent).

> Profile resolution at synthesis time (locked-audio path vs `ref_audio_path`, stored `seed`/`ref_text`) is defined in [synthesis.md](./synthesis.md) under `POST /generate`. This spec owns the storage contract; synthesis owns how that storage is consumed.

---

## 4 — State Machines

### 4.1 Library store (`frontend/src/lib/stores/profiles.ts`)

Holds the list rendered in the voice library. Typed IPC lives in `frontend/src/lib/api/profiles.ts`; the store never talks HTTP directly.

```text
states: idle → loading → loaded
                   ↘ error
```

| State     | Entered when                              | Holds                       |
|-----------|-------------------------------------------|-----------------------------|
| `idle`    | initial                                   | `[]`                        |
| `loading` | first `load()` (no cached list yet)       | previous list (if any)      |
| `loaded`  | `GET /profiles` resolves                  | `profiles: Profile[]`       |
| `error`   | `GET /profiles` rejects                   | last good list + `error`    |

Transitions:

- `load()` → `loading` → `loaded` | `error`.
- `refresh()` (background re-fetch) re-runs `GET /profiles` **without** dropping to `loading` when a list is already shown, to avoid flicker. On success replaces the list; on failure stays `loaded` and surfaces a non-blocking toast.
- On `error`, the store keeps the last successfully loaded list visible (stale-but-usable) rather than blanking the library.

### 4.2 Optimistic mutations

Single-item operations update the store optimistically, then reconcile against the server response:

- **rename / edit** — patch the in-memory row immediately; on `PUT` success replace with the returned row; on failure roll back to the pre-edit row and toast.
- **delete** — remove the row immediately; on `DELETE` success keep it removed; on failure re-insert at its prior position and toast.
- **lock / unlock** — flip `is_locked` (and clear/keep `seed`, `locked_audio_path`) immediately; reconcile on response.

### 4.3 Cross-tab refresh by re-fetch

Parrot has no event bus or pub/sub channel — `ws://127.0.0.1:3900/ws/tts` is the chunked-PCM streaming-synthesis socket only and carries no profile events. Multiple Parrot windows (and the library vs. the open profile detail page) stay consistent by plain re-fetch instead of push:

- The window that originated a mutation has already applied its optimistic update and reconciled against the mutation's response, so its list is current immediately.
- Other windows converge by re-running `GET /profiles` — on window focus and/or via a periodic background `refresh()`. Re-fetch is idempotent: it replaces the list with the server's current state and must not flicker when nothing changed.
- The same re-fetch keeps the open profile detail page in step after a mutation in the library list.

---

## 5 — Edge Cases

- **Delete a profile referenced by history.** Must null-out, never cascade. After delete, every `generation_history` row that pointed at the profile has `profile_id = NULL` and is still listed in history; the delete must not fail with a foreign-key constraint error. (Testable: create profile → generate once → delete profile → assert history row survives with `profile_id IS NULL`.)
- **Profile deleted in another window.** A `GET`/`PUT`/`lock`/`unlock` on an id that was just removed returns `404` with the "deleted from another tab/window" message. The frontend treats this `404` as a cue to drop the row and `refresh()`, not as a crash.
- **Rename to empty / whitespace.** `PUT` with `name: "   "` is rejected `400`; the stored name is unchanged. The optimistic rename must roll back.
- **Lock with a missing history audio file.** If `generation_history.audio_path` is set but the file is gone from `parrot_data/outputs/`, lock fails `404` ("Audio file not found on disk") and the profile is left **unlocked and unchanged** — no partial `locked_audio_path` is written.
- **Lock referencing an unknown / audio-less history id.** `404` ("History item not found or has no audio"); no DB write.
- **Unlock an already-unlocked profile.** Idempotent success; no file to delete, columns already cleared.
- **Locked WAV missing on unlock.** If `locked_audio_path` points at a file that is already gone, unlock skips the `os.remove` and still clears the columns — it must not error.
- **Locked WAV missing on audio fetch.** `GET /profiles/{id}/audio` returns `404` "Audio file missing" rather than serving a stale `ref_audio_path`; the resolution still prefers the (missing) locked path, so a broken lock surfaces visibly instead of silently downgrading.
- **Create with a non-`.wav` upload.** The extension is taken from the uploaded filename and preserved in `ref_audio_path`; a filename with no extension defaults to `.wav`.
- **Create DB failure after file write.** The orphaned audio file is removed before the error surfaces — no dangling files in `parrot_data/voices/`.
- **Empty library.** `GET /profiles` returns `[]`; the store lands in `loaded` with an empty list, and the UI shows the "clone your first voice" empty state, not an error.
- **Path portability.** Because only filenames are stored, a `parrot_data/` directory copied between machines resolves audio correctly on the new host (paths are re-joined at read time).

---

## 6 — Data

| Touched              | What                                                                                 |
|----------------------|--------------------------------------------------------------------------------------|
| `voice_profiles`     | Primary table for all CRUD + lock/unlock. SQLite, WAL, `foreign_keys = ON`.           |
| `generation_history` | Read by `/usage`; `profile_id` FK **nulled** (not cascaded) on profile delete.        |
| `parrot_data/voices/`   | `{id}{ext}` reference clips and `{id}_locked.wav` locked clips. Files removed on delete; locked file removed on unlock. |
| `parrot_data/outputs/`  | Source of the audio copied into `voices/` on lock (read-only here; owned by [synthesis.md](./synthesis.md)). |

Cross-tab consistency is by re-fetch (`GET /profiles`), not by any event/push channel — see §4.3.

All paths live under `parrot_data/`, which must survive upgrades with no manual migration ([../../CLAUDE.md](../../CLAUDE.md)).
