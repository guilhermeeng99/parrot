<script lang="ts">
  import type { Snippet } from "svelte";

  // Elevated surface in the Empower command center: a Charcoal Card on the
  // Deep Space canvas. Hierarchy comes from surface contrast + a 24px radius
  // (rounded-3xl) + a faint top-edge ring — not heavy chrome. A soft dark
  // shadow only grounds it on the dark page. Interactive cards warm their ring
  // toward Button Yellow on hover.
  let {
    interactive = false,
    class: klass = "",
    children,
    ...rest
  }: { interactive?: boolean; class?: string; children?: Snippet; [key: string]: unknown } =
    $props();

  const base =
    "flex flex-col gap-6 rounded-3xl bg-charcoal-card p-6 shadow-sm-2 ring-1 ring-white/5";
  const hover = $derived(
    interactive ? "transition hover:ring-button-yellow/40 hover:shadow-sm" : "",
  );
</script>

<div class="{base} {hover} {klass}" {...rest}>
  {@render children?.()}
</div>
