<script lang="ts">
  import {
    type ProfileUsage,
    type VoiceProfile,
    errMsg,
    getProfile,
    getProfileUsage,
    historyAudioUrl,
    profileAudioUrl,
  } from "$lib/api";
  import { editProfile, removeProfile, unlock } from "$lib/stores/profiles";
  import { closeProfile, mode, openProfileId, speakWith } from "$lib/stores/ui";
  import { toasts } from "$lib/stores/toasts";
  import AudioPlayer from "./AudioPlayer.svelte";
  import Badge from "./ui/Badge.svelte";
  import Button from "./ui/Button.svelte";
  import Dialog from "./ui/Dialog.svelte";
  import Field from "./ui/Field.svelte";
  import LanguageSelect from "./LanguageSelect.svelte";
  import TextInput from "./ui/TextInput.svelte";

  // Profile detail sheet (ui-ux §2.4). Reads the open id from the ui store.
  let profile = $state<VoiceProfile | null>(null);
  let usage = $state<ProfileUsage | null>(null);
  let audioUrl = $state<string | null>(null);
  let name = $state("");
  let refText = $state("");
  let language = $state("Auto");
  let confirmDelete = $state(false);

  $effect(() => {
    const id = $openProfileId;
    if (!id) {
      profile = null;
      return;
    }
    load(id);
  });

  async function load(id: string) {
    // Clear stale audio first so the previous profile's clip doesn't flash
    // while this one's reference loads.
    audioUrl = null;
    try {
      const p = await getProfile(id);
      profile = p;
      name = p.name;
      refText = p.ref_text;
      language = p.language;
      audioUrl = await profileAudioUrl(id);
      usage = await getProfileUsage(id);
    } catch (e) {
      // A 404 here = deleted from another tab; drop it and return to Clone.
      toasts.error(errMsg(e));
      closeProfile();
      mode.set("clone");
    }
  }

  async function saveMeta() {
    if (!profile) return;
    const ok = await editProfile(profile.id, { name, ref_text: refText, language });
    if (ok) toasts.success("Saved");
  }

  async function doDelete() {
    if (!profile) return;
    const ok = await removeProfile(profile.id);
    confirmDelete = false;
    if (ok) {
      closeProfile();
      mode.set("clone");
    }
  }
</script>

<Dialog open={$openProfileId !== null} title={profile?.name ?? "Voice"} onclose={closeProfile}>
  {#if profile}
    <div class="flex items-center gap-2">
      {#if profile.is_locked}
        <Badge><span aria-hidden="true">🔒</span> Locked{profile.seed !== null ? ` · seed ${profile.seed}` : ""}</Badge>
      {/if}
      <span class="text-body text-slate-blue">
        {new Date(profile.created_at * 1000).toLocaleDateString()}
      </span>
    </div>

    {#if audioUrl}<AudioPlayer src={audioUrl} />{/if}

    <Field label="Name"><TextInput bind:value={name} /></Field>
    <Field label="Transcript (ref_text)"><TextInput bind:value={refText} /></Field>
    <Field label="Language"><LanguageSelect bind:value={language} /></Field>
    <p class="text-body text-slate-blue">
      The reference clip can't be edited — re-clone to replace it.
    </p>

    <div class="flex flex-wrap gap-2">
      <Button size="sm" onclick={saveMeta}>Save changes</Button>
      <Button size="sm" variant="outline" onclick={() => speakWith(profile!.id)}>Test in Speak</Button>
      {#if profile.is_locked}
        <Button size="sm" variant="ghost" onclick={() => unlock(profile!.id)}>Unlock</Button>
      {/if}
    </div>

    {#if usage}
      <div class="flex flex-col gap-2">
        <p class="text-body text-slate-blue">Used in {usage.synth_total} generations.</p>
        {#if usage.synth_recent.length > 0}
          <ul class="flex flex-col divide-y divide-outline-gray">
            {#each usage.synth_recent as row (row.id)}
              <li class="flex flex-col gap-2 py-3">
                <span class="truncate text-body-lg text-midnight-indigo" title={row.text}>
                  {row.text}
                </span>
                <span class="text-body text-slate-blue">
                  {new Date(row.created_at * 1000).toLocaleString()}
                </span>
                {#await historyAudioUrl(row.id) then url}
                  <AudioPlayer src={url} />
                {/await}
              </li>
            {/each}
          </ul>
        {/if}
      </div>
    {/if}

    <div class="border-t border-outline-gray pt-4">
      {#if confirmDelete}
        <p class="text-body text-danger">
          Delete '{profile.name}'? Your past generations stay in History; this can't be undone.
        </p>
        <div class="mt-2 flex gap-2">
          <Button size="sm" variant="outline" onclick={doDelete}>Yes, delete</Button>
          <Button size="sm" variant="ghost" onclick={() => (confirmDelete = false)}>Cancel</Button>
        </div>
      {:else}
        <Button size="sm" variant="ghost" onclick={() => (confirmDelete = true)}>Delete voice</Button>
      {/if}
    </div>
  {/if}
</Dialog>
