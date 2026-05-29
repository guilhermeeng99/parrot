# Design System

The visual and interaction foundation for Parrot's Svelte UI: the design language, the token block that is the single source of truth, the component inventory, the screen set, the layout shell, and accessibility. This spec is the contract an implementer builds against — it defines *what tokens and components exist, the exact class recipes that realize them, and how they behave*. It is deliberately small: Parrot has two jobs (clone a voice, speak text), so the screen set and component count stay tight.

Parrot adopts the **Toolzy design system** wholesale. Toolzy's tokens are used **verbatim**; Toolzy's React `components/ui.tsx` recipes are **re-implemented in Svelte** (SvelteKit SPA, TypeScript strict) with the **same Tailwind v4 class strings**. No React, no Radix. The UI never imports Python/torch; it only talks to the sidecar over the [IPC surface](./ipc-contract.md). Default-on behavior here must render and behave consistently on Windows per the conventions in [../../CLAUDE.md](../../CLAUDE.md).

Related specs: [ui-ux.md](./ui-ux.md) (applies this system to the screens), [architecture.md](./architecture.md), [voice-cloning.md](./voice-cloning.md), [synthesis.md](./synthesis.md), [voice-profiles.md](./voice-profiles.md), [first-run-setup.md](./first-run-setup.md), [device-detection.md](./device-detection.md), [ipc-contract.md](./ipc-contract.md), [settings.md](./settings.md).

---

## Aesthetic

Calendly **"Sky Blueprint on Bright Paper"**: a bright **light** theme, deep indigo text, a single confident **action-blue** for interaction, soft slate-tinted shadows, generous spacing, consistent rounded corners. Professional but friendly. **Clarity over decoration.**

The whole UI is built from one accent (`action-blue`), one type family (Montserrat), an 8px grid, and a small set of rounded surfaces lifted with diffuse slate shadows. There are no saturated decorative colors, no second font, and no heavy chrome.

### Locked scope (V1)

- **Light theme only.** Dark mode is backlog. Tokens are structured in a single `@theme` block so a `.dark` override can be added later, but V1 ships **no theme toggle, no `data-theme`, no dark palette**.
- **No UI zoom in V1.** Zoom is backlog. The earlier OmniVoice "theme: light|dark default dark + zoom" appearance model is **superseded** by this light-only system — do not carry it over.
- **One type family: Montserrat** (SIL OFL), bundled via `@fontsource/montserrat`. The token name is kept as `--font-gilroy` so the vocabulary matches the Toolzy reference; weights 400 / 500 / 600 / 700.
- **8px base unit.** Tailwind v4's default spacing scale (the 4/8px grid) — **no** custom `--spacing-*` tokens. (`p-24` = 96px, etc.)
- **Tailwind v4 is the implementation.** Tokens live in a single `@theme` block in `frontend/src/app.css`; components use generated utilities, never raw hex.

> **Backlog (out of V1):** dark theme (`.dark` override), UI zoom. Neither ships in V1 and neither has a Settings control in V1; Appearance in Settings is light-only.

---

## Entity Contract

The design system has no SQLite entity. In V1 there is **no persisted appearance record at all** — the theme is fixed light, there is no zoom, and there is no theme toggle. Appearance therefore has **no sidecar IPC** and does **not** use the `settings` table (that table is reserved for the HF token and other secrets — see [settings.md](./settings.md)).

The single source of truth is the **`@theme` token block** in `frontend/src/app.css`. Components read only the Tailwind utilities those tokens generate (`bg-action-blue`, `text-midnight-indigo`, `rounded-2xl`, `shadow-sm-2`, …) — never raw hex, never a magic px outside the 8px grid.

### The exact `@theme` block — copy verbatim into `frontend/src/app.css`

