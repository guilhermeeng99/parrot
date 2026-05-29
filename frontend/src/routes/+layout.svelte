<script lang="ts">
  import "../app.css";
  // Montserrat (the --font-gilroy family) — weights 400/500/600/700.
  import "@fontsource/montserrat/400.css";
  import "@fontsource/montserrat/500.css";
  import "@fontsource/montserrat/600.css";
  import "@fontsource/montserrat/700.css";

  let { children } = $props();
</script>

{@render children()}
