<script lang="ts">
  import { onMount } from "svelte";
  import {
    type AppPaths,
    type TokenState,
    clearToken,
    errMsg,
    getAppPaths,
    getTokenState,
    inTauri,
    revealInFolder,
    setToken,
  } from "$lib/api";
  import { device, loadDevice } from "$lib/stores/device";
  import { toasts } from "$lib/stores/toasts";
  import Badge from "../ui/Badge.svelte";
  import Button from "../ui/Button.svelte";
  import Card from "../ui/Card.svelte";
  import Field from "../ui/Field.svelte";
  import Spinner from "../ui/Spinner.svelte";
  import TextInput from "../ui/TextInput.svelte";

  let tokenState = $state<TokenState | null>(null);
  let tokenInput = $state("");
  let busy = $state(false);
  let paths = $state<AppPaths | null>(null);

  onMount(() => {
    loadDevice();
    refreshToken();
    if (inTauri()) getAppPaths().then((p) => (paths = p)).catch(() => {});
  });

  async function refreshToken() {
    try {
      tokenState = await getTokenState();
    } catch (e) {
      toasts.error(errMsg(e));
    }
  }

  const appSource = $derived(tokenState?.sources.find((s) => s.source === "app"));
  const envSource = $derived(tokenState?.sources.find((s) => s.source === "env"));

  async function save() {
    busy = true;
    try {
      tokenState = await setToken(tokenInput);
      tokenInput = "";
      toasts.success("Token saved");
    } catch (e) {
      toasts.error(errMsg(e));
    } finally {
      busy = false;
    }
  }

  async function clear() {
    busy = true;
    try {
      tokenState = await clearToken();
    } catch (e) {
      toasts.error(errMsg(e));
    } finally {
      busy = false;
    }
  }

  async function viewLog() {
    if (paths) {
      try {
        await revealInFolder(paths.logPath);
      } catch (e) {
        toasts.error(errMsg(e));
      }
    }
  }
</script>

<section class="flex flex-col gap-6">
  <header class="mx-auto max-w-xl text-center">
    <h1 class="text-display-sm font-bold text-midnight-indigo">Settings</h1>
  </header>

  <Card>
    <h2 class="text-heading font-bold text-midnight-indigo">Appearance</h2>
    <p class="text-body-lg text-slate-blue">
      Parrot uses a single light theme. Dark mode is on the roadmap.
    </p>
  </Card>

  <Card>
    <h2 class="text-heading font-bold text-midnight-indigo">Engine</h2>
    {#if $device.state === "resolving" || $device.state === "unknown"}
      <div class="flex items-center gap-2 text-slate-blue">
        <Spinner size="sm" /> <span>Engine starting…</span>
      </div>
    {:else}
      <div class="flex items-center gap-2">
        <Badge>Engine: OmniVoice</Badge>
      </div>
      <p class="text-body-lg text-midnight-indigo">
        Running on <strong>{$device.label ?? $device.device}</strong>
        {#if $device.device === "cpu"}
          <span class="text-body text-slate-blue"> — slower but works.</span>
        {/if}
      </p>
    {/if}
    {#if inTauri() && paths}
      <Button size="sm" variant="ghost" onclick={viewLog}>View backend log</Button>
    {/if}
  </Card>

  <Card>
    <h2 class="text-heading font-bold text-midnight-indigo">Hugging Face token</h2>
    <p class="text-body-lg text-slate-blue">
      Only needed to download a <em>gated</em> voice model. The default Parrot voice needs no token.
    </p>
    {#if envSource?.set}
      <Badge>Set via HF_TOKEN environment variable</Badge>
    {/if}
    {#if appSource?.set}
      {#if appSource.whoami_ok}
        <Badge level="success">Signed in as {appSource.whoami_user}</Badge>
      {:else}
        <p class="text-body text-danger">Token saved but not valid — it may be expired or mistyped.</p>
      {/if}
      <p class="font-mono text-body text-slate-blue">{appSource.masked}</p>
    {:else}
      <p class="text-body text-slate-blue">No token — gated downloads are disabled.</p>
    {/if}
    <Field label="Token">
      <TextInput type="password" bind:value={tokenInput} placeholder="hf_…" />
    </Field>
    <div class="flex gap-2">
      <Button onclick={save} loading={busy} disabled={!tokenInput.trim()}>Save token</Button>
      <Button variant="ghost" onclick={refreshToken}>Test now</Button>
      <Button variant="ghost" onclick={clear} disabled={!appSource?.set}>Clear</Button>
    </div>
  </Card>

  {#if inTauri() && paths}
    <Card>
      <h2 class="text-heading font-bold text-midnight-indigo">Data folder</h2>
      <p class="font-mono text-body text-slate-blue">{paths.dataDir}</p>
      <Button size="sm" variant="ghost" onclick={() => revealInFolder(paths!.dataDir)}>
        Open data folder
      </Button>
    </Card>
  {/if}
</section>
