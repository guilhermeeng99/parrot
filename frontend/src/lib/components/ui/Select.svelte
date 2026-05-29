<script lang="ts">
  import { focusRing } from "./focusRing";

  // Native <select> under a custom chevron (OS-correct listbox + keyboard parity).
  let {
    value = $bindable(""),
    options = [],
    class: klass = "",
    ...rest
  }: {
    value?: string;
    options?: { value: string; label: string }[];
    class?: string;
    [key: string]: unknown;
  } = $props();
</script>

<div class="relative">
  <select
    bind:value
    class="w-full appearance-none rounded-lg border border-platinum-tint bg-snow-white px-3 py-2 pr-9 text-body-lg text-midnight-indigo focus-visible:border-action-blue {focusRing} {klass}"
    {...rest}
  >
    {#each options as opt (opt.value)}
      <option value={opt.value}>{opt.label}</option>
    {/each}
  </select>
  <span
    class="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-slate-blue"
    aria-hidden="true">▾</span
  >
</div>
