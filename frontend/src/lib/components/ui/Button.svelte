<script lang="ts">
  import type { Snippet } from "svelte";
  import { focusRing } from "./focusRing";
  import Spinner from "./Spinner.svelte";

  // The one button primitive (design-system Rule 8). Every clickable affordance
  // routes through here with a variant/size — never an ad-hoc <button>.
  //  - primary: the ONE yellow CTA (Button Yellow fill, Night Sky text).
  //  - ghost:   text-only action (yellow label, subtle hover wash).
  //  - outline: bordered neutral action on the dark canvas.
  // All variants are pill-shaped (rounded-full) per Empower.
  let {
    variant = "primary",
    size = "md",
    disabled = false,
    loading = false,
    type = "button",
    class: klass = "",
    children,
    ...rest
  }: {
    variant?: "primary" | "ghost" | "outline";
    size?: "md" | "sm";
    disabled?: boolean;
    loading?: boolean;
    type?: "button" | "submit" | "reset";
    class?: string;
    children?: Snippet;
    [key: string]: unknown;
  } = $props();

  const sizes = {
    md: "px-6 py-3 text-body-lg", // ≥40px hit target
    sm: "px-4 py-1.5 text-body",
  } as const;
  const variants = {
    primary: "bg-button-yellow text-night-sky hover:brightness-105",
    ghost: "bg-transparent text-button-yellow hover:bg-button-yellow/10",
    outline:
      "border border-metal-gray bg-transparent text-cloud-whisper hover:border-button-yellow",
  } as const;
  const base = `inline-flex items-center justify-center gap-2 rounded-full font-semibold transition disabled:opacity-50 disabled:cursor-not-allowed ${focusRing}`;
</script>

<button
  {type}
  class="{base} {sizes[size]} {variants[variant]} {klass}"
  disabled={disabled || loading}
  aria-busy={loading}
  {...rest}
>
  {#if loading}<Spinner size="sm" />{/if}
  {@render children?.()}
</button>
