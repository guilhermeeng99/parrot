<script lang="ts">
  import ProgressBar from "./ui/ProgressBar.svelte";
  import Spinner from "./ui/Spinner.svelte";

  // The download-progress sub-view of the setup gate (ui-ux §2.1). Never a
  // frozen 0%: resolving shows the indeterminate bar.
  let {
    state,
    pct = null,
    filename,
    attempt,
  }: {
    state: "downloading" | "verifying";
    pct?: number | null;
    filename?: string;
    attempt?: number;
  } = $props();
</script>

{#if state === "verifying"}
  <div class="flex items-center gap-3 text-slate-blue">
    <Spinner /> <span class="text-body-lg">Verifying the download…</span>
  </div>
{:else}
  <div class="flex flex-col gap-2" aria-live="polite">
    <ProgressBar value={pct} />
    {#if attempt}
      <span class="text-body text-slate-blue">Network hiccup — retrying (attempt {attempt})…</span>
    {:else if pct === null}
      <span class="text-body text-slate-blue">Preparing download…</span>
    {:else}
      <span class="font-mono text-body text-slate-blue">
        {filename ?? "model"} · {Math.round(pct * 100)}%
      </span>
    {/if}
  </div>
{/if}
