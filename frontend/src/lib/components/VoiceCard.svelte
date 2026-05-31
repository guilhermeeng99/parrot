<script lang="ts">
  import {
    type VoiceProfile,
    inTauri,
    profileAudioUrl,
    profileOriginalAudioBytes,
    profileOriginalAudioUrl,
    saveAudioDialog,
  } from "$lib/api";
  import { removeProfile } from "$lib/stores/profiles";
  import { toasts } from "$lib/stores/toasts";
  import AudioPlayer from "./AudioPlayer.svelte";
  import Badge from "./ui/Badge.svelte";
  import Button from "./ui/Button.svelte";
  import Dialog from "./ui/Dialog.svelte";

  // A voice_profiles row as a selectable card. Click the body to open detail; the
  // action row speaks with this voice, downloads its original reference clip, or
  // deletes it (with a confirm).
  let {
    profile,
    onopen,
    onspeak,
  }: { profile: VoiceProfile; onopen?: (id: string) => void; onspeak?: (id: string) => void } =
    $props();

  let audioUrl = $state<string | null>(null);
  let confirmDelete = $state(false);
  let deleting = $state(false);

  $effect(() => {
    let alive = true;
    profileAudioUrl(profile.id).then((u) => {
      if (alive) audioUrl = u;
    });
    return () => {
      alive = false;
    };
  });

  const created = $derived(new Date(profile.created_at * 1000).toLocaleDateString());

  // The original reference keeps its source extension (webm/wav/mp3/…); download
  // it as-is so the user gets exactly what they cloned from.
  function refExt(): string {
    const name = profile.ref_audio_path || "";
    const dot = name.lastIndexOf(".");
    return dot > -1 ? name.slice(dot + 1).toLowerCase() : "wav";
  }

  function downloadName(): string {
    const slug = profile.name
      .trim()
      .replace(/[^\p{L}\p{N}]+/gu, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 40);
    return `${slug || profile.id}.${refExt()}`;
  }

  async function download() {
    try {
      const filename = downloadName();
      if (inTauri()) {
        const path = await saveAudioDialog(filename, await profileOriginalAudioBytes(profile.id));
        if (path) toasts.success("Saved");
      } else {
        const a = document.createElement("a");
        a.href = await profileOriginalAudioUrl(profile.id);
        a.download = filename;
        a.click();
      }
    } catch (e) {
      toasts.error(e instanceof Error ? e.message : String(e));
    }
  }

  async function confirmRemove() {
    deleting = true;
    await removeProfile(profile.id);
    deleting = false;
    confirmDelete = false;
  }

  const iconBtn =
    "flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-metal-gray transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-button-yellow";
</script>

<div class="flex flex-col gap-4 rounded-3xl bg-charcoal-card p-6 shadow-sm-2 ring-1 ring-white/5 transition-shadow hover:shadow-sm">
  <button
    type="button"
    class="flex items-start justify-between gap-3 text-left"
    onclick={() => onopen?.(profile.id)}
    aria-label="Open {profile.name}"
  >
    <span class="min-w-0">
      <span class="block truncate text-heading font-display font-semibold tracking-tight text-cloud-whisper" title={profile.name}>
        {profile.name}
      </span>
      <span class="text-body text-ash-gray">{profile.language} · {created}</span>
    </span>
    {#if profile.is_locked}
      <Badge><span aria-hidden="true">🔒</span> Locked</Badge>
    {/if}
  </button>

  {#if audioUrl}
    <AudioPlayer src={audioUrl} />
  {/if}

  <div class="flex items-center gap-2">
    <Button size="sm" variant="outline" onclick={() => onspeak?.(profile.id)}>Speak with this</Button>
    <span class="flex-1"></span>
    <button
      type="button"
      class="{iconBtn} text-button-yellow hover:border-button-yellow hover:bg-button-yellow/10"
      onclick={download}
      title="Download original clip"
      aria-label="Download {profile.name}'s original clip"
    >
      <svg viewBox="0 0 20 20" class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M10 3v9" /><path d="m6.5 8.5 3.5 3.5 3.5-3.5" /><path d="M4 14.5V16a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1v-1.5" />
      </svg>
    </button>
    <button
      type="button"
      class="{iconBtn} text-danger hover:border-danger hover:bg-danger/10"
      onclick={() => (confirmDelete = true)}
      title="Delete voice"
      aria-label="Delete {profile.name}"
    >
      <svg viewBox="0 0 20 20" class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M4 6h12" /><path d="M8 6V4.5a1 1 0 0 1 1-1h2a1 1 0 0 1 1 1V6" /><path d="M6.5 6l.6 9a1 1 0 0 0 1 .94h3.8a1 1 0 0 0 1-.94l.6-9" />
      </svg>
    </button>
  </div>
</div>

<Dialog bind:open={confirmDelete} title="Delete this voice?">
  <p class="text-body-lg text-ash-gray">
    Delete "{profile.name}"? This removes the voice and its reference clip. Past generations stay in
    History. This can't be undone.
  </p>
  <div class="flex justify-end gap-2">
    <Button variant="ghost" onclick={() => (confirmDelete = false)}>Cancel</Button>
    <Button variant="outline" class="!border-danger !text-danger hover:!bg-danger/10" onclick={confirmRemove} loading={deleting}>
      Delete
    </Button>
  </div>
</Dialog>
