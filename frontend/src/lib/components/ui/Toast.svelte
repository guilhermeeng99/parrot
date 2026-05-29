<script lang="ts">
  import { toasts } from "$lib/stores/toasts";

  const color = (level: string) =>
    level === "success" ? "text-success" : level === "danger" ? "text-danger" : "text-midnight-indigo";
</script>

<div class="pointer-events-none fixed bottom-4 right-4 z-[200] flex flex-col gap-2">
  {#each $toasts as t (t.id)}
    <div
      class="pointer-events-auto flex max-w-sm items-start gap-3 rounded-lg bg-snow-white p-4 shadow-sm-2"
      role={t.level === "danger" ? "alert" : "status"}
      aria-live={t.level === "danger" ? "assertive" : "polite"}
    >
      <span class="text-body-lg font-medium {color(t.level)}">{t.message}</span>
      <button
        type="button"
        class="ml-auto text-body text-slate-blue hover:text-midnight-indigo"
        aria-label="Dismiss"
        onclick={() => toasts.dismiss(t.id)}>✕</button
      >
    </div>
  {/each}
</div>
