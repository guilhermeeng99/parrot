<script lang="ts">
  import { toasts } from "$lib/stores/toasts";

  const color = (level: string) =>
    level === "success" ? "text-success" : level === "danger" ? "text-danger" : "text-cloud-whisper";
</script>

<div class="pointer-events-none fixed bottom-4 right-4 z-[200] flex flex-col gap-2">
  {#each $toasts as t (t.id)}
    <div
      class="pointer-events-auto flex max-w-sm items-start gap-3 rounded-xl bg-charcoal-card p-4 shadow-sm-2 ring-1 ring-white/5"
      role={t.level === "danger" ? "alert" : "status"}
      aria-live={t.level === "danger" ? "assertive" : "polite"}
    >
      <span class="text-body-lg font-medium {color(t.level)}">{t.message}</span>
      <button
        type="button"
        class="ml-auto text-body text-ash-gray hover:text-cloud-whisper"
        aria-label="Dismiss"
        onclick={() => toasts.dismiss(t.id)}>✕</button
      >
    </div>
  {/each}
</div>
