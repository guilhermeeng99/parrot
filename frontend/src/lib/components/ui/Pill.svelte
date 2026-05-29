<script lang="ts">
  import type { Snippet } from "svelte";
  import { focusRing } from "./focusRing";

  // Mode/nav pill. Active = action-blue fill; inactive = pale-gray.
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

  const base = `rounded-lg px-4 py-1.5 text-body-lg font-semibold uppercase transition-colors disabled:cursor-not-allowed ${focusRing}`;
  const state = $derived(
    disabled
      ? "bg-pale-gray text-steel-gray"
      : active
        ? "bg-action-blue text-snow-white"
        : "bg-pale-gray text-midnight-indigo hover:bg-platinum-tint",
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
