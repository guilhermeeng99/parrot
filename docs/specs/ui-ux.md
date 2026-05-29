# UI & UX

How Parrot's design system becomes screens. [design-system.md](./design-system.md) owns the tokens and the shared component recipes (the `@theme` block, the Toolzy "Sky Blueprint on Bright Paper" palette, the Svelte UI primitives under `frontend/src/lib/components/ui/`). This spec owns the *application of* those tokens: the app shell, the five screens, the core flow that ties them together, and the interaction states every async action must render. Behavior is owned by the feature specs and linked inline; this spec never re-specifies an endpoint contract.

Parrot has two jobs — **clone a voice** ([voice-cloning.md](./voice-cloning.md)) and **speak text** ([synthesis.md](./synthesis.md)) — gated behind a one-time model download ([first-run-setup.md](./first-run-setup.md)) and configured through a three-group [settings.md](./settings.md) surface. The screen set is intentionally tiny; the burden of this spec is making each screen *clear under failure*, because "a first-run that actually works, and an error that tells you what to do" is Parrot's core value ([../../CLAUDE.md](../../CLAUDE.md)).

**Scope locks (V1):** LIGHT THEME ONLY — dark mode is backlog. No UI-zoom feature — backlog. Any earlier "theme: light|dark default dark + zoom" model from `settings.md` / `design-system.md` is superseded by the Toolzy light-only system. One type family: Montserrat (token `--font-gilroy`). One action color: `action-blue`. 8px grid. The process model (three processes, loopback `:3900`, supervisor-owned sidecar lifecycle) is [architecture.md](./architecture.md).

---

## 1 — App Shell & Layout

The window is a single Tauri WebView ([architecture.md §1](./architecture.md)). Inside it the layout is the Toolzy app shell, reskinned for Parrot.

### 1.1 — The shell

```
┌──────────────────────────────────────────────────────────────────────┐
│  HEADER (sticky, bg-snow-white/90 + backdrop-blur, border-b           │
│          border-outline-gray, h-16)                                    │
│  ┌──────────────────────── max-w-[1000px], px-6 ─────────────────────┐ │
│  │ 🦜 Parrot  [local]      ‹Clone › Speak › Settings›       v0.3.0 / │ │
│  │ (heading,bold)(Badge)        (Pill nav, ml-auto)     Update btn   │ │
│  └────────────────────────────────────────────────────────────────┘ │
├──────────────────────────────────────────────────────────────────────┤
│  MAIN  (body bg-cloud-mist)                                            │
│  ┌──────────────────────── max-w-[1000px], px-6, py-12 ─────────────┐ │
│  │  [optional centered screen header: title + one-line description] │ │
│  │                                                                  │ │
│  │  [ Card(s) — flex flex-col gap-6, rounded-2xl bg-snow-white      │ │
│  │    p-6 shadow-sm-2 ]                                              │ │
│  └──────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────┘
```

