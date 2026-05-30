// Shared a11y recipe — a visible 2px button-yellow focus ring, applied to every
// interactive element. The offset is the dark canvas (deep-space) so the ring
// reads as a crisp yellow halo on the command-center surface, not a white one.
// Survives forced-colors mode.
export const focusRing =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-button-yellow focus-visible:ring-offset-2 focus-visible:ring-offset-deep-space";
