<script lang="ts">
  import { profileAudioUrl, type VoiceProfile } from "$lib/api";
  import AudioPlayer from "./AudioPlayer.svelte";
  import Badge from "./ui/Badge.svelte";
  import Button from "./ui/Button.svelte";

  // A voice_profiles row as a selectable card. Click the body to open detail;
  // the quick action jumps to Speak with this voice preselected.
  let {
    profile,
    onopen,
    onspeak,
  }: { profile: VoiceProfile; onopen?: (id: string) => void; onspeak?: (id: string) => void } =
    $props();

  let audioUrl = $state<string | null>(null);
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
</script>

<div class="flex flex-col gap-4 rounded-2xl bg-snow-white p-6 shadow-sm-2 transition-shadow hover:shadow-sm">
  <button
    type="button"
    class="flex items-start justify-between gap-3 text-left"
    onclick={() => onopen?.(profile.id)}
    aria-label="Open {profile.name}"
  >
    <span class="min-w-0">
      <span class="block truncate text-heading font-semibold text-midnight-indigo" title={profile.name}>
        {profile.name}
      </span>
      <span class="text-body text-slate-blue">{profile.language} · {created}</span>
    </span>
    {#if profile.is_locked}
      <Badge><span aria-hidden="true">🔒</span> Locked</Badge>
    {/if}
  </button>

  {#if audioUrl}
    <AudioPlayer src={audioUrl} />
  {/if}

  <div class="flex gap-2">
    <Button size="sm" variant="outline" onclick={() => onspeak?.(profile.id)}>Speak with this</Button>
  </div>
</div>
