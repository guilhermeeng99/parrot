<script lang="ts">
  // 6px slate-fill track, button-yellow fill driven by the inline --fill %, 18px
  // button-yellow thumb. Keyboard: arrows step, Home/End jump (native range).
  let {
    min = 0,
    max = 100,
    step = 1,
    value = $bindable(0),
    ariaLabel = "",
    class: klass = "",
    ...rest
  }: {
    min?: number;
    max?: number;
    step?: number;
    value?: number;
    ariaLabel?: string;
    class?: string;
    [key: string]: unknown;
  } = $props();

  const fill = $derived(max > min ? ((value - min) / (max - min)) * 100 : 0);
</script>

<input
  class="parrot-range {klass}"
  type="range"
  {min}
  {max}
  {step}
  bind:value
  style="--fill:{fill}%"
  aria-label={ariaLabel}
  {...rest}
/>
