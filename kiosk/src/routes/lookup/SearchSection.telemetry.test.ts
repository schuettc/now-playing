/**
 * Telemetry decision tests for SearchSection's toggleExpanded override.
 *
 * SearchSection uses `resolveToggleExpanded` to preview the action
 * decision before delegating to baseActions.toggleExpanded, so it can
 * fire `identify_lookup_pick` with the correct `picked_album` value.
 *
 * These tests verify the decision logic that determines WHEN the
 * album-pick telemetry fires — i.e., only on `{ kind: 'submit' }`.
 * The telemetry call itself is covered separately (it delegates to
 * `console.log` via the shim; we verify the decision, not the side effect).
 *
 * @testing-library/react is not installed; we test the pure decision
 * logic extracted from the toggleExpanded override.
 */
import { describe, expect, it } from 'vitest';
import { resolveToggleExpanded } from '@/features/identify/identifyActionHelpers';
import type { SearchResponse } from '@/features/identify/types';

const searchResults: SearchResponse = {
  items: [
    {
      release_id: 10,
      artist: 'Godspeed You! Black Emperor',
      title: 'Lift Your Skinny Fists',
      tracks: [
        { position: 'A1', title: 'Storm' },
        { position: 'B1', title: 'Static' },
      ],
    },
  ],
  groups: [],
};

describe('toggleExpanded album-pick telemetry decision', () => {
  it('resolves to submit when albumPickTrackTitle matches a track — album pick fires', () => {
    const decision = resolveToggleExpanded({
      releaseId: 10,
      searchResults,
      albumPickTrackTitle: 'storm',
      expandedReleaseId: null,
      isSubmitting: false,
    });
    // SearchSection fires identify_lookup_pick with picked_album:true only here
    expect(decision.kind).toBe('submit');
    expect(decision).toMatchObject({ kind: 'submit', releaseId: 10, position: 'A1' });
  });

  it('resolves to expand when no track title match — no pick telemetry fires', () => {
    const decision = resolveToggleExpanded({
      releaseId: 10,
      searchResults,
      albumPickTrackTitle: 'unknown song',
      expandedReleaseId: null,
      isSubmitting: false,
    });
    expect(decision.kind).toBe('expand');
  });

  it('resolves to expand with null albumPickTrackTitle — no pick telemetry fires', () => {
    const decision = resolveToggleExpanded({
      releaseId: 10,
      searchResults,
      albumPickTrackTitle: null,
      expandedReleaseId: null,
      isSubmitting: false,
    });
    expect(decision.kind).toBe('expand');
  });

  it('resolves to collapse when tapping expanded release — no pick telemetry fires', () => {
    const decision = resolveToggleExpanded({
      releaseId: 10,
      searchResults,
      albumPickTrackTitle: null,
      expandedReleaseId: 10,
      isSubmitting: false,
    });
    expect(decision.kind).toBe('collapse');
  });

  it('skips album-pick submit while submitting — no pick telemetry fires', () => {
    const decision = resolveToggleExpanded({
      releaseId: 10,
      searchResults,
      albumPickTrackTitle: 'storm',
      expandedReleaseId: null,
      isSubmitting: true,
    });
    // Guard: isSubmitting prevents the submit shortcut
    expect(decision.kind).toBe('expand');
  });
});

