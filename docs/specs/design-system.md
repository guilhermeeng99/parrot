# Design System

The visual and interaction foundation for Parrot's Svelte UI: design tokens, the component inventory, the screen set, the layout shell, theming, and accessibility. This spec is the contract an implementer builds against — it defines *what tokens and components exist and how they behave*, not their exact pixel values (those live in code). It is deliberately small: Parrot has two jobs (clone a voice, speak text), so the screen set and component count stay tight.

Parrot is a focused fork of OmniVoice Studio. The token architecture, the `data-theme` theming model, and the primitive-component set below are inherited from OmniVoice's `frontend/src/ui/` system and re-implemented in **Svelte (SvelteKit SPA, TypeScript strict)** — no React, no Radix, no Tailwind required. The UI never imports Python/torch; it only talks to the sidecar over the [IPC surface](./ipc-contract.md). All cross-platform, default-on behavior here must render and behave identically on macOS / Windows / Linux per the parity rule in [../../CLAUDE.md](../../CLAUDE.md).

Related specs: [architecture.md](./architecture.md), [voice-cloning.md](./voice-cloning.md), [synthesis.md](./synthesis.md), [voice-profiles.md](./voice-profiles.md), [first-run-setup.md](./first-run-setup.md), [settings.md](./settings.md), [ipc-contract.md](./ipc-contract.md).

---

## Entity Contract

The design system has no SQLite entity. Its single persisted artifact is the **appearance preferences** record. Appearance is **frontend-local only**: it is stored in the WebView's `localStorage` via a Svelte prefs store and mirrored into a store on boot. Appearance has **no sidecar IPC** and does **not** use the `settings` table (the `settings` table is reserved for the HF token and other secrets, not appearance — see [settings.md](./settings.md)).

```ts
// frontend/src/lib/stores/prefs.ts — appearance preference store (persisted via WebView localStorage)
interface UiPrefs {
  theme: 'light' | 'dark';   // active color theme; default 'dark'
  zoom:  number;             // root scale multiplier, clamped to [0.8, 1.5]; default 1.0
  reducedMotion: 'system' | 'on' | 'off'; // override OS pref; default 'system'
}
```

**Token namespaces** (CSS custom properties; the *only* styling contract components may read). Every value is a `--*` variable so the light/dark palettes can override semantics without touching component CSS.

```css
/* Semantic color (overridable per palette) */
--color-fg / --color-fg-muted / --color-fg-subtle / --color-fg-inverse
--color-bg / --color-bg-elev-1 / --color-bg-elev-2 / --color-bg-elev-3
--color-border / --color-border-strong
--color-brand / --color-brand-hover / --color-brand-glow
--color-accent / --color-success / --color-warn / --color-danger / --color-info

/* Spacing — 4px base scale */
--space-1:2px  --space-2:4px  --space-3:6px  --space-4:8px
--space-5:12px --space-6:16px --space-7:24px --space-8:32px --space-9:44px

/* Type scale (rem) */
--text-2xs … --text-2xl   /* 8 steps */
--weight-regular:400 --weight-medium:500 --weight-semibold:600 --weight-bold:700

/* Font families */
--font-sans   /* UI / body — Inter w/ system fallback */
--font-mono   /* labels, data, durations, log lines — IBM Plex Mono w/ ui-monospace fallback */
--font-serif  /* hero/display moments only */

/* Radius */
--radius-xs:2px --radius-sm:3px --radius-md:4px --radius-lg:6px --radius-xl:10px --radius-pill:999px

/* Motion */
--dur-instant:80ms --dur-fast:120ms --dur-base:200ms --dur-slow:300ms
--ease-out --ease-in-out --ease-spring

/* Elevation + z-index */
--shadow-sm/md/lg/xl  --shadow-glow  --shadow-inset
--z-base:1 --z-docked:10 --z-sticky:100 --z-overlay:1000 --z-dialog:1100 --z-toast:1200 --z-max:9999

/* Focus */
--focus-ring   /* unified keyboard focus outline */
```

**Invariants**
- Component CSS reads **only** `--*` tokens for color, spacing, radius, type, motion, and z-index. No raw hex, no magic px outside the token scale. This is what makes theming work and what keeps the bundle framework-agnostic.
- Tokens are defined once in `frontend/src/lib/styles/tokens.css`. Palette overrides live in `frontend/src/lib/styles/themes.css` keyed by `[data-theme="light"]` / `[data-theme="dark"]` on `<html>`. The design system MAY define both a full light and a full dark palette of semantic tokens; the **user-selectable** set is exactly `{light, dark}` with `dark` as the default base set.
- `--text-*` are `rem`-based; zoom is applied by setting the root font size (`html { font-size: calc(13px * var(--zoom)) }`), so one variable scales the whole app.

