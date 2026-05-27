/**
 * Motion timings — sourced from
 * docs/features/confirmed-fingerprint-coverage/design-output/README.md
 * § "Motion". Centralized so all surfaces feel consistent.
 *
 * Durations in seconds (Framer Motion convention) unless noted.
 */
export const MOTION = {
  /** Pointerdown press (transform). 80ms. */
  buttonPress: 0.08,
  /** Background/color hover. 140ms. */
  hover: 0.14,
  /** Tracklist row chevron translate / padding shift. 160ms. */
  rowShift: 0.16,
  /** Album card hover lift. 180ms. */
  cardLift: 0.18,
  /** LearningChip entrance. 320ms ease-out. */
  chipIn: 0.32,
  /** Post-tap green flash duration on tracklist row, ms. */
  confirmFlashMs: 1400,
  /** GuessConfirm 60s auto-dismiss, ms. */
  guessTimeoutMs: 60_000,
  /** UndoStrip transient mode auto-hide, ms. */
  undoTransientMs: 8_000,
  /** LearningChip auto-dismiss, ms. */
  learningChipMs: 3_500,
  /** identifying → needs-id timeout. Covers ~3 heartbeats (3 × 15s),
      enough for blind fingerprint discovery to converge. Ms. */
  identifyingTimeoutMs: 45_000,
  /** user-pinned → confirmed-local graduation. Matches Feature A's
      cohort fill time (cap=10 × ~5s heartbeat). Ms. */
  pinGraduationMs: 50_000,
  /** /api/pin-track AbortController timeout, ms. */
  pinTrackTimeoutMs: 5_000,
  /** InlineError auto-dismiss, ms. */
  inlineErrorMs: 6_000,
} as const;