```css
@import "tailwindcss";

@theme {
  --color-midnight-indigo: #0b3558;  /* primary text, headings, inactive nav — branded almost-black */
  --color-action-blue: #006bff;      /* the ONE action color: primary CTA, active nav, key accents */
  --color-glacier-blue: #004eba;     /* informational badge text / alerts */
  --color-snow-white: #ffffff;       /* page bg, card surfaces */
  --color-cloud-mist: #f8f9fb;       /* off-white section bg (body bg) */
  --color-pale-gray: #e7edf6;        /* badge fills, soft separation, slider track */
  --color-slate-blue: #476788;       /* secondary text, supporting info, icon fills */
  --color-steel-gray: #a6bbd1;       /* tertiary text, disabled, fine borders */
  --color-platinum-tint: #d4e0ed;    /* inactive field borders, subtle dividers */
  --color-outline-gray: #e6e6e6;     /* separators, hairline borders */
  --color-text-black: #0a0a0a;       /* body text / default links — never pure #000 */
  --color-success: #1a7f4b;          /* success feedback */
  --color-danger: #c2362f;           /* failure feedback */

  --font-gilroy: "Montserrat", ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;

  --text-body: 14px;            --text-body--line-height: 1.71;       /* small/caption */
  --text-body-lg: 16px;         --text-body-lg--line-height: 1.6;     /* body default */
  --text-subheading: 18px;      --text-subheading--line-height: 1.6;  /* lead paragraph */
  --text-heading: 24px;         --text-heading--line-height: 1.4;     /* card titles, H3 */
  --text-heading-lg: 28px;      --text-heading-lg--line-height: 1.2;  /* H2 */
  --text-display-sm: 38px;      --text-display-sm--line-height: 1.21; /* section title (app only ships through this) */

  --shadow-sm: rgba(71,103,136,0.04) 0px 4px 5px 0px, rgba(71,103,136,0.03) 0px 4px 10px 0px, rgba(71,103,136,0.05) 0px 10px 20px 0px;       /* hover / interactive lift */
  --shadow-sm-2: rgba(71,103,136,0.04) 0px 4px 5px 0px, rgba(71,103,136,0.03) 0px 8px 15px 0px, rgba(71,103,136,0.08) 0px 30px 50px 0px;     /* resting / elevated featured card */
}

@layer base {
  body {
    background: var(--color-cloud-mist);
    color: var(--color-midnight-indigo);
    font-family: var(--font-gilroy);
    font-size: var(--text-body-lg);
    line-height: 1.6;
  }
}
```

**Invariants**

- Component markup reads **only** generated utilities for color, type, radius, and elevation. No raw hex, no magic px outside Tailwind's 4/8px scale. This is what keeps the visual system consistent and a future `.dark` override drop-in.
- Tokens are defined **once** in `frontend/src/app.css`'s `@theme` block. There are no per-component token files and no second `@theme`.
- Spacing uses Tailwind's **default** scale (no custom `--spacing-*`). Stay on the 8px grid (`gap-6` = 24px, `p-6` = 24px, `py-3` = 12px, …).
- Montserrat is bundled via `@fontsource/montserrat` (weights 400/500/600/700) and imported once at app entry; it is the only font family.

---

## Business Rules

1. **The `@theme` block is the only styling API.** A component that hard-codes a hex color or an off-grid px value instead of a token-backed utility is a bug — it breaks visual consistency and the future `.dark` override, and risks visual inconsistency.
2. **Light theme is fixed in V1.** There is no theme switch, no `data-theme`, no dark palette shipped. The `@theme` block is authored so a `.dark { … }` override can be added in a later milestone without touching component markup.
3. **No UI zoom in V1.** No root-scale variable, no zoom Slider. Type sizes come from the fixed `--text-*` scale.
4. **One accent.** `action-blue` (`#006bff`) is reserved for the primary action path (primary CTA, active nav/pill, focus ring, key accents). It is never used for large blocks of body text or decorative fills.
5. **One type family.** Montserrat (`font-gilroy`) for all text. No second font family. Weights are limited to 400/500/600/700.
6. **Never pure black.** Body text and headings use `midnight-indigo` (`#0b3558`); default body/link black is `text-black` (`#0a0a0a`). `#000000` is never used.
7. **Elevation = rounded surface + diffuse slate shadow.** Cards are `rounded-2xl` (16px) with `shadow-sm-2` at rest. `shadow-sm` is the lighter interactive/hover lift. Heavy shadows are never applied to non-interactive elements.
8. **One button primitive.** Every clickable affordance is the `Button` component with a `variant`; ad-hoc `<button>` styling is not allowed. Same rule for the form primitives (`Field`, `Select`, `Slider`) and `Dialog`/`Toast`.
9. **Identical class strings to Toolzy.** Svelte re-implementations keep the exact Tailwind class recipes below. A diverging class string (different padding, radius, color utility) is a regression against the reference.
10. **Single-engine assumption is visible in the design.** Parrot ships one engine (`omnivoice`), so there is **no engine picker** component and no multi-engine surface. Engine status, where shown, is a read-only `Badge`/label sourced from `GET /engine/status` → `{"active":"omnivoice","device":"<id>"}` (device ∈ {`cuda`,`cpu`}). This is the only engine/device endpoint the UI reads.

