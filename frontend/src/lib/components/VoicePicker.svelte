<script lang="ts">
  import type { VoiceProfile } from "$lib/api";
  import Select from "./ui/Select.svelte";

  // Choose a saved profile (or the model's default voice). Default-voice is a
  // valid request (synthesis Resolution #2) — represented as the empty value.
  let {
    profiles = [],
    value = $bindable(""),
  }: { profiles?: VoiceProfile[]; value?: string } = $props();

  const options = $derived([
    { value: "", label: "Default voice" },
    ...profiles.map((p) => ({ value: p.id, label: p.is_locked ? `${p.name} (locked)` : p.name })),
  ]);
</script>

<Select bind:value {options} />
