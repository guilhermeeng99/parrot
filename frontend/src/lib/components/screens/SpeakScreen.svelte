<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import {
    EFFECT_PRESETS,
    type GenerateParams,
    historyAudioMp3Bytes,
    historyAudioMp3Url,
    historyAudioUrl,
    inTauri,
    saveAudioDialog,
    transcodeWavToMp3,
  } from "$lib/api";
  import { loadProfiles, lock, profiles } from "$lib/stores/profiles";
  import { clearAll, deleteRow, history, loadHistory } from "$lib/stores/history";
  import { resetSynthesis, speak, synthesis } from "$lib/stores/synthesis";
  import { preselectedProfile } from "$lib/stores/ui";
  import { toasts } from "$lib/stores/toasts";
  import AudioPlayer from "../AudioPlayer.svelte";
  import LanguageSelect from "../LanguageSelect.svelte";
  import TextComposer from "../TextComposer.svelte";
  import VoicePicker from "../VoicePicker.svelte";
  import Button from "../ui/Button.svelte";
  import Card from "../ui/Card.svelte";
  import Dialog from "../ui/Dialog.svelte";
  import Field from "../ui/Field.svelte";
  import Select from "../ui/Select.svelte";
  import Slider from "../ui/Slider.svelte";

  let text = $state("");
  let voice = $state("");
  let language = $state("Auto");
  let speed = $state(1.0);
  let showAdvanced = $state(false);
  let seed = $state<number | null>(null);
  let numStep = $state(16);
  let guidance = $state(2.0);
  let effectPreset = $state("broadcast");
  let confirmClear = $state(false);

  const presets = EFFECT_PRESETS.map((v) => ({
    value: v,
    label: v[0].toUpperCase() + v.slice(1),
  }));

  onMount(() => {
    loadProfiles();
    loadHistory();
  });

  // Don't leave a generation running (or its blob URL leaking) after we leave.
  onDestroy(resetSynthesis);

  const busy = $derived($synthesis.state === "submitting");

  $effect(() => {
    if ($preselectedProfile) {
      voice = $preselectedProfile;
      preselectedProfile.set("");
    }
  });

  const canSpeak = $derived(text.trim().length > 0);

  function params(): GenerateParams {
    return {
      text,
      profile_id: voice || null,
      language,
      speed,
      seed,
      num_step: numStep,
      guidance_scale: guidance,
      effect_preset: effectPreset,
    };
  }

  function profileName(id: string | null): string {
    if (!id) return "Default voice";
    return $profiles.profiles.find((p) => p.id === id)?.name ?? "Default voice";
  }

  // A friendly, filesystem-safe default name: a slug of the spoken text + id
  // fallback, so saved files are recognizable instead of "<uuid>.mp3".
  function downloadName(id: string, text: string): string {
    const slug = text
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 32);
    return `parrot-${slug || id}.mp3`;
  }

  function anchorDownload(filename: string, url: string) {
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
  }

  // Export a clip as MP3 by its history id — the in-app pipeline is WAV, but the
  // sidecar re-encodes to MP3 (smaller, shareable) at `/history/{id}/audio.mp3`.
  // Both the fresh result and a History row share this one path. In Tauri the
  // bytes go through the OS save dialog; in a dev browser we anchor-download the
  // URL directly (the <a download> filename hint is only honored same-origin, so
  // `bun run dev` may name the file from the URL).
  async function downloadAudio(id: string, label: string) {
    try {
      const filename = downloadName(id, label);
      if (inTauri()) {
        const path = await saveAudioDialog(filename, await historyAudioMp3Bytes(id));
        if (path) toasts.success("Saved");
      } else {
        anchorDownload(filename, await historyAudioMp3Url(id));
      }
    } catch (e) {
      toasts.error(e instanceof Error ? e.message : String(e));
    }
  }

  // The fresh result exports straight from the WAV bytes still in memory (no
  // history-row dependency — the user may have cleared History). Transcoded to
  // MP3 by the stateless /audio/mp3 endpoint.
  async function download() {
    const r = $synthesis.result;
    if (!r) return;
    try {
      const filename = downloadName(r.id, text);
      const mp3 = await transcodeWavToMp3(r.bytes);
      if (inTauri()) {
        const path = await saveAudioDialog(filename, mp3);
        if (path) toasts.success("Saved");
      } else {
        anchorDownload(filename, URL.createObjectURL(new Blob([mp3], { type: "audio/mpeg" })));
      }
    } catch (e) {
      toasts.error(e instanceof Error ? e.message : String(e));
    }
  }

  function relTime(epoch: number): string {
    return new Date(epoch * 1000).toLocaleString();
  }

  // Resolve each row's audio URL ONCE and reuse the same promise across renders.
  // Inlining historyAudioUrl(row.id) inside {#await} would hand a fresh promise
  // to AudioPlayer on every list re-render, re-mounting it (and reloading the
  // clip's metadata). Keyed by id; survives because the component instance does.
  const audioUrlCache = new Map<string, Promise<string>>();
  function audioUrl(id: string): Promise<string> {
    let url = audioUrlCache.get(id);
    if (!url) {
      url = historyAudioUrl(id);
      audioUrlCache.set(id, url);
    }
    return url;
  }
