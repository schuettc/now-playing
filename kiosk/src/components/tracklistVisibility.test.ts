import { describe, it, expect } from 'vitest';
import type { TracklistItem } from '@/types';
import { computeTracklistVisibility } from './tracklistVisibility';

const TRACKS: TracklistItem[] = [
  { position: 'A1', title: 'Saturday Savior', side: 'A', duration_seconds: 268 },
  { position: 'A2', title: 'Sergeant Politeness', side: 'A', duration_seconds: 245 },
  { position: 'A3', title: 'Segue 1', side: 'A', duration_seconds: 114 },
  { position: 'A4', title: 'Smoking Umbrellas', side: 'A', duration_seconds: 239 },
  { position: 'B5', title: 'Pillowhead', side: 'B', duration_seconds: 130 },
  { position: 'B6', title: 'Blank', side: 'B', duration_seconds: 339 },
  { position: 'B7', title: 'Segue 2', side: 'B', duration_seconds: 77 },
  { position: 'B8', title: 'Dirty Blue Balloons', side: 'B', duration_seconds: 267 },
];

function assertBSide(result: ReturnType<typeof computeTracklistVisibility>) {
  expect(result.tracks.map((t) => t.position)).toEqual([
    'B5', 'B6', 'B7', 'B8',
  ]);
  expect(result.peekPositions.size).toBe(0);
}

describe('computeTracklistVisibility', () => {
  it('returns only the current side for a middle track', () => {
    const result = computeTracklistVisibility(TRACKS, 'A2');
    expect(result.tracks.map((t) => t.position)).toEqual([
      'A1', 'A2', 'A3', 'A4',
    ]);
    expect(result.peekPositions.size).toBe(0);
    expect(result.peekHeaderSide).toBeNull();
  });

  it('appends the next side first track when current is last on side', () => {
    const result = computeTracklistVisibility(TRACKS, 'A4');
    expect(result.tracks.map((t) => t.position)).toEqual([
      'A1', 'A2', 'A3', 'A4', 'B5',
    ]);
    expect(result.peekPositions.has('B5')).toBe(true);
    expect(result.peekHeaderSide).toBe('B');
  });

  it('no peek when last track of the last side', () => {
    const result = computeTracklistVisibility(TRACKS, 'B8');
    assertBSide(result);
    expect(result.peekHeaderSide).toBeNull();
  });

  it('first track of side B shows only side B', () => {
    const result = computeTracklistVisibility(TRACKS, 'B5');
    assertBSide(result);
  });

  it('falls back to all tracks when current position has no matching track', () => {
    const result = computeTracklistVisibility(TRACKS, 'C1');
    expect(result.tracks).toEqual(TRACKS);
    expect(result.peekPositions.size).toBe(0);
  });

  it('falls back to all tracks when current position is empty', () => {
    const result = computeTracklistVisibility(TRACKS, '');
    expect(result.tracks).toEqual(TRACKS);
    expect(result.peekPositions.size).toBe(0);
  });

  it('falls back to all tracks when current track has no side', () => {
    const sidelessTrack: TracklistItem = {
      position: '1',
      title: 'Track 1',
      side: null,
      duration_seconds: 200,
    };
    const result = computeTracklistVisibility([sidelessTrack], '1');
    expect(result.tracks).toEqual([sidelessTrack]);
    expect(result.peekPositions.size).toBe(0);
  });

  it('derives side from position[:1] when track.side is absent', () => {
    // Tracks lack the `side` field; position strings carry the side prefix.
    // Helper should still scope to the current side via position-derived side.
    const sideless: TracklistItem[] = [
      { position: 'A1', title: 'A1', side: null, duration_seconds: 100 },
      { position: 'A2', title: 'A2', side: null, duration_seconds: 100 },
      { position: 'B1', title: 'B1', side: null, duration_seconds: 100 },
    ];
    const result = computeTracklistVisibility(sideless, 'A2');
    // A2 is last on side A → peek B1.
    expect(result.tracks.map((t) => t.position)).toEqual(['A1', 'A2', 'B1']);
    expect(result.peekPositions.has('B1')).toBe(true);
    expect(result.peekHeaderSide).toBe('B');
  });

  it('handles three-side records (A → B → C)', () => {
    const threeSide: TracklistItem[] = [
      { position: 'A1', title: 'A1', side: 'A', duration_seconds: 100 },
      { position: 'A2', title: 'A2', side: 'A', duration_seconds: 100 },
      { position: 'B1', title: 'B1', side: 'B', duration_seconds: 100 },
      { position: 'B2', title: 'B2', side: 'B', duration_seconds: 100 },
      { position: 'C1', title: 'C1', side: 'C', duration_seconds: 100 },
    ];
    // Last of B → peek C1
    const result = computeTracklistVisibility(threeSide, 'B2');
    expect(result.tracks.map((t) => t.position)).toEqual(['B1', 'B2', 'C1']);
    expect(result.peekPositions.has('C1')).toBe(true);
    expect(result.peekHeaderSide).toBe('C');
  });
});
