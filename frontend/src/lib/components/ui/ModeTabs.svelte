<script lang="ts">
  import Pill from "./Pill.svelte";

  // The Clone / Speak / Settings switch (the header NavRail). aria-pressed via Pill.
  let {
    items = [],
    value = $bindable(""),
    onselect,
    class: klass = "",
  }: {
    items?: { value: string; label: string; disabled?: boolean }[];
    value?: string;
    onselect?: (v: string) => void;
    class?: string;
  } = $props();
</script>

<div class="flex flex-wrap gap-2 {klass}">
  {#each items as it (it.value)}
    <Pill
      active={value === it.value}
      disabled={it.disabled}
      onclick={() => {
        if (it.disabled) return;
        value = it.value;
        onselect?.(it.value);
      }}
    >
      {it.label}
    </Pill>
  {/each}
</div>
