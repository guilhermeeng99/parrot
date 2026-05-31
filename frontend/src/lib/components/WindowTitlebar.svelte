<script lang="ts">
  import { onMount } from "svelte";
  import type { Window as TauriWindow } from "@tauri-apps/api/window";

  let win: TauriWindow | null = null;
  let maximized = $state(false);

  onMount(async () => {
    if (!("__TAURI_INTERNALS__" in window)) return;

    const { getCurrentWindow } = await import("@tauri-apps/api/window");
    win = getCurrentWindow();
    await refreshMaximized();
  });

  async function refreshMaximized() {
    maximized = (await win?.isMaximized().catch(() => false)) ?? false;
  }

  async function minimize() {
    await win?.minimize();
  }

  async function toggleMaximize() {
    await win?.toggleMaximize();
    await refreshMaximized();
  }

  async function close() {
    await win?.close();
  }

  function startDrag(event: MouseEvent) {
    if (event.button !== 0 || event.detail > 1) return;
    void win?.startDragging();
  }

  function stopControlEvent(event: MouseEvent) {
    event.stopPropagation();
  }
</script>

<header
  class="window-titlebar"
  role="presentation"
  data-tauri-drag-region
  onmousedown={startDrag}
  ondblclick={toggleMaximize}
>
  <div class="window-title" data-tauri-drag-region>
    <img src="/parrot-128.png" alt="" class="window-title-logo" data-tauri-drag-region />
    <span data-tauri-drag-region>Parrot</span>
  </div>

  <div class="window-controls" role="group" aria-label="Window controls">
    <button
      type="button"
      class="window-control"
      aria-label="Minimize"
      onmousedown={stopControlEvent}
      ondblclick={stopControlEvent}
      onclick={minimize}
    >
      <span class="window-glyph minimize"></span>
    </button>
    <button
      type="button"
      class="window-control"
      aria-label={maximized ? "Restore" : "Maximize"}
      onmousedown={stopControlEvent}
      ondblclick={stopControlEvent}
      onclick={toggleMaximize}
    >
      <span class:maximized class="window-glyph maximize"></span>
    </button>
    <button
      type="button"
      class="window-control close"
      aria-label="Close"
      onmousedown={stopControlEvent}
      ondblclick={stopControlEvent}
      onclick={close}
    >
      <span class="window-glyph close-x"></span>
    </button>
  </div>
</header>

<style>
  .window-titlebar {
    display: flex;
    height: 34px;
    flex-shrink: 0;
    align-items: center;
    justify-content: space-between;
    overflow: hidden;
    border-bottom: 1px solid color-mix(in srgb, var(--color-cloud-whisper) 10%, transparent);
    background: var(--color-night-sky);
    color: var(--color-cloud-whisper);
    cursor: default;
    user-select: none;
  }

  .window-title {
    display: flex;
    min-width: 0;
    flex: 1;
    align-items: center;
    gap: 8px;
    padding: 0 12px;
    font-family: var(--font-body);
    font-size: 12px;
    font-weight: 800;
    line-height: 1;
  }

  .window-title-logo {
    width: 18px;
    height: 18px;
    border-radius: 5px;
  }

  .window-controls {
    display: flex;
    height: 100%;
    align-items: stretch;
  }

  .window-control {
    position: relative;
    display: grid;
    width: 44px;
    height: 34px;
    place-items: center;
    border: 0;
    background: transparent;
    color: var(--color-ash-gray);
    padding: 0;
  }

  .window-control:hover {
    background: color-mix(in srgb, var(--color-cloud-whisper) 8%, transparent);
    color: var(--color-cloud-whisper);
  }

  .window-control.close:hover {
    background: var(--color-danger);
    color: var(--color-cloud-whisper);
  }

  .window-glyph {
    position: relative;
    display: block;
    width: 12px;
    height: 12px;
  }

  .window-glyph.minimize::before {
    content: "";
    position: absolute;
    left: 1px;
    right: 1px;
    top: 7px;
    height: 1.5px;
    border-radius: 999px;
    background: currentColor;
  }

  .window-glyph.maximize::before {
    content: "";
    position: absolute;
    inset: 1px;
    border: 1.5px solid currentColor;
    border-radius: 2px;
  }

  .window-glyph.maximize.maximized::before {
    inset: 3px 1px 1px 3px;
  }

  .window-glyph.maximize.maximized::after {
    content: "";
    position: absolute;
    inset: 1px 3px 3px 1px;
    border: 1.5px solid currentColor;
    border-radius: 2px;
    background: var(--color-night-sky);
  }

  .window-control:hover .window-glyph.maximize.maximized::after {
    background: color-mix(in srgb, var(--color-night-sky) 94%, var(--color-cloud-whisper));
  }

  .window-glyph.close-x::before,
  .window-glyph.close-x::after {
    content: "";
    position: absolute;
    left: 1px;
    right: 1px;
    top: 5px;
    height: 1.5px;
    border-radius: 999px;
    background: currentColor;
  }

  .window-glyph.close-x::before {
    transform: rotate(45deg);
  }

  .window-glyph.close-x::after {
    transform: rotate(-45deg);
  }
</style>
