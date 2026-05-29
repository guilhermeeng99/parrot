<script lang="ts">
  import { focusRing } from "./focusRing";

  // Text / number / password input. Password gets a show/hide toggle. Svelte
  // forbids a dynamic `type` with `bind:value`, so each variant is a static branch.
  let {
    value = $bindable(""),
    type = "text",
    invalid = false,
    class: klass = "",
    ...rest
  }: {
    value?: string | number;
    type?: "text" | "number" | "password";
    invalid?: boolean;
    class?: string;
    [key: string]: unknown;
  } = $props();

  let reveal = $state(false);
  const border = $derived(invalid ? "border-danger" : "border-platinum-tint");
  const base = `w-full rounded-lg border bg-snow-white px-3 py-1.5 text-body-lg text-midnight-indigo focus-visible:border-action-blue focus-visible:outline-none ${focusRing}`;
</script>

{#if type === "password"}
  <div class="relative">
    {#if reveal}
      <input type="text" bind:value class="{base} {border} pr-16 {klass}" {...rest} />
    {:else}
      <input type="password" bind:value class="{base} {border} pr-16 {klass}" {...rest} />
    {/if}
    <button
      type="button"
      class="absolute right-2 top-1/2 -translate-y-1/2 text-body font-semibold text-action-blue hover:underline"
      onclick={() => (reveal = !reveal)}
      aria-label={reveal ? "Hide token" : "Show token"}
    >
      {reveal ? "Hide" : "Show"}
    </button>
  </div>
{:else if type === "number"}
  <input type="number" bind:value class="{base} {border} {klass}" {...rest} />
{:else}
  <input type="text" bind:value class="{base} {border} {klass}" {...rest} />
{/if}
