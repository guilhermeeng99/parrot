<script lang="ts">
  import type { Snippet } from "svelte";

  // A label above a control, with an optional hint (neutral or error-colored).
  let {
    label,
    hint,
    hintError = false,
    class: klass = "",
    children,
  }: {
    label: string;
    hint?: string;
    hintError?: boolean;
    class?: string;
    children?: Snippet;
  } = $props();
</script>

<label class="flex flex-col {klass}">
  <span class="mb-2 text-body font-semibold uppercase tracking-wide text-slate-blue">{label}</span>
  {@render children?.()}
  {#if hint}
    <span class="mt-1 text-body {hintError ? 'text-danger' : 'text-slate-blue'}">{hint}</span>
  {/if}
</label>