---

## Color

Used verbatim from the Toolzy `@theme` block. Every value below is a token-backed utility (`bg-*` / `text-*` / `border-*`); never the raw hex.

| Token | Hex | Primary use |
|---|---|---|
| `midnight-indigo` | `#0b3558` | Primary text, headings, inactive nav — branded almost-black |
| `action-blue` | `#006bff` | **The one action color**: primary CTA, active nav/pill, focus ring, key accents |
| `glacier-blue` | `#004eba` | Informational badge text / alerts |
| `snow-white` | `#ffffff` | Page bg, card surfaces |
| `cloud-mist` | `#f8f9fb` | Off-white section bg (body bg) |
| `pale-gray` | `#e7edf6` | Badge fills, soft separation, slider track |
| `slate-blue` | `#476788` | Secondary text, supporting info, icon fills |
| `steel-gray` | `#a6bbd1` | Tertiary text, disabled, fine borders |
| `platinum-tint` | `#d4e0ed` | Inactive field borders, subtle dividers |
| `outline-gray` | `#e6e6e6` | Separators, hairline borders |
| `text-black` | `#0a0a0a` | Body text / default links — never pure `#000` |
| `success` | `#1a7f4b` | Success feedback |
| `danger` | `#c2362f` | Failure feedback |

## Typography

One family — **Montserrat** (`--font-gilroy`), weights 400/500/600/700. The type scale is fixed (no zoom in V1).

| Token | Size | Line height | Use |
|---|---|---|---|
| `text-body` | 14px | 1.71 | Small / caption / field labels |
| `text-body-lg` | 16px | 1.6 | Body default, button/control text |
| `text-subheading` | 18px | 1.6 | Lead paragraph |
| `text-heading` | 24px | 1.4 | Card titles, H3 |
| `text-heading-lg` | 28px | 1.2 | H2 |
| `text-display-sm` | 38px | 1.21 | Section title (the app ships through this size at most) |

Weights via Tailwind: `font-normal` 400 · `font-medium` 500 · `font-semibold` 600 · `font-bold` 700.

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
| section gap | ~40px | Vertical rhythm between screen sections |

Container: max-width centered (`max-w-[1000px]`), horizontal padding `px-6` (24px). Body bg `cloud-mist`; card surfaces `snow-white`.

## Radius

| Utility | px | Use |
|---|---|---|
| `rounded-md` | 4px | Small chips, fine controls |
| `rounded-lg` | 8px | **Buttons**, pills, selects, text inputs |
| `rounded-xl` | 12px | Medium containers |
| `rounded-2xl` | 16px | **Cards**, dropzone |
| `rounded-3xl` | 24px | Large surfaces |
| `rounded-full` | 50px | Badges / pills, slider thumb, spinner |

## Elevation

Two shadows only; both are diffuse and slate-tinted.

| Token | Use |
|---|---|
| `shadow-sm` | Hover / interactive lift |
| `shadow-sm-2` | Resting / elevated featured card (default card surface) |

Interactive cards rest at `shadow-sm-2` and may use `transition-shadow hover:shadow-sm` on hover. Non-interactive elements get **no** shadow.

---

## Component Inventory

All shared primitives live under `frontend/src/lib/components/ui/` as Svelte components. Each keeps **the exact Tailwind class string** from Toolzy's `components/ui.tsx`; only the language changes (React → Svelte). A shared focus ring is applied to every interactive element.

```ts
// frontend/src/lib/components/ui/focusRing.ts — shared a11y recipe (Toolzy verbatim)
export const focusRing =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-action-blue focus-visible:ring-offset-2";
```

### Button — `Button.svelte` (primary · ghost · outline; `md` / `sm` sizes)

Primary CTA recipe (verbatim):

```
rounded-lg bg-action-blue px-6 py-3 text-body-lg font-semibold text-snow-white
transition hover:brightness-105 disabled:opacity-50
```
…plus `focusRing`.

