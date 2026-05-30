<script module lang="ts">
  // Body scroll-lock is reference-counted so stacked/nested dialogs don't unlock
  // the page while an outer dialog is still open (restore only when the last one
  // closes).
  let lockCount = 0;
  let savedOverflow = "";

  function lockScroll() {
    if (lockCount === 0) {
      savedOverflow = document.body.style.overflow;
      document.body.style.overflow = "hidden";
    }
    lockCount += 1;
  }

  function unlockScroll() {
    lockCount = Math.max(0, lockCount - 1);
    if (lockCount === 0) document.body.style.overflow = savedOverflow;
  }
</script>

<script lang="ts">
  import type { Snippet } from "svelte";

  // Modal over a dimmed backdrop. ESC + backdrop close unless dismissable=false
  // (destructive confirms). role=dialog + aria-modal, labelled by title.
  // Accessibility (design-system §modal): on open we store the opener, move
  // focus inside, lock body scroll and TRAP Tab; on close we restore both.
  let {
    open = $bindable(false),
    title = "",
    dismissable = true,
    onclose,
    children,
  }: {
    open?: boolean;
    title?: string;
    dismissable?: boolean;
    onclose?: () => void;
    children?: Snippet;
  } = $props();

  let dialogEl = $state<HTMLElement | null>(null);
  // The element to return focus to when the dialog closes (the opener).
  let opener: HTMLElement | null = null;

  function focusable(): HTMLElement[] {
    if (!dialogEl) return [];
    const sel =
      'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';
    return Array.from(dialogEl.querySelectorAll<HTMLElement>(sel));
  }

  function close() {
    if (!dismissable) return;
    open = false;
    onclose?.();
  }

  // Drive focus + scroll lock off the open flag so it works for every open
  // path (bindable prop or parent toggling it), and always cleans up.
  $effect(() => {
    if (!open) return;

    opener = document.activeElement as HTMLElement | null;
    lockScroll();

    // Move focus inside once the dialog node is mounted.
    queueMicrotask(() => (focusable()[0] ?? dialogEl)?.focus());

    return () => {
      unlockScroll();
      opener?.focus?.();
      opener = null;
    };
  });

  // Keep Tab within the dialog (focus trap). ESC closes (when dismissable).
  function onKeydown(e: KeyboardEvent) {
    if (e.key === "Escape") {
      close();
      return;
    }
    if (e.key !== "Tab") return;
    const items = focusable();
    if (items.length === 0) {
      e.preventDefault();
      dialogEl?.focus();
      return;
    }
    const first = items[0];
    const last = items[items.length - 1];
    const active = document.activeElement;
    if (e.shiftKey && active === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && active === last) {
      e.preventDefault();
      first.focus();
    }
  }
</script>

<svelte:window onkeydown={(e) => open && onKeydown(e)} />

{#if open}
  <div
    class="fixed inset-0 z-[100] flex items-center justify-center bg-night-sky/70 p-4"
    role="presentation"
    onclick={(e) => {
      if (e.target === e.currentTarget) close();
    }}
  >
    <div
      bind:this={dialogEl}
      class="flex w-full max-w-md flex-col gap-6 rounded-3xl bg-charcoal-card p-6 shadow-sm-2 ring-1 ring-white/5 focus:outline-none"
      role="dialog"
      aria-modal="true"
      aria-label={title}
      tabindex="-1"
    >
      {#if title}
        <h2 class="text-heading font-display font-bold tracking-tight text-cloud-whisper">{title}</h2>
      {/if}
      {@render children?.()}
    </div>
  </div>
{/if}
