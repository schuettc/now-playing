import { describe, expect, it } from 'vitest';
import { KEY_ROWS, applyKey } from './VirtualKeyboard';

describe('applyKey', () => {
  it('appends a printable char', () => {
    expect(applyKey('ab', 'c')).toBe('abc');
    expect(applyKey('', 'q')).toBe('q');
  });

  it("appends a space for 'space'", () => {
    expect(applyKey('ab', 'space')).toBe('ab ');
  });

  it("removes the last char for 'backspace'", () => {
    expect(applyKey('abc', 'backspace')).toBe('ab');
  });

  it("'backspace' on empty string stays empty", () => {
    expect(applyKey('', 'backspace')).toBe('');
  });

  it("'clear' empties the value", () => {
    expect(applyKey('abc', 'clear')).toBe('');
  });

  it("'done' leaves the value unchanged", () => {
    expect(applyKey('abc', 'done')).toBe('abc');
  });
});

describe('KEY_ROWS', () => {
  it('has only non-empty rows', () => {
    expect(KEY_ROWS.length).toBeGreaterThan(0);
    for (const row of KEY_ROWS) expect(row.length).toBeGreaterThan(0);
  });

  it('has a digit row with 10 entries', () => {
    expect(KEY_ROWS[0]).toEqual(['1', '2', '3', '4', '5', '6', '7', '8', '9', '0']);
  });

  it('contains only single-char printable entries', () => {
    for (const row of KEY_ROWS) {
      for (const key of row) expect(key).toHaveLength(1);
    }
  });
});
