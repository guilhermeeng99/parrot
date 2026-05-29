<script lang="ts">
  // Indeterminate short-wait spinner. Animation is suppressed under
  // prefers-reduced-motion (see app.css); the aria-label keeps it announced.
  let { size = "md", class: klass = "" }: { size?: "sm" | "md"; class?: string } = $props();
  const dims = $derived(size === "sm" ? "h-4 w-4" : "h-5 w-5");
</script>

<span
  class="{dims} shrink-0 animate-spin rounded-full border-2 border-action-blue/30 border-t-action-blue {klass}"
  role="status"
  aria-label="Working"
></span>
