<script lang="ts">
  import Spinner from "./ui/Spinner.svelte";

  // Play / scrub / (optionally) download a short clip. Backed by native <audio>.
  // States: loading → ready(paused) ⇄ playing → ended; error on load failure.
  let {
    src,
    downloadable = false,
    ondownload,
  }: { src: string; downloadable?: boolean; ondownload?: () => void } = $props();

  let audio: HTMLAudioElement;
  let playing = $state(false);
  let current = $state(0);
  let duration = $state(0);
  let loading = $state(true);
  let errored = $state(false);

  // A reused instance (e.g. the Result card swapping clips) must not show the
  // previous clip's loaded/errored state. Re-arm to "loading" on every src
  // change; onloadedmetadata/onerror will resolve it for the new source.
  $effect(() => {
    // Touch src so the effect re-runs when the prop changes.
    void src;
    loading = true;
    errored = false;
    current = 0;
    duration = 0;
  });

  const fill = $derived(duration > 0 ? (current / duration) * 100 : 0);

  function fmt(s: number): string {
    if (!isFinite(s) || s < 0) return "0:00";
    const m = Math.floor(s / 60);
    const r = Math.floor(s % 60);
    return `${m}:${r.toString().padStart(2, "0")}`;
  }

  async function toggle() {
    if (errored || !audio) return;
    if (playing) audio.pause();
    else {
      try {
        await audio.play();
      } catch {
        errored = true;
      }
    }
  }
</script>

<div class="flex items-center gap-3">
  <audio
    bind:this={audio}
    {src}
    preload="metadata"
    onloadedmetadata={() => {
      duration = audio.duration;
      loading = false;
    }}
    ontimeupdate={() => (current = audio.currentTime)}
    onplay={() => (playing = true)}
    onpause={() => (playing = false)}
    onended={() => {
      playing = false;
      current = 0;
    }}
    onerror={() => {
      errored = true;
      loading = false;
    }}
  ></audio>

  <button
    type="button"
    class="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-action-blue text-snow-white transition hover:brightness-105 disabled:opacity-50"
    onclick={toggle}
    disabled={errored}
    aria-label={playing ? "Pause" : "Play"}
  >
    {#if loading}<Spinner size="sm" />{:else}<span aria-hidden="true">{playing ? "❚❚" : "▶"}</span>{/if}
  </button>

  <input
    class="parrot-range flex-1"
    type="range"
    min={0}
    max={duration || 0}
    step={0.01}
    value={current}
    style="--fill:{fill}%"
    aria-label="Seek"
    aria-valuetext="{fmt(current)} of {fmt(duration)}"
    disabled={errored || loading}
    oninput={(e) => {
      const v = Number((e.currentTarget as HTMLInputElement).value);
      if (audio) audio.currentTime = v;
      current = v;
    }}
  />

  <span class="shrink-0 font-mono text-body text-slate-blue" aria-live="off">
    {fmt(current)} / {fmt(duration)}
  </span>

  {#if downloadable}
    <button
      type="button"
      class="flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-platinum-tint text-action-blue transition hover:border-action-blue hover:bg-action-blue/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-action-blue"
      onclick={ondownload}
      title="Download .wav"
      aria-label="Download audio"
    >
      <svg
        viewBox="0 0 20 20"
        class="h-4 w-4"
        fill="none"
        stroke="currentColor"
        stroke-width="1.8"
        stroke-linecap="round"
        stroke-linejoin="round"
        aria-hidden="true"
      >
        <path d="M10 3v9" />
        <path d="m6.5 8.5 3.5 3.5 3.5-3.5" />
        <path d="M4 14.5V16a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1v-1.5" />
      </svg>
    </button>
  {/if}
</div>

{#if errored}
  <p class="text-body text-danger">Couldn't load this clip.</p>
{/if}
