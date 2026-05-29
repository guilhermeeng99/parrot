<script lang="ts">
  import type { Snippet } from "svelte";

  // Elevated surface: rounded-2xl + shadow-sm-2 at rest. Interactive cards add
  // the hover lift. No border by default.
  let {
    interactive = false,
    class: klass = "",
    children,
    ...rest
  }: { interactive?: boolean; class?: string; children?: Snippet; [key: string]: unknown } =
    $props();

  const base = "flex flex-col gap-6 rounded-2xl bg-snow-white p-6 shadow-sm-2";
  const hover = $derived(interactive ? "transition-shadow hover:shadow-sm" : "");
</script>

<div class="{base} {hover} {klass}" {...rest}>
  {@render children?.()}
</div>
