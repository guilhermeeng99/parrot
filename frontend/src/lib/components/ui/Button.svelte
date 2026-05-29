<script lang="ts">
  import type { Snippet } from "svelte";
  import { focusRing } from "./focusRing";
  import Spinner from "./Spinner.svelte";

  // The one button primitive (design-system Rule 8). Every clickable affordance
  // routes through here with a variant/size — never an ad-hoc <button>.
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
    sm: "px-3 py-1.5 text-body",
  } as const;
  const variants = {
    primary: "bg-action-blue text-snow-white hover:brightness-105",
    ghost: "bg-transparent text-action-blue hover:bg-pale-gray",
    outline:
      "border border-platinum-tint bg-snow-white text-midnight-indigo hover:border-action-blue",
  } as const;
  const base = `inline-flex items-center justify-center gap-2 rounded-lg font-semibold transition disabled:opacity-50 disabled:cursor-not-allowed ${focusRing}`;
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