</script>

<section class="flex flex-col gap-6">
  <header class="mx-auto max-w-xl text-center">
    <h1 class="text-display-sm font-bold text-midnight-indigo">Speak</h1>
  </header>

  <Card>
    <TextComposer bind:value={text} />
    <div class="flex flex-wrap gap-3">
      <Field label="Voice" class="min-w-[200px] flex-1">
        <VoicePicker profiles={$profiles.profiles} bind:value={voice} />
      </Field>
      <Field label="Language" class="min-w-[160px] flex-1">
        <LanguageSelect bind:value={language} />
      </Field>
      <Field label="Speed: {speed.toFixed(2)}×" class="min-w-[160px] flex-1">
        <Slider min={0.5} max={2} step={0.05} bind:value={speed} ariaLabel="Speed" />
      </Field>
    </div>

    <button
      type="button"
      class="w-fit text-body font-semibold text-action-blue hover:underline"
      aria-expanded={showAdvanced}
      onclick={() => (showAdvanced = !showAdvanced)}
    >
      Advanced — you probably don't need this {showAdvanced ? "▴" : "▾"}
    </button>
    {#if showAdvanced}
      <div class="flex flex-wrap gap-3 border-t border-outline-gray pt-4">
        <Field label="Seed" class="min-w-[140px] flex-1">
          <input
            type="number"
            class="w-full rounded-lg border border-platinum-tint bg-snow-white px-3 py-1.5 text-body-lg text-midnight-indigo focus-visible:border-action-blue focus-visible:outline-none"
            placeholder="random"
            value={seed ?? ""}
            oninput={(e) => {
              const v = (e.currentTarget as HTMLInputElement).value;
              seed = v === "" ? null : Number(v);
            }}
          />
        </Field>
        <Field label="Steps: {numStep}" class="min-w-[160px] flex-1">
          <Slider min={4} max={48} step={1} bind:value={numStep} ariaLabel="Steps" />
        </Field>
        <Field label="Guidance: {guidance.toFixed(1)}" class="min-w-[160px] flex-1">
          <Slider min={1} max={5} step={0.1} bind:value={guidance} ariaLabel="Guidance scale" />
        </Field>
        <Field label="Effect preset" class="min-w-[160px] flex-1">
          <Select options={presets} bind:value={effectPreset} />
        </Field>
      </div>
    {/if}

    <Button onclick={() => speak(params())} disabled={!canSpeak || busy} loading={busy}>
      {busy ? "Generating…" : "Speak"}
    </Button>

    {#if busy}
      {@const pct = Math.round(($synthesis.progress ?? 0) * 100)}
      <div class="flex flex-col gap-1">
        <div class="flex items-center justify-between text-body text-slate-blue">
          <!-- Announce only the coarse phase (Preparing vs Generating), not every
               per-step % tick — re-announcing the percent on each step is noise. -->
          <span aria-live="polite">{pct > 0 ? "Generating…" : "Preparing model…"}</span>
          <span class="font-mono" aria-hidden="true">{pct}%</span>
        </div>
        <div
          class="h-2 w-full overflow-hidden rounded-full bg-outline-gray"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={pct}
        >
          <div
            class="h-full rounded-full bg-action-blue transition-[width] duration-200 ease-out"
            style="width: {Math.max(pct, 3)}%"
          ></div>
        </div>
        <p class="text-body text-slate-blue">
          {pct > 0
            ? "Synthesizing on the diffusion steps — almost there."
            : "Loading the voice model into memory (first run of the session is slower)."}
        </p>
      </div>
    {/if}
  </Card>

  {#if $synthesis.state === "done" && $synthesis.result}
    {@const r = $synthesis.result}
    <Card>
      <h2 class="text-heading font-bold text-midnight-indigo">Result</h2>
      <AudioPlayer src={r.url} downloadable ondownload={download} />
      <p class="font-mono text-body text-slate-blue">
        {r.durationSeconds.toFixed(1)}s · {r.genTime.toFixed(1)}s to generate{r.seed !== null
          ? ` · seed ${r.seed}`
          : ""}
      </p>
      {#if voice}
        <Button variant="outline" size="sm" onclick={() => lock(voice, r.id, r.seed)}>
          Lock this as the voice's reference
        </Button>
      {/if}
    </Card>
  {:else if $synthesis.state === "error"}
    <Card>
      <h2 class="text-heading font-bold text-danger">Couldn't synthesize that.</h2>
      <p class="text-body text-slate-blue">{$synthesis.error}</p>
      <div class="flex gap-2">
        <Button onclick={() => speak(params())}>
          {$synthesis.oom ? "Flush & retry" : "Retry"}
        </Button>
        <Button variant="ghost" onclick={resetSynthesis}>Dismiss</Button>
      </div>
    </Card>
  {/if}

  <Card>
    <div class="flex items-center justify-between">
      <h2 class="text-heading font-bold text-midnight-indigo">History</h2>
      {#if $history.length > 0}
        <Button size="sm" variant="ghost" onclick={() => (confirmClear = true)}>Clear all</Button>
      {/if}
    </div>
    {#if $history.length === 0}
      <p class="text-body-lg text-slate-blue">Nothing spoken yet — type above and hit Speak.</p>
    {:else}
      <ul class="flex flex-col divide-y divide-outline-gray">
        {#each $history as row (row.id)}
          <li class="flex flex-col gap-2 py-3">
            <div class="flex items-start justify-between gap-3">
              <span class="min-w-0 flex-1 truncate text-body-lg text-midnight-indigo" title={row.text}>
                {row.text}
              </span>
              <button
                type="button"
                class="shrink-0 text-body text-slate-blue hover:text-danger"
                aria-label="Delete"
                onclick={() => deleteRow(row.id)}>✕</button
              >
            </div>
            <span class="text-body text-slate-blue">
              {profileName(row.profile_id)} · {relTime(row.created_at)}
            </span>
            {#await audioUrl(row.id) then url}
              <AudioPlayer
                src={url}
                downloadable
                ondownload={() => downloadAudio(row.id, row.text)}
              />
            {/await}
          </li>
        {/each}
      </ul>
    {/if}
  </Card>
</section>

<Dialog open={confirmClear} title="Clear all history?" dismissable={false}>
  <p class="text-body-lg text-slate-blue">
    This permanently removes every generation from your history. This can't be undone.
  </p>
  <div class="flex justify-end gap-2">
    <Button variant="ghost" onclick={() => (confirmClear = false)}>Cancel</Button>
    <Button
      onclick={() => {
        confirmClear = false;
        clearAll();
      }}>Delete everything</Button
    >
  </div>
</Dialog>
