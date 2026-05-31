<script lang="ts">
  import { focusRing } from "./ui/focusRing";

  // The model accepts free-form language hints across its broad zero-shot set.
  // We provide common suggestions plus Auto, but do not block less common names.
  let { value = $bindable("Auto") }: { value?: string } = $props();

  const listId = "parrot-language-options";
  const options = [
    "Auto",
    "English",
    "Spanish",
    "Portuguese",
    "French",
    "German",
    "Italian",
    "Dutch",
    "Russian",
    "Chinese",
    "Japanese",
    "Korean",
    "Hindi",
    "Arabic",
    "Bengali",
    "Turkish",
    "Vietnamese",
    "Polish",
    "Ukrainian",
    "Indonesian",
    "Thai",
    "Swedish",
    "Norwegian",
    "Danish",
    "Finnish",
    "Greek",
    "Hebrew",
  ];

  function normalize() {
    const trimmed = value.trim();
    value = trimmed || "Auto";
  }
</script>

<input
  bind:value
  list={listId}
  class="w-full rounded-xl border border-metal-gray bg-charcoal-card px-3 py-2 text-body-lg text-cloud-whisper placeholder:text-ash-gray focus-visible:border-button-yellow {focusRing}"
  placeholder="Auto (detect)"
  autocomplete="off"
  spellcheck="false"
  onblur={normalize}
/>

<datalist id={listId}>
  {#each options as option}
    <option value={option}>{option === "Auto" ? "Auto (detect)" : option}</option>
  {/each}
</datalist>