```svelte
<!-- Button.svelte -->
<script lang="ts">
  import { focusRing } from "./focusRing";
  export let variant: "primary" | "ghost" | "outline" = "primary";
  export let size: "md" | "sm" = "md";
  export let disabled = false;
  const sizes = {
    md: "px-6 py-3 text-body-lg", // default; satisfies ≥40px hit target
    sm: "px-3 py-1.5 text-body",  // compact header button (e.g. "Update to vX")
  } as const;
  const base = `rounded-lg font-semibold transition disabled:opacity-50 ${focusRing}`;
  const variants = {
    primary: "bg-action-blue text-snow-white hover:brightness-105",
    // ghost: text-only action, no fill
    ghost: "bg-transparent text-action-blue hover:bg-pale-gray",
    // outline: bordered neutral action on a light surface (e.g. "Reset & retry")
    outline: "border border-platinum-tint bg-snow-white text-midnight-indigo hover:border-action-blue",
  } as const;
</script>

<button class="{base} {sizes[size]} {variants[variant]}" {disabled} on:click>
  <slot />
</button>
```

- **primary** — the one CTA recipe above. Used for **Generate**, **Save**, **Download**, **Retry**.
- **ghost** — text-only secondary action (`text-action-blue`, no fill, `hover:bg-pale-gray`). Used for cancel / tertiary actions.
- **outline** — bordered neutral action on a light surface (`border-platinum-tint bg-snow-white text-midnight-indigo`, `hover:border-action-blue`). Used for secondary recovery actions such as the SetupGate **Reset & retry**.
- **size** — `md` (default, ≥40px target) or `sm` (compact, for the header **Update to vX** button). Every clickable affordance routes through this component with a `variant`/`size` — never an ad-hoc `<button>` (Business Rule 8).
- `loading` shows the `Spinner` and sets `aria-busy`; `disabled` applies `disabled:opacity-50`. Hit target ≥40px for `md` (the `px-6 py-3` recipe satisfies this).

### Pill + ModeTabs — `Pill.svelte`, `ModeTabs.svelte`

Pill recipe (verbatim):

```
rounded-lg px-4 py-1.5 text-body-lg font-semibold uppercase transition-colors
```
…plus `focusRing`, plus state:
- active → `bg-action-blue text-snow-white`
- inactive → `bg-pale-gray text-midnight-indigo hover:bg-platinum-tint`

`ModeTabs` is the mode switcher: `flex flex-wrap justify-center gap-2` of `Pill`s. In Parrot this drives the **Clone / Speak / Settings** switch (the header/NavRail surface). `aria-pressed` reflects the active pill.

### Card — `Card.svelte`

```
flex flex-col gap-6 rounded-2xl bg-snow-white p-6 shadow-sm-2
```
No border by default. If interactive, add `transition-shadow hover:shadow-sm`.

### Badge — `Badge.svelte`

```
rounded-full bg-pale-gray px-2 py-1 text-body font-semibold text-glacier-blue
```
Status pills: the `LOCKED` state on a `VoiceCard`, the engine/device label, `success`/`danger` outcome chips (swap text color to `text-success` / `text-danger`; keep the `pale-gray` fill).

### Field — `Field.svelte`

A label above a control. Label recipe (verbatim):

```
mb-2 text-body font-semibold uppercase tracking-wide text-slate-blue
```
`Field` wraps a labeled control (text input / `Select` / `Slider`) and renders the label, an optional hint, and an invalid state.

### Select — `Select.svelte`

```
appearance-none rounded-lg border border-platinum-tint bg-snow-white px-3 py-2 pr-9
text-body-lg text-midnight-indigo focus-visible:border-action-blue
```
Custom `▾` chevron positioned in the `pr-9` gutter. Native `<select>` underneath for OS-correct listbox behavior and keyboard parity.

### Text input — `TextInput.svelte` (Number / Password pattern)

```
rounded-lg border border-platinum-tint bg-snow-white px-3 py-1.5
text-body-lg text-midnight-indigo focus-visible:border-action-blue focus-visible:outline-none
```
Same recipe for text / number / password; type is a prop. Invalid state swaps the border to `border-danger`.

### Slider — `Slider.svelte` (`.parrot-range`)

6px `pale-gray` track, `action-blue` fill driven by an inline `--fill` percentage, 18px `action-blue` thumb with a 2px `snow-white` border. (Toolzy calls this `.toolzy-range`; Parrot renames it `.parrot-range`.)

