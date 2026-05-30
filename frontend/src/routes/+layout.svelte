<script lang="ts">
  import { onMount } from "svelte";
  import "../app.css";
  // Empower type system — bundled locally (offline-first).
  // Inter (--font-body): body, navigation, control labels.
  import "@fontsource/inter/400.css";
  import "@fontsource/inter/500.css";
  import "@fontsource/inter/600.css";
  import "@fontsource/inter/700.css";
  // Poppins (--font-display): assertive hero/section headlines.
  import "@fontsource/poppins/600.css";
  import "@fontsource/poppins/700.css";
  import "@fontsource/poppins/800.css";
  // Playfair Display (--font-serif): expressive serif headings.
  import "@fontsource/playfair-display/600.css";
  import "@fontsource/playfair-display/700.css";

  import { bootstrap, initBootstrap, retry } from "$lib/stores/bootstrap";
  import { mode } from "$lib/stores/ui";
  import { appVersion, applyUpdate, checkUpdate, loadAppVersion, updater } from "$lib/stores/updater";
  import Badge from "$lib/components/ui/Badge.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import Card from "$lib/components/ui/Card.svelte";
  import ModeTabs from "$lib/components/ui/ModeTabs.svelte";
  import Spinner from "$lib/components/ui/Spinner.svelte";
  import Toast from "$lib/components/ui/Toast.svelte";
  import SetupGate from "$lib/components/SetupGate.svelte";

  let { children } = $props();

  let setupReady = $state(false);

  onMount(() => {
    initBootstrap();
    loadAppVersion();
  });

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
  <main class="flex min-h-screen items-center justify-center bg-deep-space px-6">
    <Card class="w-full max-w-md">
      <div class="flex items-center gap-3">
        <Spinner />
        <span class="text-body-lg text-ash-gray">
          {$bootstrap.stage ?? "Starting Parrot's engine…"}
        </span>
      </div>
    </Card>
  </main>
{:else if $bootstrap.state === "failed"}
  <main class="flex min-h-screen items-center justify-center bg-deep-space px-6">
    <Card class="w-full max-w-md">
      <h1 class="text-heading font-display font-bold tracking-tight text-danger">
        Parrot's engine couldn't start.
      </h1>
      <p class="text-body text-ash-gray">{$bootstrap.message}</p>
      {#if $bootstrap.logs && $bootstrap.logs.length > 0}
        <pre
          class="max-h-48 overflow-auto rounded-xl bg-night-sky p-3 text-body text-ash-gray">{$bootstrap.logs.join(
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
  <header class="sticky top-0 z-50 border-b border-white/10 bg-night-sky/90 backdrop-blur">
    <div class="mx-auto flex h-16 max-w-[1000px] items-center gap-4 px-6">
      <span class="text-heading font-display font-bold tracking-tight text-cloud-whisper">
        <span aria-hidden="true">🦜</span> Parrot
      </span>
      <Badge>local</Badge>
      {#if $appVersion}
        <span class="text-body text-ash-gray" title="Installed version">v{$appVersion}</span>
      {/if}
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
