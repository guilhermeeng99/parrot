<script lang="ts">
  import type { Snippet } from "svelte";

  // Status pill on the dark canvas. Neutral Slate Fill chip; text color carries
  // the level. Always paired with text/icon — never color alone (a11y). Yellow
  // is never used as badge text (Empower: Button Yellow is background-only).
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
    { info: "text-cloud-whisper", success: "text-success", danger: "text-danger" }[level],
  );
</script>

<span
  class="inline-flex items-center gap-1 rounded-full bg-slate-fill px-2.5 py-1 text-caption font-semibold uppercase tracking-wide {text} {klass}"
>
  {@render children?.()}
</span>
