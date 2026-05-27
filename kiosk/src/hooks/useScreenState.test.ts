import { describe, expect, it } from 'vitest';
import type { NowPlaying } from '@/types';
import { deriveScreenState, isVinylNeedsId, showsVinylOverlay } from './useScreenState';

const base = (over: Partial<NowPlaying> = {}): NowPlaying => ({
  ts: '2026-05-15T00:00:00Z',
  state: 'PLAYING',
  source: 'vinyl',
  ...over,
});

describe('deriveScreenState', () => {
  it('returns idle when no payload', () => {
    expect(deriveScreenState(null)).toEqual({ kind: 'idle' });
  });

  it('returns idle when state is STOPPED', () => {
    expect(deriveScreenState(base({ state: 'STOPPED' }))).toEqual({
      kind: 'idle',
    });
  });

  it('NEEDS_ID takes precedence over title-less branches', () => {
    // NEEDS_ID with no title and vinyl source — would otherwise look
    // like vinyl-identifying. Must resolve to needs-id.
    const data = base({ state: 'NEEDS_ID', source: 'vinyl', title: undefined });
    const s = deriveScreenState(data);
    expect(s.kind).toBe('needs-id');
  });

  it('NEEDS_ID + vinyl: isVinylNeedsId returns true', () => {
    // NowPlayingView uses isVinylNeedsId to skip TrackSurface/TrackBackdrop
    // for vinyl NEEDS_ID payloads (null title → "Unknown Track" failure UI).
    const data = base({ state: 'NEEDS_ID', source: 'vinyl' });
    const s = deriveScreenState(data);
    expect(s.kind).toBe('needs-id');
    expect(isVinylNeedsId(s)).toBe(true);
  });

  it('NEEDS_ID + non-vinyl: isVinylNeedsId returns false', () => {
    // AirPlay NEEDS_ID must NOT route to VinylIdentifying.
    const data = base({ state: 'NEEDS_ID', source: 'airplay', title: undefined });
    const s = deriveScreenState(data);
    expect(s.kind).toBe('needs-id');
    expect(isVinylNeedsId(s)).toBe(false);
  });

  it('showsVinylOverlay: true for vinyl-identifying', () => {
    const data = base({ source: 'vinyl', title: undefined });
    const s = deriveScreenState(data);
    expect(s.kind).toBe('vinyl-identifying');
    expect(showsVinylOverlay(s)).toBe(true);
  });

  it('showsVinylOverlay: true for NEEDS_ID + vinyl', () => {
    const data = base({ state: 'NEEDS_ID', source: 'vinyl' });
    const s = deriveScreenState(data);
    expect(showsVinylOverlay(s)).toBe(true);
  });

  it('showsVinylOverlay: false for NEEDS_ID + airplay', () => {
    const data = base({ state: 'NEEDS_ID', source: 'airplay' });
    const s = deriveScreenState(data);
    expect(showsVinylOverlay(s)).toBe(false);
  });

  it('returns airplay for AirPlay source with no title', () => {
    const data = base({ source: 'airplay', title: undefined });
    const s = deriveScreenState(data);
    expect(s.kind).toBe('airplay');
  });

  it('returns vinyl-identifying for vinyl source with no title', () => {
    const data = base({ source: 'vinyl', title: undefined });
    const s = deriveScreenState(data);
    expect(s.kind).toBe('vinyl-identifying');
  });

  it('returns track for a vinyl payload with a confirmed match', () => {
    // Track surface only renders for vinyl when match_method is in the
    // confirmed whitelist (or 'predicted'). A title alone is not enough.
    const data = base({ source: 'vinyl', title: 'Pitiful', match_method: 'fingerprint' });
    const s = deriveScreenState(data);
    expect(s).toMatchObject({ kind: 'track', isPaused: false });
  });

  it('returns vinyl-identifying for unmatched vinyl even when title is populated', () => {
    // Closes the "Unknown Track / Unknown Artist / NO ART" failure UI:
    // any vinyl payload without a real recognition routes to the
    // identifying screen, not the track surface.
    const data = base({ source: 'vinyl', title: 'Pitiful', match_method: 'unmatched' });
    const s = deriveScreenState(data);
    expect(s.kind).toBe('vinyl-identifying');
  });

  it('returns vinyl-identifying for vinyl with no match_method (cold-start)', () => {
    // Cold-start window: orchestrator publishes vinyl payload before
    // any recognition has landed. Must route to identifying.
    const data = base({ source: 'vinyl', title: 'Pitiful' });
    const s = deriveScreenState(data);
    expect(s.kind).toBe('vinyl-identifying');
  });

  it('returns track for match_method=predicted (predicted-advance unchanged)', () => {
    // Regression guard: this gate explicitly preserves predicted-advance
    // render behavior pending a separate evaluation. If you change
    // predicted to route to identifying, update this test deliberately.
    const data = base({ source: 'vinyl', title: 'Leo', match_method: 'predicted' });
    const s = deriveScreenState(data);
    expect(s).toMatchObject({ kind: 'track', isPaused: false });
  });

  it('marks streaming PAUSED_PLAYBACK as paused', () => {
    const data = base({
      source: 'streaming',
      state: 'PAUSED_PLAYBACK',
      title: 'Song',
    });
    const s = deriveScreenState(data);
    expect(s).toMatchObject({ kind: 'track', isPaused: true });
  });

  it('marks streaming PAUSED as paused', () => {
    const data = base({
      source: 'streaming',
      state: 'PAUSED',
      title: 'Song',
    });
    const s = deriveScreenState(data);
    expect(s).toMatchObject({ kind: 'track', isPaused: true });
  });

  it('excludes vinyl from paused even when state is PAUSED_PLAYBACK', () => {
    // Vinyl never enters a meaningful "paused" state — needle-up goes
    // silence → idle timer → STOPPED, not PAUSED_PLAYBACK. If the
    // orchestrator ever surfaces such a state on vinyl, it must NOT
    // dim the art.
    const data = base({
      source: 'vinyl',
      state: 'PAUSED_PLAYBACK',
      title: 'Song',
      match_method: 'fingerprint',
    });
    const s = deriveScreenState(data);
    expect(s).toMatchObject({ kind: 'track', isPaused: false });
  });

  it('does not mark TRANSITIONING as paused', () => {
    // TRANSITIONING is excluded to avoid flicker during the brief
    // play↔pause UPnP transition.
    const data = base({
      source: 'streaming',
      state: 'TRANSITIONING',
      title: 'Song',
    });
    const s = deriveScreenState(data);
    expect(s).toMatchObject({ kind: 'track', isPaused: false });
  });
});
