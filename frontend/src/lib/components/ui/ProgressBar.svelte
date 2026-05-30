<script lang="ts">
  // Determinate (value 0..1) or indeterminate (value null) progress.
  let { value = null, class: klass = "" }: { value?: number | null; class?: string } = $props();
</script>

<div
  class="h-2 w-full overflow-hidden rounded-full bg-slate-fill {klass}"
  role="progressbar"
  aria-valuemin={0}
  aria-valuemax={100}
  aria-valuenow={value === null ? undefined : Math.round(value * 100)}
>
  {#if value === null}
    <div class="parrot-indeterminate h-full w-1/3 rounded-full bg-button-yellow"></div>
  {:else}
    <div
      class="h-full rounded-full bg-button-yellow transition-[width]"
      style="width:{Math.max(0, Math.min(1, value)) * 100}%"
    ></div>
  {/if}
</div>
