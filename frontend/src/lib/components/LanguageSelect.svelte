<script lang="ts">
  import Select from "./ui/Select.svelte";

  // Language picker. The model supports ~600 zero-shot languages; we surface
  // "Auto" (default) plus a curated common set. Auto = let the model detect.
  let { value = $bindable("Auto") }: { value?: string } = $props();

  const options = [
    { value: "Auto", label: "Auto (detect)" },
    { value: "English", label: "English" },
    { value: "Spanish", label: "Spanish" },
    { value: "Portuguese", label: "Portuguese" },
    { value: "French", label: "French" },
    { value: "German", label: "German" },
    { value: "Italian", label: "Italian" },
    { value: "Dutch", label: "Dutch" },
    { value: "Russian", label: "Russian" },
    { value: "Chinese", label: "Chinese" },
    { value: "Japanese", label: "Japanese" },
    { value: "Korean", label: "Korean" },
    { value: "Hindi", label: "Hindi" },
    { value: "Arabic", label: "Arabic" },
  ];
</script>

<Select bind:value {options} />
