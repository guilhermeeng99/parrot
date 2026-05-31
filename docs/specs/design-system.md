# Design System

The visual and interaction foundation for Parrot's Svelte UI: the design language, the token block that is the single source of truth, the component inventory, the screen set, the layout shell, and accessibility. This spec is the contract an implementer builds against — it defines *what tokens and components exist, the exact class recipes that realize them, and how they behave*. It is deliberately small: Parrot has two jobs (clone a voice, speak text), so the screen set and component count stay tight.

Parrot's look is **"Empower"** — a dark, high-contrast fintech *command center* punctuated by a single optimistic yellow. The palette and type system are adapted from the Refero "Empower" style (empower.me); Parrot ships its **dark-dominant** read for an app shell. The UI never imports Python/torch; it only talks to the sidecar over the [IPC surface](./ipc-contract.md). Default-on behavior here must render and behave consistently on Windows per the conventions in [../../CLAUDE.md](../../CLAUDE.md).

Related specs: [ui-ux.md](./ui-ux.md) (applies this system to the screens), [architecture.md](./architecture.md), [voice-cloning.md](./voice-cloning.md), [synthesis.md](./synthesis.md), [voice-profiles.md](./voice-profiles.md), [first-run-setup.md](./first-run-setup.md), [device-detection.md](./device-detection.md), [ipc-contract.md](./ipc-contract.md), [settings.md](./settings.md).

---

## Aesthetic

**"Midnight command center, bright button."** A dominant near-black canvas (Deep Space), charcoal card surfaces, warm off-white text, and **one** vivid **Button Yellow** reserved for the primary action path. Components are lightweight and purposeful: **surface contrast + radius carry hierarchy, not heavy chrome.** Confident, direct, utility-first. **Clarity over decoration.**

The whole UI is built from one accent (`button-yellow`), a display/body type pairing (Poppins for headlines, Inter for everything else, Playfair as an optional expressive serif), an 8px grid, and a small set of rounded surfaces (24px cards, pill buttons) on a dark canvas. There are no second saturated accent colors and no heavy slate shadows.

### Locked scope (V1)

