import { describe, expect, it } from 'vitest';
import { pickDominantColor, scorePixel } from './dominantColor';

describe('scorePixel', () => {
  it('rejects near-black', () => {
    expect(scorePixel(10, 10, 10)).toBe(-1);
  });

  it('rejects near-white', () => {
    expect(scorePixel(250, 250, 250)).toBe(-1);
  });

  it('returns 0 for mid-grey (zero saturation)', () => {
    expect(scorePixel(128, 128, 128)).toBe(0);
  });

  it('handles max === 0 without NaN (lightness guard catches it)', () => {
    expect(scorePixel(0, 0, 0)).toBe(-1);
  });

  it('scores saturated mid-lightness color highly', () => {
    const score = scorePixel(200, 50, 50);
    expect(score).toBeGreaterThan(0.5);
  });

  it('penalizes off-center lightness', () => {
    const mid = scorePixel(200, 50, 50);
    const dark = scorePixel(100, 0, 0);
    expect(mid).toBeGreaterThan(dark);
  });
});

describe('pickDominantColor', () => {
  it('returns null on empty data', () => {
    expect(pickDominantColor(new Uint8ClampedArray(0))).toBeNull();
  });

  it('returns null when all pixels fail lightness guard', () => {
    const data = new Uint8ClampedArray([0, 0, 0, 255, 255, 255, 255, 255]);
    expect(pickDominantColor(data)).toBeNull();
  });

  it('picks the highest-scoring pixel', () => {
    const data = new Uint8ClampedArray([
      128, 128, 128, 255,
      200, 50, 50, 255,
      100, 100, 100, 255,
    ]);
    expect(pickDominantColor(data)).toEqual({ r: 200, g: 50, b: 50 });
  });
});