---

## Business Rules

1. **Tokens are the only styling API.** A component that hard-codes a color/spacing value instead of a token is a bug — it will not respond to theme or zoom changes and breaks cross-platform parity.
2. **Theme switch is instant and persisted (frontend-local).** Setting `prefs.theme` writes `data-theme` on `<html>` synchronously and persists to `localStorage`; no reload, no flash, no sidecar call. On boot the persisted theme is applied *before* first paint (inline script or root layout guard) to avoid a default-theme flash.
3. **Zoom is a clamped float in `[0.8, 1.5]`, default `1.0`**, and multiplies the root rem only. No component defines absolute font sizes outside the `--text-*` scale, so scaling is uniform. Out-of-range values are clamped to the nearest bound.
4. **Default theme is dark.** Parrot ships dark-first. The user-selectable theme set is exactly `{light, dark}`; there are no named/decorative selectable themes. A palette is "complete" only when it overrides every semantic color token; a partial palette is rejected (text could fall back to an unreadable contrast against a new background).
5. **Reduced motion is honored.** When `prefers-reduced-motion: reduce` is set (or `prefs.reducedMotion === 'on'`), all decorative animation (pulses, drifting glows, shimmer, hero sweeps) is disabled; only opacity/duration-clamped transitions for state feedback remain. This is a default-on accessibility behavior and must be identical on all three OSes.
6. **Contrast floor.** Body text (`--color-fg` on `--color-bg`) and primary interactive labels must meet WCAG AA (≥4.5:1); large text and non-text UI affordances meet ≥3:1. Both the light and dark palettes are verified against this floor.
7. **Mono for data, sans for prose.** Durations, seeds, timestamps, byte counts, file paths, and chrome labels use `--font-mono` with `font-variant-numeric: tabular-nums slashed-zero` so columns of digits align. Prose and control labels use `--font-sans`. Serif is reserved for one hero title at most.
8. **One button primitive.** Every clickable affordance is the `Button` component with a `variant`; ad-hoc `<button>` styling is not allowed. Same rule for `Input`, `Slider`, `Dialog`, `Toast`.
9. **No new heavy UI framework.** Components are plain Svelte + scoped CSS reading tokens. The modal/menu primitives implement their own focus trap and ARIA (Parrot does not ship Radix); no Tailwind class soup in component markup.
10. **Single-engine assumption is visible in the design.** Parrot ships one engine (`omnivoice`), so there is **no engine picker** component and no multi-engine UI surface. Engine status, where shown, is a read-only label sourced from `GET /engine/status` → `{"active":"omnivoice","device":"<id>"}` (device ∈ {`cuda`,`mps`,`rocm`,`cpu`}). This is the only engine/device endpoint the UI reads.

---

## Component Inventory

All components live under `frontend/src/lib/components/` (primitives in `…/ui/`). Each is a Svelte component with scoped CSS that reads design tokens. Props below are the contract; names match OmniVoice's primitive set where one exists.

### Primitives (`lib/components/ui/`)

| Component | Variants / key props | Notes |
|---|---|---|
| `Button` | `variant: primary \| subtle \| ghost \| danger \| icon \| chip`; `size: sm \| md`; `loading`, `disabled`, `active`, `leading`/`trailing` icon, `block` | The one button. `loading` shows a spinner and sets `aria-busy`. `chip` toggles set `aria-pressed`. `icon` requires an `aria-label`. |
| `Input` | `type: text \| textarea \| select`; `value`, `placeholder`, `invalid`, `disabled` | Text, multiline (resize-vertical), and native `select` (custom chevron). Focus state uses `--focus-ring`. |
| `Slider` | `min`, `max`, `step`, `value`, `label`, `valueBubble` | Used for `num_step`, `guidance_scale`, `speed`, and advanced params. Shows a tabular-nums value bubble. Keyboard: arrows step, Home/End jump. |
| `Select` / `SearchableSelect` | `options`, `value`, `searchable` | Language picker uses the searchable variant (model supports ~600 zero-shot languages, plus `Auto`). Listbox ARIA, type-ahead. |
| `Toast` | `level: info \| success \| warn \| error`; `message`, `actionLabel?` | Transient bottom/top-corner notice. Auto-dismiss except `error`. Region is `aria-live="polite"` (`assertive` for `error`). Driven by a `toasts` store. |
| `Dialog` (Modal) | `open`, `onClose`, `title`, `footer`, `size: sm \| md \| lg \| xl`, `dismissable` | Self-implemented focus trap, ESC-to-close, scroll-lock, backdrop click. `role="dialog"` + `aria-modal`, labelled by title. `dismissable=false` blocks ESC/backdrop (used by destructive confirms). |
| `Menu` | `items`, anchored/portalled | Dropdowns (e.g. profile row actions). Roving-tabindex, ESC closes, returns focus to trigger. |
| `Progress` | `value` (0–1) or `indeterminate` | Setup download + first-synthesize spinner. |
| `Badge` | `tone: neutral \| success \| warn \| danger \| brand` | Status pills (e.g. `LOCKED` on a voice card, engine status). |
| `Tooltip` | `label`, `placement` | Hover/focus hint; mirrors the element's `title`/`aria-label`. |
| `Tabs` / `Segmented` | `items`, `value` | Segmented control used for the light/dark theme toggle and small in-screen toggles. |

