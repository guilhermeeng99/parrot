<script lang="ts">
  import type { Snippet } from "svelte";

  // Drag-or-click file picker. Emits the chosen File via onfile; visuals shift
  // on drag-over. The accept hint is the caller's (passed as children).
  let {
    accept = "audio/*",
    onfile,
    children,
  }: {
    accept?: string;
    onfile?: (file: File) => void;
    children?: Snippet;
  } = $props();

  let over = $state(false);
  let input: HTMLInputElement;

  function pick(files: FileList | null | undefined) {
    const f = files?.[0];
    if (f) onfile?.(f);
  }

  const base =
    "flex w-full cursor-pointer flex-col items-center justify-center gap-2 rounded-2xl border-2 border-dashed px-6 py-16 text-center transition-colors";
  // NB: must not be named `state` — Svelte would then parse `$state(false)` above
  // as a store subscription of `state` rather than the rune.
  const dropClasses = $derived(
    over
      ? "border-button-yellow bg-slate-fill/60"
      : "border-metal-gray bg-deep-space hover:border-button-yellow",
  );
</script>

<label
  class="{base} {dropClasses}"
  ondragover={(e) => {
    e.preventDefault();
    over = true;
  }}
  ondragleave={() => (over = false)}
  ondrop={(e) => {
    e.preventDefault();
    over = false;
    pick(e.dataTransfer?.files);
  }}
>
  <input
    bind:this={input}
    type="file"
    {accept}
    class="hidden"
    onchange={(e) => pick((e.currentTarget as HTMLInputElement).files)}
  />
  {@render children?.()}
</label>
