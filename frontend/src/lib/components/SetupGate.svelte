<script lang="ts">
  import { onMount } from "svelte";
  import { errMsg, setToken } from "$lib/api";
  import { checkSetup, setup, startDownload } from "$lib/stores/setup";
  import { toasts } from "$lib/stores/toasts";
  import Button from "./ui/Button.svelte";
  import Card from "./ui/Card.svelte";
  import Field from "./ui/Field.svelte";
  import TextInput from "./ui/TextInput.svelte";
  import DownloadProgress from "./DownloadProgress.svelte";

  // First-run model-download gate (ui-ux §2.1). Held here until models_ready.
  let { onready }: { onready?: () => void } = $props();

  let token = $state("");
  let savingToken = $state(false);

  onMount(checkSetup);

  $effect(() => {
    if ($setup.state === "ready") onready?.();
  });

  async function saveTokenAndRetry() {
    savingToken = true;
    try {
      await setToken(token);
      await startDownload();
    } catch (e) {
      toasts.error(errMsg(e));
    } finally {
      savingToken = false;
    }
  }
</script>

<main class="flex min-h-screen flex-col items-center justify-center bg-deep-space px-6">
  <Card class="w-full max-w-md">
    <header class="flex items-center gap-3">
      <span class="text-heading-lg" aria-hidden="true">🦜</span>
      <h1 class="text-heading-lg font-display font-bold tracking-tight text-cloud-whisper">Parrot</h1>
    </header>

    {#if $setup.state === "checking"}
      <p class="text-body-lg text-ash-gray">Checking your setup…</p>
    {:else if $setup.state === "downloading" || $setup.state === "verifying"}
      <h2 class="text-heading font-display font-bold tracking-tight text-cloud-whisper">Downloading the voice model…</h2>
      <DownloadProgress
        state={$setup.state}
        pct={$setup.pct}
        filename={$setup.filename}
        attempt={$setup.attempt}
      />
    {:else if $setup.state === "needs_token"}
      <h2 class="text-heading font-display font-bold tracking-tight text-cloud-whisper">This model is gated</h2>
      <p class="text-body-lg text-ash-gray">
        Paste a Hugging Face token to continue. Most users never need this.
      </p>
      <Field label="Hugging Face token">
        <TextInput type="password" bind:value={token} placeholder="hf_…" />
      </Field>
      <Button onclick={saveTokenAndRetry} loading={savingToken} disabled={!token.trim()}>
        Save token & continue
      </Button>
    {:else if $setup.state === "download_failed"}
      <h2 class="text-heading font-display font-bold tracking-tight text-danger">Couldn't download the model.</h2>
      <p class="text-body text-ash-gray">
        {$setup.message ??
          "Check your connection, VPN, or firewall (needs huggingface.co:443), then retry."}
      </p>
      <Button onclick={startDownload}>Retry</Button>
    {:else if $setup.state === "needs_download"}
      <h2 class="text-heading font-display font-bold tracking-tight text-cloud-whisper">
        One more step — download the voice model.
      </h2>
      <p class="text-body-lg text-ash-gray">
        Parrot needs to download its voice engine once (a few hundred MB). After this, everything
        runs offline — no account, no internet.
      </p>
      {#if $setup.status}
        <p class="font-mono text-body text-ash-gray">{$setup.status.hf_cache_dir}</p>
        {#if !$setup.status.enough_disk}
          <p class="text-body text-danger">
            Only {$setup.status.disk_free_gb} GB free — Parrot needs at least
            {$setup.status.min_free_gb} GB. Free some space, then try again.
          </p>
        {/if}
      {/if}
      <Button onclick={startDownload} disabled={$setup.status ? !$setup.status.enough_disk : false}>
        Download model
      </Button>
    {/if}
  </Card>
</main>
