<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import DownloadProgress from "./DownloadProgress.svelte";
  import Badge from "./ui/Badge.svelte";
  import Button from "./ui/Button.svelte";
  import Field from "./ui/Field.svelte";
  import Select from "./ui/Select.svelte";
  import {
    cancelDownload,
    downloadModel,
    loadTranscribeStatus,
    selectModel,
    transcribe,
  } from "$lib/stores/transcribe";

  // Transcription-model setup (transcription.md §5 / ipc-contract §6A). The user
  // picks a Whisper model and downloads it ONCE, before cloning — then a captured
  // clip is auto-transcribed into the transcript field.
  onMount(loadTranscribeStatus);
  // Close the download SSE if the user navigates away from Clone mid-download, so
  // a backgrounded tab doesn't leak an open EventSource (transcription.md §5).
  onDestroy(cancelDownload);

  const gb = (mb: number) => `${(mb / 1000).toFixed(1)} GB`;

  let models = $derived($transcribe.status?.models ?? []);
  let options = $derived(
    models.map((m) => ({
      value: m.id,
      label: `${m.label} · ${gb(m.size_mb)}${m.downloaded ? " — downloaded" : ""}`,
    })),
  );
  let selected = $derived(models.find((m) => m.id === $transcribe.selectedModel));
  let dl = $derived($transcribe.download);
  let busy = $derived(dl.state === "downloading" || dl.state === "verifying");
</script>

<div class="flex flex-col gap-3 rounded-xl bg-pale-gray/60 p-4">
  <div class="flex items-center justify-between gap-2">
    <span class="text-body font-semibold uppercase tracking-wide text-slate-blue">
      Auto-transcription
    </span>
    {#if $transcribe.status}
      {#if $transcribe.status.gpu}
        <Badge level="success" class="whitespace-nowrap"
          ><span aria-hidden="true">⚡</span> GPU acceleration on</Badge
        >
      {:else}
        <Badge level="info" class="whitespace-nowrap">Running on CPU — slower</Badge>
      {/if}
    {/if}
  </div>

  <p class="text-body text-slate-blue">
    Parrot can fill the transcript for you by listening to your clip. Pick a model and download it
    once — higher fidelity is larger and slower.
  </p>

  <Field label="Model">
    <Select
      value={$transcribe.selectedModel}
      {options}
      disabled={busy}
      onchange={(e: Event) => selectModel((e.currentTarget as HTMLSelectElement).value)}
    />
  </Field>

  {#if selected?.downloaded}
    <Badge level="success" class="self-start">Ready — clips transcribe automatically</Badge>
  {:else if busy}
    <DownloadProgress
      state={dl.state === "verifying" ? "verifying" : "downloading"}
      pct={dl.pct}
      filename={dl.filename}
      attempt={dl.attempt}
    />
  {:else}
    <div class="flex flex-col gap-2">
      <Button variant="outline" size="sm" class="self-start" onclick={downloadModel}>
        Download {selected?.label ?? "model"} ({selected ? gb(selected.size_mb) : "—"})
      </Button>
      {#if dl.state === "failed"}
        <span class="text-body text-danger">{dl.message ?? "Download failed."} — try again.</span>
      {/if}
    </div>
  {/if}
</div>
