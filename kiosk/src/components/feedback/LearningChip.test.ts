import { describe, expect, it } from 'vitest';
import { MOTION } from '@/lib/motion';

// LearningChip is a React component with Framer Motion + Zustand
// dependencies; without @testing-library/react installed, we verify
// behavior through the motion-config constants the component consumes.
// Render-tests can be added later when testing-library is part of the
// kiosk deps.

describe('LearningChip motion constants', () => {
  it('auto-dismiss duration is 3500ms per design Surface 5', () => {
    // Design spec: "auto-dismisses after 3.5s"
    // (design-output/README.md § Surface 5).
    expect(MOTION.learningChipMs).toBe(3500);
  });

  it('entrance animation is 320ms ease-out per design', () => {
    // Design spec: "Entry: chipIn 320ms ease-out"
    expect(MOTION.chipIn).toBeCloseTo(0.32, 2);
  });
});
