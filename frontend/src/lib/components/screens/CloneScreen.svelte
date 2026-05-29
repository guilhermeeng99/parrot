<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import AudioPlayer from "../AudioPlayer.svelte";
  import Recorder from "../Recorder.svelte";
  import VoiceCard from "../VoiceCard.svelte";
  import LanguageSelect from "../LanguageSelect.svelte";
  import Button from "../ui/Button.svelte";
  import Card from "../ui/Card.svelte";
  import Dialog from "../ui/Dialog.svelte";
  import Dropzone from "../ui/Dropzone.svelte";
  import Field from "../ui/Field.svelte";
  import ModeTabs from "../ui/ModeTabs.svelte";
  import TextInput from "../ui/TextInput.svelte";
  import { createVoice, loadProfiles, profiles } from "$lib/stores/profiles";
  import { openProfile, speakWith } from "$lib/stores/ui";
  import { toasts } from "$lib/stores/toasts";

  // Clone screen (ui-ux §2.2): capture → name → save a reusable VoiceProfile.
  let captureMode = $state<"record" | "upload">("record");
  let captured = $state<{ blob: Blob; url: string; filename: string } | null>(null);
  let durationHint = $state("");

  let name = $state("");
  let refText = $state("");
  let language = $state("Auto");
  let saving = $state(false);
  let confirmDuplicate = $state(false);

  // One reusable probe element — creating a fresh Audio() per capture (and
  // never tearing it down) leaks a media element + decode buffer each time.
  let probe: HTMLAudioElement | null = null;

  onMount(loadProfiles);

  onDestroy(() => {
    if (captured) URL.revokeObjectURL(captured.url);
    if (probe) {
      probe.onloadedmetadata = null;
      probe.src = "";
    }
  });

  function setCaptured(blob: Blob, filename: string) {
    if (captured) URL.revokeObjectURL(captured.url);
    captured = { blob, url: URL.createObjectURL(blob), filename };
    probeLength(captured.url);
  }

  function probeLength(url: string) {
    if (!probe) probe = new Audio();
    probe.onloadedmetadata = () => {
      const d = probe?.duration ?? 0;
      durationHint =
        d < 3
          ? "That's quite short — a 3–10s clip clones more reliably."
          : d > 20
            ? "Long clips are slower and clone less cleanly — 3–10s of clean speech is ideal."
            : "";
      // Release the handler so a stale callback can't overwrite a newer probe.
      if (probe) probe.onloadedmetadata = null;
    };
    probe.src = url;
  }

  async function save() {
    if (!captured || !name.trim()) return;
    const dup = $profiles.profiles.some((p) => p.name === name.trim());
    if (dup) {
      confirmDuplicate = true;
      return;
    }
    await persist();
  }

  async function persist() {
    if (saving || !captured || !name.trim()) return; // guard against a double-fire
    confirmDuplicate = false;
    saving = true;
    const ok = await createVoice({
      name: name.trim(),
      refAudio: captured.blob,
      refAudioFilename: captured.filename,
      refText,
      language,
    });
    saving = false;
    if (ok) {
      name = "";
      refText = "";
      captured = null;
      durationHint = "";
    }
  }
</script>

<section class="flex flex-col gap-6">
  <header class="mx-auto max-w-xl text-center">
    <h1 class="text-display-sm font-bold text-midnight-indigo">Clone a voice</h1>
    <p class="text-body-lg text-slate-blue">
      Record or upload a short, clean sample. 3–10 seconds is the sweet spot.
    </p>
  </header>

  <Card>
    <ModeTabs
      class="justify-center"
      items={[
        { value: "record", label: "Record" },
        { value: "upload", label: "Upload" },
      ]}
      bind:value={captureMode}
    />

    {#if captureMode === "record"}
      <Recorder
        onrecorded={(blob) => setCaptured(blob, "recording.webm")}
        onerror={(m) => {
          toasts.error(m);
          captureMode = "upload";
        }}
      />
    {:else}
      <Dropzone
        accept="audio/*"
        onfile={(file) => setCaptured(file, file.name)}
      >
        <span class="text-body-lg text-midnight-indigo">Drop an audio file here, or click to choose</span>
        <span class="text-body text-slate-blue">wav · mp3 · m4a · flac · ogg · webm — stays on your device</span>
      </Dropzone>
    {/if}

    {#if captured}
      <div class="flex flex-col gap-3 border-t border-outline-gray pt-4">
        <AudioPlayer src={captured.url} />
        {#if durationHint}<p class="text-body text-slate-blue">{durationHint}</p>{/if}
        <Field label="Voice name">
          <TextInput bind:value={name} placeholder="e.g. My narration voice" />
        </Field>
        <Field
          label="What was said? (optional)"
          hint="Type the exact words in your clip — it sharpens the clone. Leave blank if unsure: better empty than wrong."
        >
          <TextInput bind:value={refText} placeholder="Transcript of the reference clip" />
        </Field>
        <Field label="Language">
          <LanguageSelect bind:value={language} />
        </Field>
        <Button onclick={save} loading={saving} disabled={!name.trim()}>Save voice</Button>
      </div>
    {/if}
  </Card>

  <Card>
    <h2 class="text-heading font-bold text-midnight-indigo">Your voices</h2>
    {#if $profiles.profiles.length === 0}
      <p class="text-body-lg text-slate-blue">
        No voices yet. Record or upload a sample above to clone your first voice.
      </p>
    {:else}
      <div class="flex flex-wrap gap-6">
        {#each $profiles.profiles as p (p.id)}
          <div class="min-w-[260px] flex-1">
            <VoiceCard profile={p} onopen={openProfile} onspeak={speakWith} />
          </div>
        {/each}
      </div>
    {/if}
  </Card>
</section>

<Dialog bind:open={confirmDuplicate} title="Duplicate voice name">
  <p class="text-body-lg text-slate-blue">
    You already have a voice named "{name.trim()}". Save anyway?
  </p>
  <div class="flex justify-end gap-2">
    <Button variant="ghost" onclick={() => (confirmDuplicate = false)}>Cancel</Button>
    <Button onclick={persist} loading={saving}>Save anyway</Button>
  </div>
</Dialog>