```svelte
<!-- Slider.svelte -->
<script lang="ts">
  export let min = 0, max = 100, step = 1, value = 0;
  $: fill = ((value - min) / (max - min)) * 100;
</script>
<input class="parrot-range" type="range" {min} {max} {step}
       bind:value style="--fill:{fill}%" on:input />
```
```css
/* in app.css — track/fill/thumb realized from tokens, no new colors */
.parrot-range { height: 6px; border-radius: 9999px;
  background: linear-gradient(to right,
    var(--color-action-blue) var(--fill), var(--color-pale-gray) var(--fill)); }
.parrot-range::-webkit-slider-thumb { width: 18px; height: 18px; border-radius: 9999px;
  background: var(--color-action-blue); border: 2px solid var(--color-snow-white); }
```
Keyboard: arrows step, Home/End jump. Used for generation params (`num_step`, `guidance_scale`, `speed`, advanced).

### Spinner — `Spinner.svelte`

```
h-5 w-5 animate-spin rounded-full border-2 border-action-blue/30 border-t-action-blue
```

### Dropzone — `Dropzone.svelte`

Base recipe (verbatim):

```
flex w-full cursor-pointer flex-col items-center justify-center gap-2 rounded-2xl border-2 border-dashed px-6 py-16 text-center transition-colors
```
- drag-over → `border-action-blue bg-pale-gray/60`
- idle → `border-platinum-tint bg-cloud-mist hover:border-action-blue`

```svelte
<!-- Dropzone.svelte -->
<script lang="ts">
  export let over = false;
  const base = "flex w-full cursor-pointer flex-col items-center justify-center gap-2 rounded-2xl border-2 border-dashed px-6 py-16 text-center transition-colors";
  $: state = over ? "border-action-blue bg-pale-gray/60"
                  : "border-platinum-tint bg-cloud-mist hover:border-action-blue";
</script>
<label class="{base} {state}"
       on:dragover|preventDefault={() => over = true}
       on:dragleave={() => over = false}
       on:drop|preventDefault><slot /></label>
```

### ProgressBar — `ProgressBar.svelte` (determinate + indeterminate)

- **Determinate**: a `pale-gray` track (`rounded-full`) with an `action-blue` fill set by inline width `%`.
- **Indeterminate**: an `action-blue` chunk sliding across the `pale-gray` track via keyframes — used when no granular `%` is available.

```svelte
<!-- ProgressBar.svelte -->
<script lang="ts">
  export let value: number | null = null; // 0..1, or null = indeterminate
</script>
<div class="h-2 w-full overflow-hidden rounded-full bg-pale-gray"
     role="progressbar" aria-valuemin={0} aria-valuemax={100}
     aria-valuenow={value === null ? undefined : Math.round(value * 100)}>
  {#if value === null}
    <div class="parrot-indeterminate h-full w-1/3 rounded-full bg-action-blue"></div>
  {:else}
    <div class="h-full rounded-full bg-action-blue transition-[width]"
         style="width:{value * 100}%"></div>
  {/if}
</div>
```
The indeterminate slide keyframes are disabled under `prefers-reduced-motion: reduce`.

### Dialog / Toast (self-implemented)

| Component | Recipe basis | Notes |
|---|---|---|
| `Dialog.svelte` | `Card` surface (`rounded-2xl bg-snow-white p-6 shadow-sm-2`) over a dimmed backdrop | Self-implemented focus trap, ESC-to-close, scroll-lock, backdrop click. `role="dialog"` + `aria-modal`, labelled by title. `dismissable=false` blocks ESC/backdrop (destructive confirms, e.g. profile delete). |
| `Toast.svelte` | `rounded-lg` surface; `success`/`danger` text tokens for level | Transient corner notice; auto-dismiss except errors. Region `aria-live="polite"` (`assertive` for errors). Driven by a `toasts` store. |

---

## Parrot-specific components

Composed strictly from the tokens and primitive recipes above. **No new colors.** All live under `frontend/src/lib/components/`.

### `VoiceCard.svelte` — a `voice_profiles` row as a selectable card

- **Anatomy:** a `Card` (`rounded-2xl bg-snow-white p-6 shadow-sm-2`, interactive → `hover:shadow-sm`) containing: profile **name** (`text-heading font-semibold text-midnight-indigo`, truncated with `title`), **language** sub-label (`text-body text-slate-blue`), a `LOCKED` `Badge` when `is_locked`, and a play-preview `Button(variant="ghost", icon)` wired to `GET /profiles/{id}/audio`.
- **Selectable:** radio semantics in the Speak picker; the selected card adds an `action-blue` outline via `focusRing`/`ring-action-blue`. Tokens: `snow-white` surface, `midnight-indigo`/`slate-blue` text, `pale-gray`/`glacier-blue` badge, `action-blue` selection accent.

