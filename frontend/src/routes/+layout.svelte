<script lang="ts">
  import { onMount } from "svelte";
  import "../app.css";
  // Montserrat (the --font-gilroy family) — weights 400/500/600/700.
  import "@fontsource/montserrat/400.css";
  import "@fontsource/montserrat/500.css";
  import "@fontsource/montserrat/600.css";
  import "@fontsource/montserrat/700.css";

  import { bootstrap, initBootstrap, retry } from "$lib/stores/bootstrap";
  import { mode } from "$lib/stores/ui";
  import { applyUpdate, checkUpdate, updater } from "$lib/stores/updater";
  import Badge from "$lib/components/ui/Badge.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import Card from "$lib/components/ui/Card.svelte";
  import ModeTabs from "$lib/components/ui/ModeTabs.svelte";
  import Spinner from "$lib/components/ui/Spinner.svelte";
  import Toast from "$lib/components/ui/Toast.svelte";
  import SetupGate from "$lib/components/SetupGate.svelte";

  let { children } = $props();

  let setupReady = $state(false);

  onMount(initBootstrap);

  $effect(() => {
    if (setupReady) checkUpdate();
  });

  const navItems = [
    { value: "clone", label: "Clone" },
    { value: "speak", label: "Speak" },
    { value: "settings", label: "Settings" },
  ];
</script>

{#if $bootstrap.state === "checking"}
  <main class="flex min-h-screen items-center justify-center bg-cloud-mist px-6">
    <Card class="w-full max-w-md">
      <div class="flex items-center gap-3">
        <Spinner />
        <span class="text-body-lg text-slate-blue">
          {$bootstrap.stage ?? "Starting Parrot's engine…"}
        </span>
      </div>
    </Card>
  </main>
{:else if $bootstrap.state === "failed"}
  <main class="flex min-h-screen items-center justify-center bg-cloud-mist px-6">
    <Card class="w-full max-w-md">
      <h1 class="text-heading font-bold text-danger">Parrot's engine couldn't start.</h1>
      <p class="text-body text-slate-blue">{$bootstrap.message}</p>
      {#if $bootstrap.logs && $bootstrap.logs.length > 0}
        <pre
          class="max-h-48 overflow-auto rounded-lg bg-pale-gray p-3 text-body text-slate-blue">{$bootstrap.logs.join(
            "\n",
          )}</pre>
      {/if}
      <div class="flex gap-2">
        <Button onclick={() => retry(false)}>Retry</Button>
        <Button variant="outline" onclick={() => retry(true)}>Reset &amp; retry</Button>
      </div>
    </Card>
  </main>
{:else if !setupReady}
  <SetupGate onready={() => (setupReady = true)} />
{:else}
  <header
    class="sticky top-0 z-50 border-b border-outline-gray bg-snow-white/90 backdrop-blur"
  >
    <div class="mx-auto flex h-16 max-w-[1000px] items-center gap-4 px-6">
      <span class="text-heading font-bold text-midnight-indigo">
        <span aria-hidden="true">🦜</span> Parrot
      </span>
      <Badge>local</Badge>
      <nav class="ml-auto">
        <ModeTabs items={navItems} value={$mode} onselect={(v) => mode.set(v as typeof $mode)} />
      </nav>
      {#if $updater.state === "available"}
        <Button size="sm" onclick={applyUpdate}>Update to v{$updater.version}</Button>
      {:else if $updater.state === "downloading"}
        <Button size="sm" disabled loading>Updating…</Button>
      {/if}
    </div>
  </header>
  <main class="mx-auto max-w-[1000px] px-6 py-12">
    {@render children()}
  </main>
{/if}

<Toast />
