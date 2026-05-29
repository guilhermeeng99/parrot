<script lang="ts">
  import { focusRing } from "./ui/focusRing";

  // The large prompt textarea (Speak). Empty/whitespace disables Generate upstream.
  let {
    value = $bindable(""),
    label = "Text to speak",
    placeholder = "Type anything for your voice to say…",
  }: { value?: string; label?: string; placeholder?: string } = $props();

  const empty = $derived(value.trim().length === 0);
</script>

<label class="flex flex-col">
  <span class="mb-2 text-body font-semibold uppercase tracking-wide text-slate-blue">{label}</span>
  <textarea
    bind:value
    {placeholder}
    class="min-h-[140px] w-full resize-y rounded-lg border border-platinum-tint bg-snow-white px-3 py-1.5 text-body-lg text-midnight-indigo focus-visible:border-action-blue focus-visible:outline-none {focusRing}"
  ></textarea>
  <span class="mt-1 text-body text-slate-blue">
    {#if empty}Type something to speak.{:else}{value.length} characters{/if}
  </span>
</label>