- **Header** — sticky `top-0 z-50`, `border-b border-outline-gray bg-snow-white/90 backdrop-blur`, inner row `h-16 max-w-[1000px] mx-auto px-6 flex items-center gap-4`.
  - **Logo**: `text-heading font-bold text-midnight-indigo` ("Parrot", with a small parrot glyph allowed — decorative, `aria-hidden`).
  - **Local badge**: the DS **Badge** recipe (`rounded-full bg-pale-gray px-2 py-1 text-body font-semibold text-glacier-blue`) reading **"local"** (Toolzy says "native"; Parrot's truth is *runs on your machine, offline*). Static, not a button. It reinforces the local-first promise on every screen.
  - **Nav** (`ml-auto flex gap-2`): three **Pill**s — **Clone · Speak · Settings** — using `pill(active)` from the DS. Active pill `bg-action-blue text-snow-white`; inactive `bg-pale-gray text-midnight-indigo hover:bg-platinum-tint`. This is the whole NavRail; Parrot has no sidebar.
  - **Right slot**: when an update is available, the small update button (`rounded-lg bg-action-blue px-3 py-1.5 text-body font-semibold text-snow-white transition hover:brightness-105 disabled:opacity-50` + focusRing) reading **"Update to vX.Y.Z"**; while applying, **"Updating…"** + disabled. With no update, a quiet version label `text-body text-slate-blue` ("v0.3.0"). Update/version come from the Tauri updater (native concern, [architecture.md §1.2](./architecture.md)), not the sidecar.

- **Main** — `max-w-[1000px] mx-auto px-6 py-12`, on `cloud-mist` body. Each screen optionally opens with a centered header (`text-display-sm font-bold text-midnight-indigo` title + `text-body-lg text-slate-blue` description, `max-w-xl mx-auto`), then a single column of **Card**s with `gap-6`. ~40px vertical section rhythm.

- **Nav gating.** While the setup gate is unresolved (`models_ready=false`, [first-run-setup.md §3 Rule 1](./first-run-setup.md)) the Clone/Speak pills are **disabled** (`steel-gray`, `aria-disabled`, not clickable) and the whole main area is the Setup screen. Settings stays reachable (the user may need to enter an HF token to unblock a gated download). Once `models_ready=true`, all three pills are live and Parrot defaults to **Clone** (a new user's first job is to make a voice).

### 1.2 — Window sizing & responsive behavior

- **Min window size: 760 × 600.** Below the `1000px` container the content is full-width with `px-6` gutters preserved; the header nav pills `flex flex-wrap` so they never clip.
- The layout is fluid between the min width and `1000px`, then centers. There is no mobile breakpoint — this is a desktop window, not a responsive web page. Cards stack in one column at every width; multi-control rows (`flex flex-wrap gap-2/3`) reflow rather than overflow.
- Long content scrolls vertically inside `main`; the header stays sticky. The History list (Speak) and the profile list (Clone) scroll within their cards or the page, never horizontally.
- No UI-zoom control in V1. The OS/WebView's own zoom is untouched; honoring it is a backlog item alongside dark mode.

---

## 2 — Screen-by-Screen UX

Each screen below names the DS components it composes (invent no new colors), its layout, and its copy. Async-state mapping is centralized in [§4](#4--interaction-states-the-contract); screen sections call out only their screen-specific states.

### 2.1 — First-Run / Setup Gate

Owner: [first-run-setup.md](./first-run-setup.md). This is the make-or-break screen. Two distinct surfaces stack in sequence: the **boot splash** (supervisor stages) and the **model-download gate** (sidecar stages).

**Boot splash (supervisor-owned, pre-`/healthz`).** Shown while the Rust supervisor brings the sidecar up ([architecture.md §3.1](./architecture.md), [first-run-setup.md §5](./first-run-setup.md)). A single centered **Card** on `cloud-mist`:
- Logo + **Spinner** (`h-5 w-5 animate-spin rounded-full border-2 border-action-blue/30 border-t-action-blue`) + a stage line in `text-body-lg text-slate-blue`, mapping the supervisor state machine to plain copy:
  - `checking` → "Starting Parrot's engine…"
  - `downloading_uv` → "Fetching the Python runtime…"
  - `creating_venv` → "Setting up a local environment…"
  - `installing_deps` → "Installing the engine (this is a one-time step, a few minutes)…"
  - `starting_backend` → "Waking the engine…"
- A collapsible **"Show details"** disclosure reveals the live `bootstrap-log` tail in a `font-mono text-body` scroll panel (token-redacted per [first-run-setup.md §2](./first-run-setup.md)). Collapsed by default — friendly first, diagnostic on demand.
- `failed{message}` → the **error layout** ([§4](#4--interaction-states-the-contract)): `danger` heading "Parrot's engine couldn't start.", the supervisor message + stderr tail in the mono panel, and two **PrimaryButton**-class actions — **Retry** and a secondary **Clean & Retry** (`rounded-lg border border-platinum-tint bg-snow-white …`, the "outline" button variant). Copy names the likely cause when known (port held, deps failed).

**Model-download gate (sidecar-owned, after `/healthz`).** Once health is green the UI calls `GET /setup/status`. If `models_ready=false`, the gate Card is shown and Clone/Speak stay disabled (§1.1). Layout — one centered **Card**:
- Title `text-heading`: **"One more step — download the voice model."**
- Body `text-body-lg text-slate-blue`: "Parrot needs to download its voice engine once (a few hundred MB). After this, everything runs offline — no account, no internet." Show the target path (`hf_cache_dir`) in muted `font-mono text-body` and the free-space line.
- **Disk guard.** When `enough_disk=false` (`< min_free_gb`, [first-run-setup.md §3 Rule 5](./first-run-setup.md)), a **danger** inline notice: "Only {disk_free_gb} GB free — Parrot needs at least {min_free_gb} GB. Free some space, then try again." The Download **PrimaryButton** is disabled while disk is short.
- **PrimaryButton**: **"Download model"**. On click → `POST /setup/download` + open the SSE stream.
- **Progress** (the `downloading` state) replaces the button with the **ProgressBar** component:
  - `phase=resolving` (and the ~2s heartbeats) → the **indeterminate** ProgressBar (a chunk sliding across a `pale-gray` track, `action-blue` fill) with "Preparing download…" — never a frozen 0%.
  - `phase=progress` → determinate ProgressBar driven by `pct` (0–1), with `{filename}` and a `rate` readout in `font-mono text-body text-slate-blue`, e.g. "model-00001-of-00002.safetensors · 142 MB / 380 MB · 6.4 MB/s".
  - `phase=install_retry` → keep the bar, show a non-blocking `slate-blue` line "Network hiccup — retrying (attempt {n})…" using the redacted error.
  - `phase=install_done` → swap to **verifying** (Spinner + "Verifying the download…") while the store re-polls `/setup/status` ([first-run-setup.md §5](./first-run-setup.md): readiness is confirmed by status, not by the event).
- **Success** → gate clears, pills enable, app navigates to **Clone**. No success toast needed; arriving on the working surface *is* the success signal.
- **Error states** (map to [§4](#4--interaction-states-the-contract)):
  - `install_error` / offline → **danger** Card: "Couldn't reach Hugging Face to download the model. Check your connection, VPN, or firewall (needs `huggingface.co:443`), then retry." Retry button respects the **cooldown** — if `429`, the button is disabled with "Retry in {n}s" counting down ([first-run-setup.md §3 Rule 8](./first-run-setup.md)).
  - `needs_token` (gated repo, 401/403) → an inline **HF token Field** (same control as Settings, §2.5) appears *in the gate* with copy "This model is gated. Paste a Hugging Face token to continue." Saving via `POST /settings/hf-token` and a successful `whoami` retries the download. The default engine is ungated, so most users never see this.
- **Offline-forever.** On every later launch the gate is skipped (cached model detected); the user lands straight on Clone. The setup screen makes no network call once `models_ready=true`.

### 2.2 — Clone

Owner: [voice-cloning.md](./voice-cloning.md). Capture a reference sample → name it → save a reusable **VoiceProfile**. Default landing screen post-setup.

**Layout** — screen header ("Clone a voice" / "Record or upload a short, clean sample. 3–10 seconds is the sweet spot.") then two stacked Cards: a **Capture Card** and the **Library Card**.

**Capture Card.** A two-path capture that converges on one `captured` artifact ([voice-cloning.md §4](./voice-cloning.md)):
- A **mode toggle** (two DS Pills, "Record" / "Upload") selects the path; both end at the same preview + save form.
- **Record path** → the **Recorder** component: a large `action-blue` round mic button (≥40px), a live elapsed timer in `font-mono`, and a level meter (animated `action-blue` bar). States: idle → recording (button shows stop, pulsing ring honoring reduced-motion) → captured. On stop, the blob is held in memory and previewable.
- **Upload path** → the **Dropzone** component (DS `FileDropzone` recipe): `border-2 border-dashed`, `over` → `border-action-blue bg-pale-gray/60`, else `border-platinum-tint bg-cloud-mist hover:border-action-blue`. Copy: "Drop an audio file here, or click to choose" / hint `text-body text-slate-blue` "wav · mp3 · m4a · flac · ogg · webm — stays on your device". File selection uses the native Tauri dialog.
- **Captured preview** (shared by both paths): an inline **AudioPlayer** (§3) on the captured clip, a **"Re-record" / "Choose another"** secondary button, and a **length hint**:
  - `< ~3s` → `slate-blue` caution "That's quite short — a 3–10s clip clones more reliably." (warn, never block — [voice-cloning.md EDGE-1](./voice-cloning.md)).
  - `> ~20s` → `slate-blue` "Long clips are slower and clone less cleanly — 3–10s of clean speech is ideal." ([voice-cloning.md EDGE-2](./voice-cloning.md)).
- **Save form** (enabled only once a capture exists):
  - **Field** "Voice name" → text input (DS recipe), required, trimmed. Save is disabled until non-empty (mirrors `BR-1`).
  - **Field** "What was said? (optional)" → a small **TextComposer**/textarea for `ref_text`, with helper `text-body text-slate-blue`: "Type the exact words in your clip — it sharpens the clone. Leave blank if unsure: **better empty than wrong.**" ([voice-cloning.md BR-4 / EDGE-8](./voice-cloning.md)).
  - **LanguageSelect** "Language" → DS **Select**, default **"Auto"**.
  - `instruct` / `seed` are **not** surfaced (de-emphasized per [voice-cloning.md EDGE-12](./voice-cloning.md)); seed lives only in the Speak Advanced panel.
  - **PrimaryButton** "Save voice" → `POST /profiles` (multipart). On `saving`: Spinner + "Saving…" + disabled. On success: **success** toast "Saved '{name}'", form resets to idle, and the Library re-fetches so the new card appears immediately ([voice-cloning.md §5](./voice-cloning.md)).
- **Duplicate-name warn** ([voice-cloning.md EDGE-7](./voice-cloning.md)): if the name matches an existing profile, a non-blocking confirm before save — "You already have a voice named '{name}'. Save anyway?" — never a hard block (`BR-8`).

**Capture-specific error mapping** ([§4](#4--interaction-states-the-contract)):
- Mic permission denied (record path, frontend-only) → **danger** inline: "Parrot can't access your microphone. Allow mic access in your OS settings — or **Upload a file instead.**" The "Upload" affordance flips the mode toggle ([voice-cloning.md EDGE-6](./voice-cloning.md)).
- All-silence (`422`) → "We couldn't hear any speech in that clip — record again with the mic closer, or upload a clearer sample." ([voice-cloning.md EDGE-3](./voice-cloning.md)).
- Unsupported/corrupt format (`415`) → "We couldn't read that {ext} file. Try a wav, mp3, m4a, flac, ogg, or webm." ([voice-cloning.md EDGE-5](./voice-cloning.md)). Errors keep the captured clip so the user can retry the same payload.

**Library Card.** The voice library ([voice-profiles.md §4.1](./voice-profiles.md)) rendered as a responsive grid of **VoiceCard**s (`flex flex-wrap gap-6`, each VoiceCard = a `rounded-2xl bg-snow-white p-6 shadow-sm-2` Card, interactive → `hover:shadow-sm`):
- Each VoiceCard: voice name (`text-heading`), a **locked Badge** when `is_locked` ("Locked" pill, `glacier-blue` on `pale-gray`, paired with a lock glyph so it never relies on color alone), created-at in `text-body text-slate-blue`, a mini **AudioPlayer** for the profile's representative clip (`GET /profiles/{id}/audio`), and a **"Speak with this"** quick action that jumps to Speak with the profile pre-selected.
- Clicking the card body opens the **Voice Profile detail** (§2.4).
- **Empty state** ([voice-profiles.md EDGE "Empty library"](./voice-profiles.md)): a friendly centered block inside the Library Card — "No voices yet. Record or upload a sample above to clone your first voice." — never an error.
- **Loaded-with-stale** ([voice-profiles.md §4.1](./voice-profiles.md)): a background `refresh()` keeps the last good list visible (no flicker, no `loading` blank); a failed refresh shows a non-blocking toast and keeps the list.

### 2.3 — Speak

Owner: [synthesis.md](./synthesis.md). Type text → pick a voice → generate → play/export. The most-used screen once voices exist.

**Layout** — screen header ("Speak") then a **Compose Card**, a **Result Card** (appears on first generation), and a **History Card**.

**Compose Card** (`flex flex-col gap-6`):
- **TextComposer** — the large prompt textarea (DS textarea recipe, `min-h-[140px]`, `text-body-lg`), label "Text to speak". Placeholder "Type anything for your voice to say…". This is the focal control; Speak is disabled while it is empty/whitespace ([synthesis.md BR-1](./synthesis.md)).
- A row (`flex flex-wrap gap-3`) of primary controls:
  - **VoicePicker** — DS **Select** of saved profiles, label "Voice". Default option **"Default voice"** (no profile → model's own voice, a valid request per [synthesis.md Resolution #2](./synthesis.md)). When arrived-at from a VoiceCard's "Speak with this", the matching profile is preselected.
  - **LanguageSelect** — DS **Select**, label "Language", default **"Auto"** ("let Parrot detect").
  - **Speed** — DS **Slider** (`.parrot-range`), label "Speed: {x}×", default `1.0`.
- **Advanced** disclosure (collapsed, copy "Advanced — you probably don't need this"): exposes the [synthesis.md](./synthesis.md) advanced params — `seed` (NumberInput), `num_step`, `guidance_scale`, `effect_preset` (Select over the DSP preset table in [synthesis.md](./synthesis.md), default `broadcast`), `t_shift`, `denoise`, `postprocess_output`, `duration`. `instruct` and `ref_audio`/`ref_text` are **hidden** (driven by the clone flow / de-emphasized).
- **PrimaryButton** "Speak" (full-row on narrow widths). Disabled when text is empty.

**Generate lifecycle** (maps to the synthesis state machine in [synthesis.md](./synthesis.md) and [§4](#4--interaction-states-the-contract)):
- `submitting` → Speak button shows **Spinner** + "Sending…" + disabled.
- `waitingForModel` → a non-blocking **model-loading pill** (`Badge`-style, `pale-gray`/`glacier-blue`) "Loading the voice model — {progress}%" reading the load sub-stage from `GET /engine/status`. First-ever generation may sit here while weights load; the rest of the UI stays interactive.
- `generating` → button "Generating…" + Spinner; the page does not freeze (inference runs off the event loop, [synthesis.md BR-2](./synthesis.md)).
- `done` → the **Result Card** fills and the History list re-fetches.

**Result Card** (shown after the first generation this session):
- A full-width **AudioPlayer** (§3) on the returned WAV, auto-focusable, with **Download / Export** (native save dialog → write the WAV; default name from `X-Audio-Id`).
- A meta row in `font-mono text-body text-slate-blue`: duration (`X-Audio-Duration`), generation time (`X-Gen-Time`), and seed (`X-Seed`) when present — the data that lets a user reproduce a take.
- A **"Lock this as the voice's reference"** action when a profile was used: calls `POST /profiles/{id}/lock` with this `history_id` ([voice-profiles.md Rule 8](./voice-profiles.md)), turning a good generation into the profile's deterministic reference. Confirmation toast on success.

**History Card.** `GET /history` (newest 50) as a list of compact rows ([synthesis.md `/history`](./synthesis.md)): truncated text (≤200 chars), relative time, a row **AudioPlayer**, voice name (or "Default voice" when `profile_id` is null), and per-row **Delete** (`DELETE /history/{id}`) plus a header **"Clear all"** (`DELETE /history`, confirm first). Empty state: "Nothing spoken yet — type above and hit Speak." Each mutation re-fetches the list (no push channel).

**Speak-specific error mapping** ([§4](#4--interaction-states-the-contract)):
- **OOM / aborted (`500`)** → the **danger** Result-area message *plus* a **"Flush & retry"** PrimaryButton: "The engine ran out of memory mid-generation. Flush reloads the model — then I'll retry your text." Flush is the recovery affordance from [synthesis.md Edge Cases](./synthesis.md).
- **Unknown effect preset (`400`)** → danger inline naming the valid presets (shouldn't happen via the Select; defensive).
- **Generic inference failure (`500`)** → "Couldn't synthesize that. The full trace is in Settings → backend log." with the underlying message. Actionable, never a bare stack trace.
- **Empty text** → no request; Speak stays disabled (no error to show).

### 2.4 — Voice Profile Detail

Owner: [voice-profiles.md](./voice-profiles.md). Opened from a VoiceCard. A single **Card** (modal sheet or routed sub-page; either way `rounded-2xl bg-snow-white p-6 shadow-sm-2`) for one profile.

- **Header row**: editable name (inline-editable `text-heading`, or a "Rename" affordance opening a text input), the **locked Badge** (with lock glyph) reflecting `is_locked`, and created-at in `text-body text-slate-blue`.
- **Test it**: an **AudioPlayer** on the profile's representative audio (`GET /profiles/{id}/audio`, which prefers the locked clip), plus a **"Test in Speak"** button that opens Speak with this voice preselected and the focus in the TextComposer. ("Test it" = hear the reference, then speak custom text.)
- **Metadata edits** (`PUT /profiles/{id}`, partial patch — [voice-profiles.md Rule 4](./voice-profiles.md)): **Field**s for name, `ref_text`, language (Select). `ref_audio` is **not** editable (re-clone to replace — [voice-profiles.md Rule 6](./voice-profiles.md)); the UI states this plainly. Optimistic rename with rollback-on-`400` ([voice-profiles.md §4.2](./voice-profiles.md)); empty/whitespace rename is rejected and rolled back.
- **Lock / Unlock**:
  - When **unlocked**, locking happens from a generated take (the "Lock this" action on the Speak Result Card, §2.3) — the detail page explains this and shows the current reference as the original clone.
  - When **locked**, the badge reads "Locked" + the pinned `seed` (if any) in `font-mono`, with an **"Unlock"** button (`POST /profiles/{id}/unlock`) and copy "Unlocking reverts to your original clone." Optimistic flip, reconcile on response.
- **Usage** (`GET /profiles/{id}/usage`): "Used in {synth_total} generations" + the ≤20 most-recent as a compact list with mini AudioPlayers. Read-only.
- **Delete** (`DELETE /profiles/{id}`): a **danger** secondary button ("Delete voice"), confirm first — "Delete '{name}'? Your past generations stay in History; this can't be undone." On success: optimistic removal from the library, navigate back to Clone, success toast. History rows survive with `profile_id` nulled ([voice-profiles.md Rule 11](./voice-profiles.md)).
- **Concurrent-delete** ([voice-profiles.md EDGE](./voice-profiles.md)): a `404` on any action here ("deleted from another tab/window") drops the profile from the list and returns to Clone rather than erroring hard.

### 2.5 — Settings

Owner: [settings.md](./settings.md). Three groups, each its own **Card**, in one column. Routes are loopback-gated; appearance never touches the sidecar.

- **Appearance** Card:
  - In V1 this is informational: copy "Parrot uses a single light theme. Dark mode is on the roadmap." No theme toggle, no zoom control (both backlog per scope locks, §0). The Card still exists so the group is discoverable when dark mode lands. (Appearance prefs remain frontend-local, [settings.md §2](./settings.md); V1 simply pins `theme=light` and ships no control.)
- **Engine status** Card (read-only):
  - The **Engine status label** sources `GET /engine/status` → `{"active":"omnivoice","device":"<id>"}`. The `device` field is one of `cuda`/`cpu`. Render: a **Badge** "Engine: OmniVoice" + a device line "Running on **{device_label or device}**" mapping the device to friendly text ("NVIDIA GPU (CUDA)", "CPU — slower but works"). One fixed engine, **no picker** ([settings.md Rule 1](./settings.md)).
  - While the sidecar is still starting, a `slate-blue` "Engine starting…" placeholder with a Spinner — the rest of Settings stays interactive ([settings.md Edge Cases](./settings.md)).
  - A quiet link "View backend log" (opens the rotating `backend.log` via the native shell) — the only diagnostics affordance; there is no Logs tab ([settings.md Non-goals](./settings.md)).
- **Hugging Face token** Card (optional):
  - Copy: "Only needed to download a *gated* voice model. The default Parrot voice needs no token." — keeps it honestly optional ([settings.md Rule 2](./settings.md)).
  - **Field** "Token" → a password-style text input (DS recipe, `type=password` with a show/hide toggle), helper showing the **masked** current value `hf_…{last3}` and the cascade state:
    - `valid` → **success** badge "Signed in as {whoami_user}".
    - `invalid` → **danger** banner "Token saved but not valid — it may be expired or mistyped." (still stored; non-blocking — [settings.md Rule 7 / Edge](./settings.md)).
    - `absent` → `slate-blue` "No token — gated downloads are disabled."
    - `source: env` → read-only chip "Set via HF_TOKEN environment variable" (power-user override; field disabled, [settings.md read model](./settings.md)).
  - **PrimaryButton** "Save token" (`POST`), a secondary **"Test now"** (forces re-validate), and a **"Clear"** (`DELETE`). The raw token is never echoed back — only `masked`.
  - Sidecar-down error ([settings.md Edge](./settings.md)): "Engine not running — can't save the token yet." + retry. Appearance is unaffected (it never calls the sidecar).

---

## 3 — AudioPlayer (shared component)

Parrot plays a lot of short clips (captured reference, profile reference, generated takes, history rows), so the **AudioPlayer** is specified once and reused.

- **Anatomy**: a play/pause button (≥40px, `action-blue`), a scrub track (the DS slider track styling — `pale-gray` track, `action-blue` fill, 18px thumb), a `font-mono` time readout (`0:03 / 0:08`), and an optional **Download** icon-button (native save dialog). A "busy" variant shows a Spinner over the play button while bytes are still streaming.
- **States** ([§4](#4--interaction-states-the-contract)): idle (paused) / loading (Spinner, track disabled) / playing / ended (resets to start) / error ("Couldn't load this clip" — e.g. a profile whose reference file went missing, [voice-profiles.md EDGE "Audio file missing"](./voice-profiles.md)).
- **Accessibility**: play/pause is a `<button>` with `aria-label="Play"` / `"Pause"` that updates with state. The scrubber is a native `<input type="range">` (so it's keyboard-operable: Arrow keys seek, Home/End jump) with `aria-label="Seek"` and `aria-valuetext` announcing the time. Time updates are announced via `aria-live="off"` on the readout (avoid spamming SR on every frame; announce on play/pause/seek only). Reduced-motion disables any waveform animation.

---

## 4 — Interaction States (the contract)

Every async action in Parrot renders the **same five states** with the same DS tokens. This uniformity is what makes the app feel reliable and makes errors actionable — Parrot's core value. The mapping:

| State | What the user sees | DS components / tokens | Rules |
|---|---|---|---|
| **idle** | The control, ready. Primary actions enabled only when their precondition holds. | PrimaryButton (enabled), Field controls. | Speak disabled on empty text; Save voice disabled until name + capture exist; Download model disabled when disk short. |
| **loading** | The triggering control is **disabled** and shows a **Spinner** + present-tense label ("Saving…", "Generating…", "Downloading…"). Long indeterminate work uses the **ProgressBar** (indeterminate chunk) or, when `pct` is known, a determinate bar. | `Spinner` (`border-action-blue/30 border-t-action-blue`), `ProgressBar`, `disabled:opacity-50`. | Never a frozen 0%: `resolving` heartbeats drive the indeterminate bar ([first-run-setup.md §4](./first-run-setup.md)). The page stays interactive where the work is off-thread ([synthesis.md BR-2](./synthesis.md)). |
| **empty** | A friendly, instructive empty block — never styled as an error. | `text-body-lg text-slate-blue`, centered, inside the relevant Card. | Empty library → "clone your first voice"; empty history → "nothing spoken yet". |
| **success** | A brief confirmation: a toast and/or arriving on the working surface; success-colored where a badge is warranted. | `--color-success` (`#1a7f4b`) for badges/checks; toast. | Setup success = landing on Clone (no toast needed). Save/lock/delete = toast. Paired with text/icon, never color alone. |
| **error** | A **danger**-colored, plain-language message that says **what happened and what to do next**, plus the actionable control (Retry / Flush & retry / Upload instead / Free space). The underlying technical detail is available but secondary (mono panel or "view log"). | `--color-danger` (`#c2362f`) for the heading/border; PrimaryButton or outline button for the recovery action; `font-mono` for the raw detail. | Cooldown-aware retries show "Retry in {n}s" ([first-run-setup.md Rule 8](./first-run-setup.md)). Connection-refused is "engine starting", not a hard error ([architecture.md §4](./architecture.md)). Tokens/paths are redacted/`~`-stripped per [first-run-setup.md §2](./first-run-setup.md) and [../../CLAUDE.md](../../CLAUDE.md). |

**Per-action quick map:**

| Action | loading | empty | success | error (+recovery) |
|---|---|---|---|---|
| Boot (supervisor) | stage line + Spinner / log tail | — | splash dismisses | `failed` → Retry / Clean & Retry |
| Download model | indeterminate→determinate ProgressBar | — | gate clears → Clone | offline/`install_error` → Retry (cooldown); gated → token Field |
| Save voice | "Saving…" + Spinner | — | toast + library refresh | `415`/`422`/mic-denied → keep clip, retry / upload instead |
| List profiles | Spinner (first load only; refresh is silent) | "clone your first voice" | list renders | stale list kept + toast |
| Generate (Speak) | "Sending…/Loading model %/Generating…" | — | Result Card + history refresh | OOM → Flush & retry; `500` → view log |
| Lock / Unlock / Rename / Delete | optimistic flip + reconcile | — | toast | rollback + toast; `404` → drop & refresh |
| Save / Test / Clear token | "Saving…"/"Testing…" + Spinner | — | success badge / "signed in as {user}" | invalid → non-blocking banner; sidecar-down → retry |

---

## 5 — The Core User Flow

The path the whole app is built around — first launch to first exported clip. Everything off this path is secondary.

```
 LAUNCH ── supervisor boots ──► [Boot splash: stages + log]
                                      │ /healthz ok
                                      ▼
                              GET /setup/status
                          models_ready? ── true ──────────────┐
                                │ false                        │
                                ▼                              │
                    [Setup gate: Download model] ◄─ retry ─┐   │
                       │ POST /setup/download              │   │
                       ▼  SSE progress                     │   │
                    resolving → progress → verifying        │   │
                       │ install_error / offline ───────────┘   │
                       │ models_ready=true (verified)            │
                       ▼                                         │
                     ┌───────────────────────────────────◄──────┘
                     ▼
                 CLONE screen
            record OR upload → preview → name (+ ref_text, language)
                     │ POST /profiles
                     ▼  saved → library refresh
                 VoiceCard appears ──"Speak with this"──┐
                     │                                  │
                     ▼                                  ▼
                 SPEAK screen ◄────────────────────── (voice preselected)
            type text → pick voice → (speed/lang) → Speak
                     │ POST /generate  (waitingForModel? → generating)
                     ▼  200 + audio/wav
                 RESULT card: AudioPlayer ──► PLAY / EXPORT (.wav)
                     │ optional: "Lock as reference" (POST /profiles/{id}/lock)
                     ▼
                 HISTORY row recorded ── reuse / delete

      ── second launch onward: model cached → skip gate → straight to CLONE ──
                         (fully offline; no network calls)
```

The flow has exactly one network-dependent step (the one-time download); every step after it works offline forever ([first-run-setup.md §1](./first-run-setup.md)). A user who downloads, clones once, and speaks once has touched every core component — that round trip working cleanly *is* the v0.3.0 "actually useful" bar ([../../CLAUDE.md](../../CLAUDE.md)).

---

## 6 — Microcopy & Tone

- **Friendly, plain, action-first.** Lead with the verb and the user's goal, not the system's internals. "Download model", "Save voice", "Speak", "Flush & retry". Avoid "execute", "initialize", "invalid input".
- **Errors say what *and* what-next.** Never a bare code or stack trace in the primary surface. "Couldn't reach Hugging Face — check your connection, then retry" beats "HTTP 0 / network error". The raw detail stays available (mono panel / "view log") for bug reports.
- **Honest about the long step.** The first-run dep install and model download are minutes long; copy says so ("a one-time step, a few minutes") rather than implying instant.
- **Local-first reassurance**, surfaced lightly: the "local" header badge, "stays on your device" on the dropzone, "runs offline" on the gate. Don't over-repeat it.
- **Encourage, never nag.** `ref_text` and clip-length are *suggestions* with a clear "better empty than wrong" out; the app never blocks on them.
- **No jargon leakage** in default copy: "voice model", not "safetensors checkpoint"; "the engine", not "uvicorn sidecar". Diagnostic panels may show the real strings.

---

## 7 — Motion

- **Simple, fast, token-bound.** Hover lifts and color shifts use the DS button/card transitions (`transition`, `hover:brightness-105` on PrimaryButton; `transition-shadow hover:shadow-sm` on interactive Cards). No bespoke easings or large translate animations.
- **Two purposeful motions only:** the **Spinner** (continuous, for indeterminate short waits) and the **indeterminate ProgressBar** chunk (the sliding keyframe, for downloads/loads with no known `pct`). When `pct` is known, the determinate bar animates its width with `--dur-base`.
- **Recording pulse** on the mic button is a gentle scale/opacity loop — decorative, not informational.
- **Reduced motion (must honor).** Under `prefers-reduced-motion: reduce`: the Spinner degrades to a static "Working…" label, the indeterminate ProgressBar becomes a static striped/filled bar (no slide), the recording pulse is removed, and Card hover lifts are instant. No information is ever conveyed *only* by motion.

---

## 8 — Accessibility-in-Context

The DS guarantees the primitives (focus rings, contrast, hit targets); this section pins down per-screen behavior.

- **Focus rings everywhere.** Every interactive element carries the shared `focusRing` (`focus-visible:ring-2 focus-visible:ring-action-blue focus-visible:ring-offset-2`). Visible, `action-blue`, 2px, offset.
- **Hit targets ≥ 40px.** The mic button, play/pause, pills, and primary buttons all meet the minimum. Slider thumbs are 18px but sit in a ≥40px-tall hit row.
- **Never color alone.** The **locked Badge** pairs color with a lock glyph + "Locked" text. Success/error states pair color with an icon and a sentence. Device status is a labeled string, not a colored dot.
- **Focus order per screen:**
  - *Setup gate*: heading → (disk notice) → Download/Retry button → "Show details" disclosure → (token Field when gated).
  - *Clone*: mode toggle → capture control (mic/dropzone) → captured AudioPlayer → name → ref_text → language → Save. Library grid is reachable after the form; each VoiceCard is a single tab-stop opening detail, with its quick-action as a nested stop.
  - *Speak*: TextComposer (autofocus on screen enter) → Voice → Language → Speed → Advanced disclosure → Speak. Result Card (AudioPlayer → Download → Lock) follows; History rows last.
  - *Profile detail*: name/rename → test AudioPlayer → metadata Fields → Lock/Unlock → Delete (Delete is last and requires a confirm step so it's never the accidental default).
  - *Settings*: Appearance → Engine status → token Field → Save/Test/Clear.
- **Keyboard flows.** Enter submits the active primary action (Save voice / Speak / Save token) when its precondition holds; it does **not** trigger destructive actions. `Esc` closes the profile-detail sheet and any confirm dialog. The AudioPlayer scrubber is fully keyboard-seekable (§3). Disclosures (`Advanced`, `Show details`) are `<button aria-expanded>` toggling a region.
- **Live regions.** The download ProgressBar uses `aria-live="polite"` on a coarse status ("Downloading — 38%", updated at intervals, not every byte). The model-loading pill and synthesis state changes announce once per transition. Error banners are `role="alert"` so a screen reader hears the actionable message immediately. Token values are never placed in any live region.
- **Engine-down is not a dead end.** When the sidecar is unreachable, controls that need it are disabled with an explanatory label ("Engine starting…"), focus is not trapped, and Settings/Appearance remain operable — consistent with [architecture.md §4](./architecture.md) and [settings.md Edge Cases](./settings.md).

---

## Related specs

- [design-system.md](./design-system.md) — tokens, the `@theme` block, and the shared Svelte UI component recipes this spec composes.
- [first-run-setup.md](./first-run-setup.md) — boot ordering, the download gate, SSE progress, HF token gating (Setup screen behavior).
- [voice-cloning.md](./voice-cloning.md) — capture paths, normalization, `ref_text` guidance, capture→profile state machine (Clone screen behavior).
- [synthesis.md](./synthesis.md) — `/generate`, params, presets, history, the synthesis state machine (Speak screen behavior).
- [voice-profiles.md](./voice-profiles.md) — profile CRUD, lock/unlock, usage (Library + Profile-detail behavior).
- [settings.md](./settings.md) — HF token store, engine status, appearance ownership (Settings screen behavior).
- [architecture.md](./architecture.md) — three-process model, ports, supervisor lifecycle, frontend stores.
- [ipc-contract.md](./ipc-contract.md) — full REST request/response shapes and headers.
- [device-detection.md](./device-detection.md) — how the `device` string in the Engine status label is computed.
- [../../CLAUDE.md](../../CLAUDE.md) — project conventions, local-first and beta-cadence constraints.
- [../ROADMAP.md](../ROADMAP.md) — milestone context (dark mode and UI-zoom are backlog).
