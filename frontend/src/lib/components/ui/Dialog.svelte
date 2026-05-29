<script lang="ts">
  import type { Snippet } from "svelte";

  // Modal over a dimmed backdrop. ESC + backdrop close unless dismissable=false
  // (destructive confirms). role=dialog + aria-modal, labelled by title.
  let {
    open = $bindable(false),
    title = "",
    dismissable = true,
    onclose,
    children,
  }: {
    open?: boolean;
    title?: string;
    dismissable?: boolean;
    onclose?: () => void;
    children?: Snippet;
  } = $props();

  function close() {
    if (!dismissable) return;
    open = false;
    onclose?.();
  }
</script>

<svelte:window onkeydown={(e) => e.key === "Escape" && close()} />

{#if open}
  <div
    class="fixed inset-0 z-[100] flex items-center justify-center bg-midnight-indigo/40 p-4"
    role="presentation"
    onclick={(e) => {
      if (e.target === e.currentTarget) close();
    }}
  >
    <div
      class="flex w-full max-w-md flex-col gap-6 rounded-2xl bg-snow-white p-6 shadow-sm-2"
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      {#if title}
        <h2 class="text-heading font-bold text-midnight-indigo">{title}</h2>
      {/if}
      {@render children?.()}
    </div>
  </div>
{/if}