### `AudioPlayer.svelte` — play / scrub / download a clip

- **Anatomy:** a play/pause `Button(icon)` (`action-blue`), a scrubber built on the `Slider`/`.parrot-range` recipe (`pale-gray` track, `action-blue` fill + thumb), current/total time (`text-body text-slate-blue`), and a download `Button(variant="ghost")`. Backed by native `<audio>` for default playback. Reads result metadata from synthesis response headers (`X-Audio-Id`, `X-Gen-Time`, `X-Audio-Duration`, `X-Seed`, `X-Audio-Path`). See [synthesis.md](./synthesis.md).
- Tokens: `action-blue` (controls/fill), `pale-gray` (track), `slate-blue` (timecodes), `snow-white` surface.

### `Recorder.svelte` — mic capture for the reference (Clone)

- **Anatomy:** a record/stop `Button` (record idle = `action-blue` primary; recording state pulses the `action-blue` accent), an elapsed timer (`text-body text-slate-blue`), and a level meter rendered from `pale-gray` (empty) → `action-blue` (active). Surfaces permission-denied and "too long" (> max seconds) states as `Toast`s and falls back to `Dropzone`. See [voice-cloning.md](./voice-cloning.md).
- Tokens: `action-blue`, `pale-gray`, `slate-blue`, `danger` (over-length warning text). No new colors; the recording pulse is suppressed under reduced motion.

### `TextComposer.svelte` — the large prompt textarea (Speak)

- **Anatomy:** a `Field` label (`text-slate-blue` uppercase) above a large multiline control reusing the text-input recipe at textarea scale: `rounded-lg border border-platinum-tint bg-snow-white px-3 py-1.5 text-body-lg text-midnight-indigo focus-visible:border-action-blue focus-visible:outline-none`, with `resize-y` and a generous `min-h`. A character/empty-state hint sits below in `text-body text-slate-blue`.
- Empty text disables **Generate** (`disabled:opacity-50`) and shows an inline error.

### `VoicePicker.svelte` — choose a saved profile (Speak)

- **Anatomy:** in V1 a `Select` of profiles (`GET /profiles`) using the `Select` recipe (custom chevron, `platinum-tint` border, `focus-visible:border-action-blue`), or a grid of `VoiceCard`s with radio semantics for richer display. Includes an inline "use an ad-hoc reference" affordance that opens the `Dropzone`.
- Tokens inherited from `Select`/`VoiceCard`; no new colors.

### `LanguageSelect.svelte` — language picker

- **Anatomy:** the `Select` recipe with a searchable type-ahead overlay (the model supports ~600 zero-shot languages plus `Auto`, default `Auto`). The dropdown surface is a `Card`-style `snow-white` panel with `outline-gray` hairline separators; the highlighted option uses `bg-pale-gray text-midnight-indigo`, the selected option `text-action-blue`.
- Tokens: `platinum-tint` border, `action-blue` focus/selection, `pale-gray` hover, `slate-blue` secondary text.

### `SetupGate.svelte` + `DownloadProgress.svelte` — first-run gate

- **Anatomy:** a full-window `cloud-mist` background centering a single `Card` (`rounded-2xl shadow-sm-2`). Inside: a title (`text-heading-lg text-midnight-indigo`), a stage **stepper** (active step `text-action-blue`, done step `text-success` with check, pending `text-steel-gray`), a `ProgressBar` (`%` when the stream reports bytes, **indeterminate** otherwise), and a live log tail in `text-body text-slate-blue`. On failure: an actionable hint (`text-danger`) + **Retry** / **Reset & retry** as `Button`s (primary + ghost). Drives off `GET /setup/status`, the `GET /setup/download-stream` SSE, and `POST /setup/download`. See [first-run-setup.md](./first-run-setup.md).
- Tokens: `cloud-mist`/`snow-white` surfaces, `action-blue` progress + active step, `success`/`danger`/`steel-gray` status, `slate-blue` log text. No new colors.

> **Out of inventory (non-goals — do not build):** engine picker, batch queue table, dub segment table, voice gallery, glossary panel, casting view, story editor, marketplace/bundle import-export, theme toggle, zoom control. These are either OmniVoice surfaces cut from Parrot or V1-backlog.

---

## Screen Inventory

Parrot has **five** screens. The shell renders a header/NavRail (the Clone / Speak / Settings switch, built from `ModeTabs`) + a single content region; only `SetupGate` takes over the full window.

