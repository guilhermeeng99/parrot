<script lang="ts">
  import { mode } from "$lib/stores/ui";
  import CloneScreen from "$lib/components/screens/CloneScreen.svelte";
  import SpeakScreen from "$lib/components/screens/SpeakScreen.svelte";
  import SettingsScreen from "$lib/components/screens/SettingsScreen.svelte";
  import ProfileDetail from "$lib/components/ProfileDetail.svelte";

  // Mode-based SPA: the shell (+layout) renders the header NavRail; this page
  // swaps the active screen. The profile-detail sheet overlays any screen.
</script>

{#if $mode === "clone"}
  <CloneScreen />
{:else if $mode === "speak"}
  <SpeakScreen />
{:else}
  <SettingsScreen />
{/if}

<ProfileDetail />