### Composite components

| Component | Purpose | Key behavior |
|---|---|---|
| `NavRail` | Vertical icon rail, app-level navigation | Items: **Clone**, **Speak**, **Voices**, **Settings** (footer). Each is a `RailBtn` with icon + label, `aria-label`, active state, and an accent token. (Parrot drops OmniVoice's Launchpad/Design/Dub/Stories/Gallery/Transcripts/OmniDrive rail items — out of scope.) |
| `VoiceCard` | One `voice_profiles` row as a selectable card | Shows name, language, a `LOCKED` `Badge` when `is_locked`, and a play-preview button (`GET /profiles/{id}/audio`). Used in the Speak voice picker and the Voices list. Selectable (radio semantics in the picker). |
| `AudioPlayer` | Playback of a generated/reference clip | Play/pause, scrub, current/total time (mono, tabular), download/export. Backed by native `<audio>` for default playback; Tauri native playback is an opt-in path. See [synthesis.md](./synthesis.md). |
| `Waveform` | Visual waveform for the reference clip on Clone and for generated output | Canvas/WebAudio render; playhead synced to `AudioPlayer`. Decorative animation only; controls remain keyboard-reachable. Reduced-motion disables the idle shimmer. |
| `RecordButton` / `CaptureWidget` | Mic capture for a reference sample on Clone | Record / stop, elapsed timer (mono), level meter. Surfaces permission-denied and "too long" (> max seconds) states as toasts. See [voice-cloning.md](./voice-cloning.md). |
| `FileDrop` | Drag-and-drop / click-to-upload reference audio | Dashed border, hover/drag-active accent state, accepts audio types only. |
| `Setup` / `BootstrapSplash` | First-run gate UI | Stepper + live log tail + progress bar; see Screens. |

> **Out of inventory (non-goals — do not build):** engine picker, batch queue table, dub segment table, voice gallery, glossary panel, casting view, story editor, marketplace/bundle import-export. These exist in OmniVoice and are explicitly cut from Parrot.

---

## Screen Inventory

Parrot has **five** screens. The shell renders the NavRail + a single content region; only `Setup` takes over full-window.

### 1. First-Run / Setup gate
Full-window takeover shown until the sidecar reports models ready. Drives off `GET /setup/status` → `{ models_ready: boolean, ... }` (polled) plus the `GET /setup/download-stream` Server-Sent Events progress stream (started by `POST /setup/download`). Renders a `Progress` bar, a stage stepper (e.g. *checking → creating venv → installing deps → downloading model → starting backend → ready*), and a live log tail. On failure: an actionable hint + **Retry** / **Clean & Retry**. The app does not route to Clone/Speak until `models_ready === true` and `GET /healthz` succeeds. See [first-run-setup.md](./first-run-setup.md).

### 2. Clone — *record/upload reference → save a profile*
Capture a short reference: `RecordButton`/`CaptureWidget` or `FileDrop`, rendered as a `Waveform` + `AudioPlayer` for review. Fields: profile **name** (required), **ref_text** (optional transcript), **language** (default `Auto`), optional **seed**, and a **de-emphasized** `instruct` style field (collapsed under an "Advanced" disclosure — present but not promoted, per scope). **Save** → `POST /profiles` (multipart: name, ref_audio, ref_text, instruct, language, seed). Over-length reference triggers a trim hint (toast). The capture flow (record/upload, normalization, ref_text guidance, the create→profile state machine) is owned by [voice-cloning.md](./voice-cloning.md); the `/profiles` endpoint contract is owned by [voice-profiles.md](./voice-profiles.md).

### 3. Speak — *text → voice picker → generate → player*
Primary screen. A large `Input(textarea)` for the text (required), a `VoiceCard` picker bound to `GET /profiles` (or an inline ad-hoc reference upload), a **Generate** button, and an `AudioPlayer` for the result. Generation params surface as `Slider`s, defaulting to the contract values: `num_step=16`, `guidance_scale=2.0`, `speed=1.0`, `denoise=true`, `postprocess_output=true`, `effect_preset="broadcast"`; advanced (`t_shift`, `layer_penalty_factor`, `position_temperature`, `class_temperature`) live behind a disclosure. **Generate** → `POST /generate` (multipart). The response is a `StreamingResponse audio/wav`; the player reads metadata from headers `X-Audio-Id`, `X-Gen-Time`, `X-Audio-Path`, `X-Seed`, `X-Audio-Duration`. The optional WS path (`ws://127.0.0.1:3900/ws/tts`, chunked PCM) feeds the same player for streaming synthesis. See [synthesis.md](./synthesis.md).

### 4. Voice Profile detail — *rename / test / lock / delete*
Opened from a `VoiceCard`. Shows the profile's reference (`AudioPlayer`), usage (`GET /profiles/{id}/usage` → `{"synth_recent":[…], "synth_total":int}`), and actions: **rename / edit** (`PUT /profiles/{id}` — name?, ref_text?, instruct?, language?), **test** (a quick `POST /generate` with `profile_id`), **lock** (`POST /profiles/{id}/lock` form: history_id, seed?) / **unlock** (`POST /profiles/{id}/unlock`), and **delete** (`DELETE /profiles/{id}`, behind a non-dismissable confirm `Dialog`). A locked profile shows the `LOCKED` `Badge` and, per the generate contract, resolves to its `locked_audio_path` + stored `ref_text`/`seed`. The full profile entity and CRUD + lock/unlock + usage contract is owned by [voice-profiles.md](./voice-profiles.md).

### 5. Settings
A small panel set: **Appearance** (theme as a `{light, dark}` toggle, a `zoom` `Slider` over `[0.8, 1.5]`, reduced-motion override) — all appearance prefs are frontend-local (`localStorage`), no sidecar IPC; **Engine** (read-only `{"active":"omnivoice","device":"<id>"}` label from `GET /engine/status` — no picker); **HF token** field (stored encrypted in the `settings` table; resolution order is the in-app encrypted setting first, then the `HF_TOKEN` env var as a documented power-user override — `GET/POST/DELETE /settings/hf-token`); **History** management (`GET /history`, `DELETE /history`, `DELETE /history/{id}`); and **data folder** (`parrot_data/`) location/open action. See [settings.md](./settings.md).

---

## Layout Shell

The shell is a CSS grid owned by `frontend/src/routes/+layout.svelte`:

```
[ nav rail 48px ] [ content 1fr ]
```

- The NavRail is fixed-width (48px) and always visible above a narrow breakpoint; below ~600px it collapses to an overflow menu (icons-only is the default compact state). The rail position is fixed (left); it is not a user setting.
- The content region is a single scroll container (`overflow-y:auto`, `min-height:0`) on `--color-bg`.
- OmniVoice's third "history sidebar" column and bottom logs-footer chrome are **not** part of Parrot's shell — history lives inside Settings and Speak, keeping the shell to two columns.
- The shell mounts only after `Setup` reports ready; before that, `Setup` is the whole window.

The full grid contract and breakpoints live with the shell implementation; the architectural boundary between shell and sidecar is described in [architecture.md](./architecture.md).

---

## State Machines

### Appearance/prefs store (`prefs.ts`)
```
boot → (read localStorage) → applied(theme, zoom, reducedMotion)
applied --setTheme(t)--> applied'        // writes data-theme on <html>, persists to localStorage
applied --setZoom(z)--> applied'         // clamps z to [0.8,1.5], writes --zoom on :root, persists
applied --setReducedMotion(m)--> applied' // persists override
```
Transitions are synchronous and idempotent; every transition also writes to `localStorage`. There is no sidecar IPC for appearance. A failed `localStorage` write does not block the visual change (optimistic UI).

### `Toast` store (`toasts.ts`)
```
idle --push(toast)--> visible(toast[])
visible --timeout(id)--> visible(without id)        // non-error auto-dismiss
visible --dismiss(id)--> visible(without id)
visible --(empty)--> idle
```
`error`-level toasts have no auto-dismiss timeout; they remain until dismissed or replaced.

### `Dialog` open state (per modal instance)
```
closed --open()--> open(focus-trapped, scroll-locked)
open --esc / backdrop / close-btn--> closed     // unless dismissable=false
open --confirm-action--> (caller resolves) --> closed
```
On `open`, focus moves to the first focusable element (or the dialog container); on `closed`, focus returns to the element that opened it.

### `AudioPlayer` (per instance)
```
empty --load(src)--> loading --canplay--> ready(paused)
ready(paused) --play--> playing --pause/ended--> ready(paused)
loading --error--> error(toast)
```
Full audio-player playback behavior in the synthesis flow: [synthesis.md](./synthesis.md).

---

## Edge Cases

- **Theme flash on boot.** If the persisted theme is applied after first paint, the user sees a dark→light (or light→dark) flash. Apply `data-theme` from `localStorage` before paint; treat a visible flash as a bug.
- **Incomplete palette.** A palette that omits a semantic token inherits the base value, which may fail contrast. Both palettes must override the full semantic set; a CI check (or visual review) guards this.
- **Reduced-motion vs. decorative glow.** Aurora/breath/halo/shimmer effects must be fully suppressed under reduced-motion. A residual animation here is an accessibility regression, not a polish nit.
- **Zoom clipping.** At the max zoom (1.5×) on small windows, the NavRail labels and Slider value bubbles can overflow. Components must ellipsize or wrap, never clip interactive targets or push controls off-screen. Out-of-range zoom values are clamped to `[0.8, 1.5]`.
- **Long voice-profile names.** `VoiceCard` and the picker must truncate with ellipsis and expose the full name via `title`/`aria-label`; never reflow the card grid.
- **Mic permission denied / no input device** (Clone). `RecordButton` surfaces a clear toast and falls back to `FileDrop`; it never silently no-ops.
- **Over-length reference audio** (Clone). Probe duration before upload; if over the cloning max, prompt to trim rather than sending a too-long sample.
- **Empty text on Generate** (Speak). Disable **Generate** and show an inline/toast error; do not POST.
- **No profile + no ad-hoc reference** (Speak in clone-from-scratch mode). Block generation with an error toast — matches the sidecar's required-input rule.
- **Streaming WS drop** (`ws://127.0.0.1:3900/ws/tts`). If the socket closes mid-synthesis, fall back to the buffered `POST /generate` result or surface a retry; the player must not hang in `loading`. A client disconnect is logged server-side, not surfaced as an HTTP status to the gone client.
- **Locked profile resolution.** When a `VoiceCard` is locked, the design must reflect that generation uses the locked audio + stored seed (badge + disabled reference-swap), so users aren't surprised the live reference is ignored.
- **Backend not ready / `GET /healthz` failing after setup.** `GET /healthz` returns `{"status":"ok"}` only; the shell shows a non-blocking reconnect banner when it fails. Navigation between Clone/Speak stays available but generation actions are disabled until health returns.
- **High-contrast / forced-colors OS mode.** Focus ring and borders must remain visible under `forced-colors`; do not rely solely on box-shadow glows for affordance.

---

## Data

| Source | Touched by | Notes |
|---|---|---|
| WebView `localStorage` (via `prefs.ts`) | Appearance prefs (`theme`, `zoom`, `reducedMotion`) | Frontend-local only; no sidecar IPC, not the `settings` table. Mirrored into the Svelte store on boot. |
| `settings` table (`key`/`value`/`updated_at`) | HF token (encrypted) | Persisted via the sidecar. Used for secrets only — not appearance. |
| `voice_profiles` (read) | `VoiceCard`, Speak picker, Voice detail | `id`, `name`, `ref_audio_path`, `ref_text`, `language`, `instruct`, `locked_audio_path`, `seed`, `is_locked`, `created_at`; audio via `GET /profiles/{id}/audio`. |
| `generation_history` (read/delete) | Settings → History | `id`, `text`, `language`, `profile_id`, `audio_path`, `duration_seconds`, `generation_time`, `seed`, `created_at` via `GET /history`, `DELETE /history`, `DELETE /history/{id}`. |
| `frontend/src/lib/styles/tokens.css` | every component | Single source of truth for `--*` tokens. |
| `frontend/src/lib/styles/themes.css` | theme switch | `[data-theme="light"]` / `[data-theme="dark"]` semantic overrides. |
| `frontend/src/lib/stores/{prefs,toasts}.ts` | shell + all screens | Svelte stores for UI state. |
| `parrot_data/` | Settings (location/open) | User voices, generated audio, DB, settings, and the bootstrapped venv at `parrot_data/.venv`. Survives upgrades with no manual migration. |

No design-system code reads Python/torch or any GPU API; all sidecar data crosses the [IPC surface](./ipc-contract.md) (REST + SSE at `http://127.0.0.1:3900`, streaming-synthesis WS at `ws://127.0.0.1:3900/ws/tts`). Appearance prefs never cross this boundary — they live entirely in the WebView.