### 1. First-Run / Setup gate
`SetupGate` full-window takeover shown until the sidecar reports models ready. Drives off `GET /setup/status` → `{ models_ready: boolean, … }` (polled) plus the `GET /setup/download-stream` SSE progress stream (started by `POST /setup/download`). Renders `DownloadProgress` (stage stepper + `ProgressBar` + log tail). On failure: actionable hint + **Retry** / **Reset & retry**. The app does not route to Clone/Speak until `models_ready === true` and `GET /healthz` succeeds. See [first-run-setup.md](./first-run-setup.md).

### 2. Clone — *record/upload reference → save a profile*
Capture a short reference via `Recorder` or `Dropzone`, reviewed in an `AudioPlayer`. Fields (each a `Field`): profile **name** (required), **ref_text** (optional transcript), **language** (`LanguageSelect`, default `Auto`), optional **seed**, and a **de-emphasized** `instruct` style field collapsed under an "Advanced" disclosure. **Save** → `POST /profiles` (multipart: name, ref_audio, ref_text, instruct, language, seed). Over-length reference triggers a trim hint (`Toast`). Capture-flow ownership: [voice-cloning.md](./voice-cloning.md); endpoint contract: [voice-profiles.md](./voice-profiles.md).

### 3. Speak — *text → voice picker → generate → player*
Primary screen. A `TextComposer` for the text (required), a `VoicePicker` bound to `GET /profiles` (or an inline ad-hoc reference upload), a primary **Generate** `Button`, and an `AudioPlayer` for the result. Generation params surface as `Slider`s, defaulting to the contract values: `num_step=16`, `guidance_scale=2.0`, `speed=1.0`, `denoise=true`, `postprocess_output=true`, `effect_preset="broadcast"`; advanced (`t_shift`, `layer_penalty_factor`, `position_temperature`, `class_temperature`) behind a disclosure. **Generate** → `POST /generate` (multipart) returning a `StreamingResponse audio/wav`; player metadata reads from headers `X-Audio-Id`, `X-Gen-Time`, `X-Audio-Path`, `X-Seed`, `X-Audio-Duration`. The optional WS path (`ws://127.0.0.1:3900/ws/tts`, chunked PCM) feeds the same `AudioPlayer`. See [synthesis.md](./synthesis.md).

### 4. Voice Profile detail — *rename / test / lock / delete*
Opened from a `VoiceCard`. Shows the reference (`AudioPlayer`), usage (`GET /profiles/{id}/usage` → `{"synth_recent":[…], "synth_total":int}`), and actions: **rename/edit** (`PUT /profiles/{id}`), **test** (a quick `POST /generate` with `profile_id`), **lock** (`POST /profiles/{id}/lock`) / **unlock** (`POST /profiles/{id}/unlock`), and **delete** (`DELETE /profiles/{id}`, behind a non-dismissable confirm `Dialog`). A locked profile shows the `LOCKED` `Badge` and resolves to its `locked_audio_path` + stored `ref_text`/`seed`. Entity + CRUD + lock/unlock + usage contract: [voice-profiles.md](./voice-profiles.md).

### 5. Settings
A small panel set of `Card`s: **Appearance** — light-only in V1 (a read-only "Light theme" indicator; **no** theme toggle and **no** zoom Slider — both are backlog); **Engine** (read-only `{"active":"omnivoice","device":"<id>"}` `Badge`/label from `GET /engine/status` — no picker); **HF token** field (`TextInput` type=password; stored encrypted in the `settings` table; resolution order is the in-app encrypted setting first, then `HF_TOKEN` env var as a documented power-user override — `GET/POST/DELETE /settings/hf-token`); **History** management (`GET /history`, `DELETE /history`, `DELETE /history/{id}`); and **data folder** (`parrot_data/`) location/open action. See [settings.md](./settings.md).

---

## Layout Shell

The shell is owned by `frontend/src/routes/+layout.svelte`.

- **Header:** sticky top bar, `bg-snow-white/90` + backdrop blur, hairline `border-b border-outline-gray`. Contains the logo + a "native" `Badge`, the `ModeTabs` (Clone / Speak / Settings), and a version / "Update to vX" `Button` on the right.
- **Container:** content is centered at `max-w-[1000px]` with `px-6` (24px) horizontal padding, on `bg-cloud-mist`; cards are `snow-white`.
- **Vertical rhythm:** ~40px section gap; comfortable breathing room (`gap-6`/`p-6` inside cards).
- The shell mounts only after `SetupGate` reports ready; before that, `SetupGate` is the whole window.
- OmniVoice's third "history sidebar" column and bottom logs-footer chrome are **not** part of Parrot's shell — history lives inside Settings.

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
On `open`, focus moves to the first focusable element (or the dialog container); on `closed`, focus returns to the element that opened it.

