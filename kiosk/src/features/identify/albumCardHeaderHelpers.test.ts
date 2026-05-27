import { describe, expect, it } from 'vitest';
import { buildHeaderStyles } from './albumCardHeaderHelpers';

describe('buildHeaderStyles', () => {
  it('uses row layout and cursor-pointer when expanded with tracks', () => {
    const s = buildHeaderStyles(true, true);
    expect(s.button).toContain('flex-row items-start gap-5 p-5 pr-16');
    expect(s.button).toContain('cursor-pointer');
    expect(s.textBlock).not.toContain('gap-1');
    expect(s.title).toContain('text-[20px]');
    expect(s.titleStyle).toBeUndefined();
    expect(s.artist).toContain('text-[15px]');
    expect(s.caption).toContain('mt-0.5');
  });

  it('uses column layout and clamp style when collapsed', () => {
    const s = buildHeaderStyles(false, true);
    expect(s.button).toContain('flex-col');
    expect(s.textBlock).toContain('gap-1 px-3 pb-3 pt-3');
    expect(s.title).toContain('text-[15px]');
    expect(s.titleStyle).toMatchObject({ WebkitLineClamp: 2 });
    expect(s.artist).toContain('text-[13px]');
    expect(s.caption).toContain('text-[11px]');
  });

  it('uses cursor-default when no tracks', () => {
    expect(buildHeaderStyles(false, false).button).toContain('cursor-default');
    expect(buildHeaderStyles(true, false).button).toContain('cursor-default');
  });
});
