<script lang="ts">
  import { onMount } from "svelte";
  import { getHealth } from "$lib/api/health";
  import { getEngineStatus, type EngineStatus } from "$lib/api/engine";
  import { whenSidecarReady } from "$lib/api/sidecar";

  // Named `status`, not `state` — `$state` is a Svelte 5 rune, and shadowing it
  // with a same-named binding breaks svelte-check's type analysis.
  let status = $state<"loading" | "ok" | "error">("loading");
  let engine = $state<EngineStatus | null>(null);
  let errorMsg = $state("");

  async function probe() {
    status = "loading";
    errorMsg = "";
    try {
      await getHealth();
      engine = await getEngineStatus();
      status = "ok";
    } catch (e) {
      errorMsg = e instanceof Error ? e.message : String(e);
      status = "error";
    }
  }

  onMount(() => {
    // Under Tauri, wait for the supervisor's readiness signal before the first
    // probe so we don't flash an error while the sidecar is still booting; if
    // the supervisor gives up, show that failure. In a plain browser
    // (`bun run dev`) there is no supervisor, so this resolves immediately.
    whenSidecarReady().then(probe, (e: unknown) => {
      errorMsg = e instanceof Error ? e.message : String(e);
      status = "error";
    });
  });
</script>

<main class="mx-auto flex min-h-screen max-w-[1000px] flex-col items-center justify-center px-6">
  <div class="flex w-full max-w-md flex-col gap-6 rounded-2xl bg-snow-white p-6 shadow-sm-2">
    <header class="flex items-center gap-3">
      <span class="text-heading-lg">🦜</span>
      <div>
        <h1 class="text-heading font-bold text-midnight-indigo">Parrot</h1>
        <p class="text-body text-slate-blue">Clone a voice. Make it speak.</p>
      </div>
    </header>

    {#if status === "loading"}
      <div class="flex items-center gap-3 text-slate-blue">
        <span
          class="h-5 w-5 shrink-0 animate-spin rounded-full border-2 border-action-blue/30 border-t-action-blue"
        ></span>
        <span class="text-body-lg">Connecting to the engine…</span>
      </div>
    {:else if status === "ok" && engine}
      <div class="flex flex-col gap-2">
        <span
          class="w-fit rounded-full bg-pale-gray px-2 py-1 text-body font-semibold text-glacier-blue"
        >
          Engine online
        </span>
        <p class="text-body-lg text-midnight-indigo">
          Active engine: <strong class="font-semibold">{engine.active}</strong>
        </p>
        <p class="text-body-lg text-midnight-indigo">
          Compute device: <strong class="font-semibold">{engine.device}</strong>
        </p>
        <p class="text-body text-slate-blue">
          The Svelte UI read this value from the Python sidecar over loopback — the three-process
          architecture works end to end.
        </p>
      </div>
    {:else}
      <div class="flex flex-col gap-3">
        <p class="text-body-lg font-semibold text-danger">Couldn't reach the engine.</p>
        <p class="text-body text-slate-blue">{errorMsg}</p>
        <button
          type="button"
          onclick={probe}
          class="w-fit rounded-lg bg-action-blue px-6 py-3 text-body-lg font-semibold text-snow-white transition hover:brightness-105 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-action-blue focus-visible:ring-offset-2"
        >
          Retry
        </button>
      </div>
    {/if}
  </div>
</main>