### `AudioPlayer` (per instance)
```
empty --load(src)--> loading --canplay--> ready(paused)
ready(paused) --play--> playing --pause/ended--> ready(paused)
loading --error--> error(toast)
```
Full playback behavior in the synthesis flow: [synthesis.md](./synthesis.md).

---

## Edge Cases

- **Long voice-profile names.** `VoiceCard` and `VoicePicker` must truncate with ellipsis and expose the full name via `title`/`aria-label`; never reflow the card grid.
- **Mic permission denied / no input device** (Clone). `Recorder` surfaces a clear `Toast` and falls back to `Dropzone`; it never silently no-ops.
- **Over-length reference audio** (Clone). Probe duration before upload; if over the cloning max, prompt to trim (`Toast`, `danger` hint) rather than sending a too-long sample.
- **Empty text on Generate** (Speak). Disable **Generate** (`disabled:opacity-50`) and show an inline/toast error; do not POST.
- **No profile + no ad-hoc reference** (Speak in clone-from-scratch mode). Block generation with an error `Toast` — matches the sidecar's required-input rule.
- **Streaming WS drop** (`ws://127.0.0.1:3900/ws/tts`). If the socket closes mid-synthesis, fall back to the buffered `POST /generate` result or surface a retry; the player must not hang in `loading`.
- **Locked profile resolution.** A locked `VoiceCard` shows the `LOCKED` `Badge` and disables reference-swap so users aren't surprised the live reference is ignored (generation uses locked audio + stored seed).
- **Backend not ready / `GET /healthz` failing after setup.** `GET /healthz` returns `{"status":"ok"}` only; the shell shows a non-blocking reconnect banner when it fails. Navigation stays available but generation actions are disabled until health returns.
- **Reduced motion.** The `Spinner`, `ProgressBar` indeterminate slide, and `Recorder` recording pulse must honor `prefers-reduced-motion: reduce` — decorative animation is suppressed; only state-feedback transitions remain. This is a default-on accessibility behavior.
- **High-contrast / forced-colors OS mode.** The `action-blue` focus ring and field borders must remain visible under `forced-colors`; do not rely solely on shadow glows for affordance.

---

## Do / Don't

**DO**
- Use Montserrat (`font-gilroy`) for all text.
- Reserve `action-blue` for the primary action path (CTA, active nav/pill, focus ring, key accents).
- Use `rounded-2xl` + `shadow-sm-2` on elevated cards; `shadow-sm` for the interactive/hover lift.
- Use `midnight-indigo` headings + `slate-blue` secondary text; `pale-gray` for soft separations and badge fills.
- Keep Svelte component class strings **identical** to Toolzy's recipes.

**DON'T**
- No saturated accents for large text blocks.
- No heavy shadows on non-interactive elements.
- No extra font families.
- Don't break the 8px grid (no off-scale px, no custom `--spacing-*`).
- Never pure `#000` — use `text-black` (`#0a0a0a`) or `midnight-indigo`.
- No raw hex in component markup — only token-backed utilities.

---

## Accessibility

- **Contrast.** Body copy is `midnight-indigo`/`text-black` on the light `snow-white`/`cloud-mist` background (high contrast). `slate-blue` is acceptable for secondary text at ≥16px. `steel-gray` is **disabled-only**, never primary content.
- **Focus.** Every interactive element carries the shared `focusRing` — a 2px `action-blue` ring with `ring-offset-2`, visible on `focus-visible`. The ring must survive `forced-colors` mode.
- **Hit targets** are ≥40px (the `px-6 py-3` button recipe and pill padding satisfy this).
- **Never rely on color alone.** Pair color with text/icon — `LOCKED` badge has a label, success/error toasts have a message and an icon, the setup stepper marks done steps with a check, not just a hue change.
- **Reduced motion** is honored for all decorative animation (see Edge Cases).

> **Backlog (out of V1):** dark theme (`.dark` override on the `@theme` block) and UI zoom. The token block is authored to accept a `.dark` override later without component changes; neither has a Settings control in V1.
