<script lang="ts">
  import type { Snippet } from "svelte";
  import { focusRing } from "./focusRing";

  // Mode/nav pill. Active = Button Yellow fill (Night Sky text); inactive = a
  // quiet Slate Fill chip that warms toward yellow text on hover. Pill-shaped.
  let {
    active = false,
    disabled = false,
    class: klass = "",
    children,
    ...rest
  }: {
    active?: boolean;
    disabled?: boolean;
    class?: string;
    children?: Snippet;
    [key: string]: unknown;
  } = $props();

  const base = `rounded-full px-4 py-1.5 text-body-lg font-semibold uppercase tracking-wide transition-colors disabled:cursor-not-allowed ${focusRing}`;
  const state = $derived(
    disabled
      ? "bg-slate-fill text-metal-gray"
      : active
        ? "bg-button-yellow text-night-sky"
        : "bg-slate-fill text-ash-gray hover:text-button-yellow",
  );
</script>

<button
  type="button"
  class="{base} {state} {klass}"
  aria-pressed={active}
  aria-disabled={disabled}
  {disabled}
  {...rest}
>
  {@render children?.()}
</button>
