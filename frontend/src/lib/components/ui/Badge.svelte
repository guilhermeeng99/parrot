<script lang="ts">
  import type { Snippet } from "svelte";

  // Status pill. Keeps the pale-gray fill; swaps text color by level. Always
  // paired with text/icon — never color alone (a11y).
  let {
    level = "info",
    class: klass = "",
    children,
  }: {
    level?: "info" | "success" | "danger";
    class?: string;
    children?: Snippet;
  } = $props();

  const text = $derived(
    { info: "text-glacier-blue", success: "text-success", danger: "text-danger" }[level],
  );
</script>

<span
  class="inline-flex items-center gap-1 rounded-full bg-pale-gray px-2 py-1 text-body font-semibold {text} {klass}"
>
  {@render children?.()}
</span>