- **Dark-dominant theme.** Parrot ships the dark Empower read: Deep Space page, Charcoal cards, Cloud Whisper text, Button Yellow accent. Light surfaces (`canvas-white`, `cloud-whisper`) are defined in the token block so a future light-section rhythm (Empower's dark→off-white alternation) can be added **without** touching component markup — but V1 ships no theme toggle and no `data-theme`.
- **No UI zoom in V1.** Zoom is backlog. Type sizes come from the fixed `--text-*` scale.
- **Three type families, clear roles.** `font-display` = **Poppins** (assertive hero/section headlines, tight tracking), `font-body` = **Inter** (body, navigation, control labels — the default), `font-serif` = **Playfair Display** (optional expressive serif heading). All bundled via `@fontsource/*` (offline-first). The legacy `--font-gilroy` token is kept as an **alias to the body family** so any lingering utility still resolves.
- **8px base unit.** Tailwind v4's default spacing scale (the 4/8px grid) — **no** custom `--spacing-*` tokens.
- **Tailwind v4 is the implementation.** Tokens live in a single `@theme` block in `frontend/src/app.css`; components use generated utilities, never raw hex.

> **Backlog (out of V1):** light-section rhythm / theme toggle, UI zoom. Neither ships in V1 and neither has a Settings control; Appearance in Settings is the fixed dark theme.

---

## Entity Contract

The design system has no SQLite entity. In V1 there is **no persisted appearance record at all** — the theme is fixed dark, there is no zoom, and there is no theme toggle. Appearance therefore has **no sidecar IPC** and does **not** use the `settings` table (that table is reserved for the HF token and other secrets — see [settings.md](./settings.md)).

The single source of truth is the **`@theme` token block** in `frontend/src/app.css`. Components read only the Tailwind utilities those tokens generate (`bg-button-yellow`, `text-cloud-whisper`, `rounded-3xl`, `bg-charcoal-card`, …) — never raw hex, never a magic px outside the 8px grid.

### The exact `@theme` block — source of truth in `frontend/src/app.css`

```css
@import "tailwindcss";

@theme {
  /* Surfaces (dark-dominant) */
  --color-night-sky: #100f0f;       /* deepest chrome: header, footer, modal scrim base */
  --color-deep-space: #171616;      /* page / body background */
  --color-charcoal-card: #262525;   /* card + panel surface */
  --color-slate-fill: #322f2f;      /* subtle inset: slider/progress track, badge fill, log block */

  /* Light surfaces (available for callout sections; V1 is dark-dominant) */
  --color-canvas-white: #ffffff;    /* light section bg / pure-white surface */
  --color-cloud-whisper: #fffdf6;   /* PRIMARY TEXT on dark — and a warm off-white light surface */

  /* Accent — the ONE action color */
  --color-button-yellow: #e4e24e;   /* primary CTA, active nav/pill, focus ring, key accents — NEVER text */
  --color-muted-yellow: #faf9b6;    /* soft highlight callout / secondary accent surface */

  /* Neutrals / text */
  --color-ash-gray: #a8a59b;        /* secondary text on dark (AA on deep-space/charcoal) */
  --color-metal-gray: #64635c;      /* borders, dividers, tertiary/caption text, disabled */

  /* Status */
  --color-success: #5fd39a;         /* success feedback (reads on dark) */
  --color-danger: #ff6b5e;          /* failure feedback (warm coral, reads on dark) */

  /* Type families */
  --font-display: "Poppins", ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  --font-serif: "Playfair Display", Georgia, "Times New Roman", serif;
  --font-body: "Inter", ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  --font-gilroy: "Inter", ui-sans-serif, system-ui, sans-serif;  /* back-compat alias → body */

  /* Type scale (app-scaled Empower; display sizes use --font-display) */
  --text-caption: 11px;        --text-caption--line-height: 1.5;     /* tiny labels / overline */
  --text-body: 14px;           --text-body--line-height: 1.6;        /* small / caption / field labels */
  --text-body-lg: 16px;        --text-body-lg--line-height: 1.6;     /* body default, button/control text */
  --text-subheading: 20px;     --text-subheading--line-height: 1.4;  /* lead paragraph */
  --text-heading: 28px;        --text-heading--line-height: 1.15;    /* card titles, H3 */
  --text-heading-lg: 40px;     --text-heading-lg--line-height: 1.05; /* H2 */
  --text-display-sm: 56px;     --text-display-sm--line-height: 1;    /* hero / section title */

  /* Elevation — soft DARK shadows that only ground floating cards (not slate lifts) */
  --shadow-sm: 0 2px 8px rgba(0,0,0,0.35);    /* interactive/hover lift */
  --shadow-sm-2: 0 14px 36px rgba(0,0,0,0.45);/* resting elevated card */
}

@layer base {
  body {
    background: var(--color-deep-space);
    color: var(--color-cloud-whisper);
    font-family: var(--font-body);
    font-size: var(--text-body-lg);
    line-height: 1.6;
  }
}
```

**Invariants**

- Component markup reads **only** generated utilities for color, type, radius, and elevation. No raw hex, no magic px outside Tailwind's 4/8px scale. This keeps the visual system consistent and a future light-section rhythm drop-in.
- Tokens are defined **once** in `frontend/src/app.css`'s `@theme` block. There are no per-component token files and no second `@theme`.
- Spacing uses Tailwind's **default** scale (no custom `--spacing-*`). Stay on the 8px grid (`gap-6` = 24px, `p-6` = 24px, `py-3` = 12px, …).
- Poppins / Inter / Playfair Display are bundled via `@fontsource/*` and imported once at app entry (`+layout.svelte`). Inter is the default body family.

---

## Business Rules

1. **The `@theme` block is the only styling API.** A component that hard-codes a hex color or an off-grid px value instead of a token-backed utility is a bug — it breaks visual consistency and the future light-section override.
2. **Dark theme is fixed in V1.** There is no theme switch, no `data-theme`, no light palette shipped on the body. The light surfaces (`canvas-white`/`cloud-whisper`) exist in the token block for future callout sections only.
3. **No UI zoom in V1.** No root-scale variable, no zoom Slider. Type sizes come from the fixed `--text-*` scale.
4. **One accent.** `button-yellow` (`#e4e24e`) is reserved for the primary action path (primary CTA, active nav/pill, focus ring, key affordance icons/borders). It is **never used as a text color** and never for large decorative fills.
5. **Display vs body type.** Headlines (hero, section titles, card titles) use `font-display` (Poppins) with `tracking-tight`. Everything else — body, navigation, labels, buttons — uses `font-body` (Inter). `font-serif` (Playfair) is an optional expressive heading accent. Weights 400/500/600/700 (Poppins also 800).
6. **Never pure black or white.** Text is `cloud-whisper` (`#fffdf6`); secondary text `ash-gray` (`#a8a59b`); the canvas is `deep-space`/`night-sky`, not `#000000`.
7. **Elevation = surface contrast + radius.** Cards are a `charcoal-card` surface on the `deep-space` canvas, `rounded-3xl` (24px), with a faint `ring-white/5` top edge and a soft dark `shadow-sm-2` only to ground them. Heavy/slate shadows are never used.
8. **One button primitive.** Every clickable affordance is the `Button` component with a `variant`; ad-hoc `<button>` styling is not allowed. Same rule for the form primitives (`Field`, `Select`, `Slider`) and `Dialog`/`Toast`. All buttons/pills are `rounded-full`.
9. **Consistent recipes.** Svelte components keep the exact class recipes below. A diverging class string (different padding, radius, color utility) is a regression.
10. **Single-engine assumption is visible in the design.** Parrot ships one engine (`omnivoice`), so there is **no engine picker** component and no multi-engine surface. Engine status, where shown, is a read-only `Badge`/label sourced from `GET /engine/status` → `{"active":"omnivoice","device":"<id>"}` (device ∈ {`cuda`,`cpu`}). This is the only engine/device endpoint the UI reads.

---

## Color

Used verbatim from the `@theme` block. Every value below is a token-backed utility (`bg-*` / `text-*` / `border-*`); never the raw hex.

| Token | Hex | Primary use |
|---|---|---|
| `night-sky` | `#100f0f` | Deepest chrome: header, footer, modal scrim base |
| `deep-space` | `#171616` | Page / body background |
| `charcoal-card` | `#262525` | Card + panel surface |
| `slate-fill` | `#322f2f` | Subtle inset: slider/progress track, badge fill, log block |
| `canvas-white` | `#ffffff` | Light section bg / pure-white surface (reserved) |
| `cloud-whisper` | `#fffdf6` | **Primary text** on dark — and a warm off-white light surface |
| `button-yellow` | `#e4e24e` | **The one action color**: primary CTA, active nav/pill, focus ring, key affordances — **never text** |
| `muted-yellow` | `#faf9b6` | Soft highlight callout / secondary accent surface |
| `ash-gray` | `#a8a59b` | Secondary text on dark |
| `metal-gray` | `#64635c` | Borders, dividers, tertiary/caption text, disabled |
| `success` | `#5fd39a` | Success feedback |
| `danger` | `#ff6b5e` | Failure feedback |

## Typography

Three families with clear roles — **Poppins** (`font-display`, headlines), **Inter** (`font-body`, default), **Playfair Display** (`font-serif`, optional). The type scale is fixed (no zoom in V1).

| Token | Size | Line height | Use |
|---|---|---|---|
| `text-caption` | 11px | 1.5 | Tiny labels / overline |
| `text-body` | 14px | 1.6 | Small / caption / field labels |
| `text-body-lg` | 16px | 1.6 | Body default, button/control text |
| `text-subheading` | 20px | 1.4 | Lead paragraph |
| `text-heading` | 28px | 1.15 | Card titles, H3 |
| `text-heading-lg` | 40px | 1.05 | H2 |
| `text-display-sm` | 56px | 1.0 | Hero / section title |

Weights via Tailwind: `font-normal` 400 · `font-medium` 500 · `font-semibold` 600 · `font-bold` 700. Headlines pair `font-display font-bold tracking-tight`.

## Spacing (8px grid)

No custom spacing tokens — **Tailwind v4's default scale**. Stay on the 4/8px grid.

| Utility | px | Common use |
|---|---|---|
| `gap-2` | 8px | Tight clusters (pills in ModeTabs) |
| `py-1.5` / `px-4` | 6px / 16px | Pill padding |
| `py-3` / `px-6` | 12px / 24px | Primary button padding, container horizontal padding |
| `p-6` | 24px | Card padding |
| `gap-6` | 24px | Intra-card vertical rhythm |
| `py-16` | 64px | Dropzone vertical padding |
| section gap | ~32px | Vertical rhythm between screen sections |

Container: max-width centered (`max-w-[1000px]`), horizontal padding `px-6` (24px). Body bg `deep-space`; card surfaces `charcoal-card`.

## Radius

| Utility | px | Use |
|---|---|---|
| `rounded-md` | 4px | Small chips, fine controls |
| `rounded-xl` | 12px | **Text inputs, selects, textareas**, log blocks, toasts |
| `rounded-2xl` | 16px | Inner modules, dropzone |
| `rounded-3xl` | 24px | **Cards**, dialogs, panels |
| `rounded-full` | ∞ | **Buttons, pills**, badges, slider thumb, spinner, icon buttons |

## Elevation

Hierarchy is carried by **surface contrast + radius**, not chrome. Two soft *dark* shadows only ground floating surfaces on the canvas.

| Token | Use |
|---|---|
| `shadow-sm` | Hover / interactive lift |
| `shadow-sm-2` | Resting elevated card (default card surface) |

Cards also carry a faint `ring-1 ring-white/5` top edge. Interactive cards warm the ring toward `ring-button-yellow/40` on hover. Non-interactive elements get **no** shadow.

---

## Component Inventory

All shared primitives live under `frontend/src/lib/components/ui/` as Svelte components, keeping the exact class recipes below. A shared focus ring is applied to every interactive element.

```ts
// frontend/src/lib/components/ui/focusRing.ts — shared a11y recipe
export const focusRing =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-button-yellow focus-visible:ring-offset-2 focus-visible:ring-offset-deep-space";
```

### Button — `Button.svelte` (primary · ghost · outline; `md` / `sm` sizes)

Pill-shaped. Primary CTA recipe (verbatim):

```
rounded-full bg-button-yellow px-6 py-3 text-body-lg font-semibold text-night-sky
transition hover:brightness-105 disabled:opacity-50
```
…plus `focusRing`.

- **primary** — `bg-button-yellow text-night-sky hover:brightness-105`. Used for **Generate**, **Save**, **Download**, **Retry**.
- **ghost** — text-only secondary action: `bg-transparent text-button-yellow hover:bg-button-yellow/10`. Used for cancel / tertiary actions.
- **outline** — bordered neutral action on the dark canvas: `border border-metal-gray bg-transparent text-cloud-whisper hover:border-button-yellow`. Used for secondary recovery actions (e.g. SetupGate **Reset & retry**).
- **size** — `md` (default, ≥40px target) or `sm` (compact, header **Update to vX**). Every clickable affordance routes through this component (Business Rule 8).
- `loading` shows the `Spinner` and sets `aria-busy`; `disabled` applies `disabled:opacity-50`.

### Pill + ModeTabs — `Pill.svelte`, `ModeTabs.svelte`

Pill recipe (verbatim):

```
rounded-full px-4 py-1.5 text-body-lg font-semibold uppercase tracking-wide transition-colors
```
…plus `focusRing`, plus state:
- active → `bg-button-yellow text-night-sky`
- inactive → `bg-slate-fill text-ash-gray hover:text-button-yellow`
- disabled → `bg-slate-fill text-metal-gray`

`ModeTabs` is the mode switcher: `flex flex-wrap gap-2` of `Pill`s. Drives the **Clone / Speak / Settings** switch. `aria-pressed` reflects the active pill.

### Card — `Card.svelte`

```
flex flex-col gap-6 rounded-3xl bg-charcoal-card p-6 shadow-sm-2 ring-1 ring-white/5
```
Interactive → add `transition hover:ring-button-yellow/40 hover:shadow-sm`.

### Badge — `Badge.svelte`

```
rounded-full bg-slate-fill px-2.5 py-1 text-caption font-semibold uppercase tracking-wide text-cloud-whisper
```
Status pills: the `LOCKED` state on a `VoiceCard`, the engine/device label, `success`/`danger` outcome chips (swap text color to `text-success` / `text-danger`; keep the `slate-fill` fill). Yellow is never used as badge text.

### Field — `Field.svelte`

Label above a control (verbatim):

```
mb-2 text-body font-semibold uppercase tracking-wide text-ash-gray
```
`Field` renders the label, an optional hint (`text-ash-gray`, or `text-danger` when invalid), and an invalid state.

### Select — `Select.svelte`

```
appearance-none rounded-xl border border-metal-gray bg-charcoal-card px-3 py-2 pr-9
text-body-lg text-cloud-whisper focus-visible:border-button-yellow
```
Custom `▾` chevron (`text-ash-gray`) in the `pr-9` gutter. Native `<select>` underneath for OS-correct listbox behavior and keyboard parity.

### Text input — `TextInput.svelte`

```
w-full rounded-xl border border-metal-gray bg-charcoal-card px-3 py-1.5
text-body-lg text-cloud-whisper focus-visible:border-button-yellow focus-visible:outline-none
```
Same recipe for text / number / password; type is a prop. Invalid state swaps the border to `border-danger`. The password show/hide toggle is `text-button-yellow`.

### Slider — `Slider.svelte` (`.parrot-range`)

6px `slate-fill` track, `button-yellow` fill driven by an inline `--fill` percentage, 18px `button-yellow` thumb with a 2px `night-sky` border.

```css
.parrot-range { height: 6px; border-radius: 9999px;
  background: linear-gradient(to right,
    var(--color-button-yellow) var(--fill), var(--color-slate-fill) var(--fill)); }
.parrot-range::-webkit-slider-thumb { width: 18px; height: 18px; border-radius: 9999px;
  background: var(--color-button-yellow); border: 2px solid var(--color-night-sky); }
```
Keyboard: arrows step, Home/End jump. Used for generation params (`num_step`, `guidance_scale`, `speed`, advanced).

### Spinner — `Spinner.svelte`

```
h-5 w-5 animate-spin rounded-full border-2 border-button-yellow/30 border-t-button-yellow
```

### Dropzone — `Dropzone.svelte`

Base recipe (verbatim):

```
flex w-full cursor-pointer flex-col items-center justify-center gap-2 rounded-2xl border-2 border-dashed px-6 py-16 text-center transition-colors
```
- drag-over → `border-button-yellow bg-slate-fill/60`
- idle → `border-metal-gray bg-deep-space hover:border-button-yellow`

### ProgressBar — `ProgressBar.svelte` (determinate + indeterminate)

- **Determinate**: a `slate-fill` track (`rounded-full`) with a `button-yellow` fill set by inline width `%`.
- **Indeterminate**: a `button-yellow` chunk sliding across the `slate-fill` track via keyframes.

The indeterminate slide keyframes are disabled under `prefers-reduced-motion: reduce`.

### Dialog / Toast (self-implemented)

| Component | Recipe basis | Notes |
|---|---|---|
| `Dialog.svelte` | `Card` surface (`rounded-3xl bg-charcoal-card p-6 shadow-sm-2 ring-1 ring-white/5`) over a `bg-night-sky/70` backdrop | Self-implemented focus trap, ESC-to-close, scroll-lock, backdrop click. `role="dialog"` + `aria-modal`, labelled by title. `dismissable=false` blocks ESC/backdrop. |
| `Toast.svelte` | `rounded-xl bg-charcoal-card p-4 shadow-sm-2` surface; `success`/`danger` text tokens for level | Transient corner notice; auto-dismiss except errors. Region `aria-live="polite"` (`assertive` for errors). Driven by a `toasts` store. |

---

## Parrot-specific components

Composed strictly from the tokens and primitive recipes above. **No new colors.** All live under `frontend/src/lib/components/`.

### `VoiceCard.svelte` — a `voice_profiles` row as a selectable card

- **Anatomy:** a `Card` (`rounded-3xl bg-charcoal-card p-6 shadow-sm-2`, interactive → `hover:ring-button-yellow/40`) containing: profile **name** (`text-heading font-display font-semibold tracking-tight text-cloud-whisper`, truncated with `title`), **language** sub-label (`text-body text-ash-gray`), a `LOCKED` `Badge` when `is_locked`, and audio + action rows.
- **Selectable:** radio semantics in the Speak picker; the selected card adds a `button-yellow` ring. Tokens: `charcoal-card` surface, `cloud-whisper`/`ash-gray` text, `slate-fill` badge, `button-yellow` selection accent. Icon buttons: `rounded-full border border-metal-gray text-button-yellow hover:border-button-yellow hover:bg-button-yellow/10`.

### `AudioPlayer.svelte` — play / scrub / download a clip

- **Anatomy:** a play/pause `Button(icon)` (`bg-button-yellow text-night-sky`), a scrubber on the `.parrot-range` recipe (`slate-fill` track, `button-yellow` fill + thumb), current/total time (`font-mono text-body text-ash-gray`), and a download icon button (`text-button-yellow`). Backed by native `<audio>`. Reads result metadata from synthesis response headers. See [synthesis.md](./synthesis.md).

### `Recorder.svelte` — mic capture for the reference (Clone)

- **Anatomy:** a record/stop `Button` (record idle = `button-yellow` primary; recording state pulses the `button-yellow` accent), an elapsed timer (`text-body text-ash-gray`), and a level meter rendered from `slate-fill` (empty) → `button-yellow` (active). Surfaces permission-denied and "too long" states as `Toast`s and falls back to `Dropzone`. The recording pulse is suppressed under reduced motion.

### `TextComposer.svelte` — the large prompt textarea (Speak)

- **Anatomy:** a `Field` label (`text-ash-gray` uppercase) above a large multiline control reusing the text-input recipe at textarea scale: `rounded-xl border border-metal-gray bg-charcoal-card px-3 py-1.5 text-body-lg text-cloud-whisper focus-visible:border-button-yellow focus-visible:outline-none`, `resize-y` + generous `min-h`. A hint sits below in `text-body text-ash-gray`. Empty text disables **Generate**.

### `VoicePicker.svelte` — choose a saved profile (Speak)

- **Anatomy:** a `Select` of profiles (`GET /profiles`) or a grid of `VoiceCard`s with radio semantics. Includes an inline "use an ad-hoc reference" affordance that opens the `Dropzone`. Tokens inherited from `Select`/`VoiceCard`; no new colors.

### `LanguageSelect.svelte` — language picker

- **Anatomy:** a token-styled type-ahead input with native suggestions (`Auto`, default `Auto`, plus common languages). The field accepts any non-empty language hint so less common zero-shot languages are not blocked by the curated suggestion list. Empty blur normalizes back to `Auto`.

### `SetupGate.svelte` + `DownloadProgress.svelte` — first-run gate

- **Anatomy:** a full-window `deep-space` background centering a single `Card` (`rounded-3xl shadow-sm-2`). Inside: a title (`text-heading-lg font-display tracking-tight text-cloud-whisper`), a stage **stepper** (active step `text-button-yellow`, done step `text-success` with check, pending `text-metal-gray`), a `ProgressBar`, and a live log tail in `text-body text-ash-gray`. On failure: an actionable hint (`text-danger`) + **Retry** / **Reset & retry** as `Button`s. See [first-run-setup.md](./first-run-setup.md).

> **Out of inventory (non-goals — do not build):** engine picker, batch queue table, dub segment table, voice gallery, glossary panel, casting view, story editor, marketplace/bundle import-export, theme toggle, zoom control.

---

## Screen Inventory

Parrot has **five** screens. The shell renders a header/NavRail (the Clone / Speak / Settings switch, built from `ModeTabs`) + a single content region; only `SetupGate` takes over the full window.

### 1. First-Run / Setup gate
`SetupGate` full-window takeover shown until the sidecar reports models ready. Drives off `GET /setup/status` (polled) plus the `GET /setup/download-stream` SSE progress stream (started by `POST /setup/download`). Renders `DownloadProgress`. On failure: actionable hint + **Retry** / **Reset & retry**. The app does not route to Clone/Speak until `models_ready === true` and `GET /healthz` succeeds. See [first-run-setup.md](./first-run-setup.md).

### 2. Clone — *record/upload reference → save a profile*
Capture a short reference via `Recorder` or `Dropzone`, reviewed in an `AudioPlayer`. Fields: profile **name** (required), **ref_text** (optional, auto-filled by transcription), **language** (`LanguageSelect`, default `Auto`), and a de-emphasized `instruct` field behind an "Advanced" disclosure. **Save** → `POST /profiles`. Capture-flow: [voice-cloning.md](./voice-cloning.md); endpoint: [voice-profiles.md](./voice-profiles.md).

### 3. Speak — *text → voice picker → generate → player*
Primary screen. A `TextComposer` (required), a `VoicePicker` bound to `GET /profiles` (or ad-hoc reference upload), a primary **Generate** `Button`, and an `AudioPlayer` for the result. Generation params surface as `Slider`s defaulting to the contract values; advanced params behind a disclosure. **Generate** → `POST /generate` (multipart) returning a `StreamingResponse audio/wav`. See [synthesis.md](./synthesis.md).

### 4. Voice Profile detail — *rename / test / lock / delete*
Opened from a `VoiceCard`. Shows the reference (`AudioPlayer`), usage, and actions: rename/edit, test, lock/unlock, delete (behind a non-dismissable confirm `Dialog`). A locked profile shows the `LOCKED` `Badge`. Contract: [voice-profiles.md](./voice-profiles.md).

### 5. Settings
A small panel set of `Card`s: **Appearance** (read-only "Dark theme" indicator — no toggle, no zoom); **Engine** (read-only `{"active":"omnivoice","device":"<id>"}` from `GET /engine/status` — no picker); **HF token** field; **History** management; and **data folder** location/open action. See [settings.md](./settings.md).

---

## Layout Shell

The shell is owned by `frontend/src/routes/+layout.svelte`.

- **Header:** sticky top bar, `bg-night-sky/90` + backdrop blur, hairline `border-b border-white/10`. Contains the logo (`font-display font-bold tracking-tight text-cloud-whisper`) + a "local" `Badge`, the `ModeTabs` (Clone / Speak / Settings), and a version / "Update to vX" `Button` on the right.
- **Container:** content is centered at `max-w-[1000px]` with `px-6` (24px) horizontal padding, on `bg-deep-space`; cards are `charcoal-card`.
- **Vertical rhythm:** ~32px section gap; comfortable breathing room (`gap-6`/`p-6` inside cards).
- The shell mounts only after `SetupGate` reports ready; before that, `SetupGate` is the whole window.

The architectural boundary between shell and sidecar is described in [architecture.md](./architecture.md).

---

## State Machines

### `Toast` store (`toasts.ts`)
```
idle --push(toast)--> visible(toast[])
visible --timeout(id)--> visible(without id)   // non-error auto-dismiss
visible --dismiss(id)--> visible(without id)
visible --(empty)--> idle
```
`error`-level toasts have no auto-dismiss; they remain until dismissed or replaced.

### `Dialog` open state (per modal instance)
```
closed --open()--> open(focus-trapped, scroll-locked)
open --esc / backdrop / close-btn--> closed     // unless dismissable=false
open --confirm-action--> (caller resolves) --> closed
```
On `open`, focus moves to the first focusable element; on `closed`, focus returns to the opener.

### `AudioPlayer` (per instance)
```
empty --load(src)--> loading --canplay--> ready(paused)
ready(paused) --play--> playing --pause/ended--> ready(paused)
loading --error--> error(toast)
```

---

## Edge Cases

- **Long voice-profile names.** `VoiceCard`/`VoicePicker` truncate with ellipsis and expose the full name via `title`/`aria-label`.
- **Mic permission denied / no input device** (Clone). `Recorder` surfaces a clear `Toast` and falls back to `Dropzone`.
- **Over-length reference audio** (Clone). Probe duration before upload; prompt to trim (`Toast`, `danger` hint).
- **Empty text on Generate** (Speak). Disable **Generate** and show an inline/toast error; do not POST.
- **Streaming WS drop.** Fall back to the buffered `POST /generate` result or surface a retry; the player must not hang in `loading`.
- **Locked profile resolution.** A locked `VoiceCard` shows the `LOCKED` `Badge` and disables reference-swap.
- **Backend not ready / `GET /healthz` failing after setup.** Show a non-blocking reconnect banner; generation actions disabled until health returns.
- **Reduced motion.** The `Spinner`, `ProgressBar` indeterminate slide, and `Recorder` pulse honor `prefers-reduced-motion: reduce`.
- **High-contrast / forced-colors OS mode.** The `button-yellow` focus ring and field borders must remain visible under `forced-colors`; do not rely solely on shadow for affordance.

---

## Do / Don't

**DO**
- Use Inter (`font-body`) for body/labels/buttons and Poppins (`font-display`) with `tracking-tight` for headlines.
- Reserve `button-yellow` for the primary action path (CTA, active nav/pill, focus ring, key affordances) — as a fill/border/icon, never text.
- Use `rounded-3xl` `charcoal-card` cards on the `deep-space` canvas; lean on surface contrast + the faint `ring-white/5` edge, not heavy chrome.
- Use `cloud-whisper` text + `ash-gray` secondary text; `slate-fill` for inset tracks/fills and badge chips.
- Keep Svelte component class strings identical to the recipes above.

**DON'T**
- No `button-yellow` as a text color; no second saturated accent.
- No heavy/slate shadows; no shadows on non-interactive elements.
- No extra font families beyond display/body/serif.
- Don't break the 8px grid (no off-scale px, no custom `--spacing-*`).
- Never pure `#000`/`#fff` for text — use `cloud-whisper` / `night-sky`.
- No raw hex in component markup — only token-backed utilities.

---

## Accessibility

- **Contrast.** Body copy is `cloud-whisper` on the dark `deep-space`/`charcoal-card` canvas (high contrast). `ash-gray` is acceptable for secondary text at ≥16px. `metal-gray` is **disabled/border-only**, never primary content.
- **Focus.** Every interactive element carries the shared `focusRing` — a 2px `button-yellow` ring with a `deep-space` offset, visible on `focus-visible`. The ring must survive `forced-colors` mode.
- **Hit targets** are ≥40px (the `px-6 py-3` button recipe and pill padding satisfy this).
- **Never rely on color alone.** Pair color with text/icon — `LOCKED` badge has a label, success/error toasts have a message and icon, the setup stepper marks done steps with a check.
- **Reduced motion** is honored for all decorative animation.

> **Backlog (out of V1):** light-section rhythm / theme toggle and UI zoom. The token block already defines the light surfaces (`canvas-white`/`cloud-whisper`) so a later light-section milestone needs no component changes; neither has a Settings control in V1.
