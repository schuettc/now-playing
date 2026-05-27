import type { SearchRelease } from './types';

export function buildCaptionParts(rel: SearchRelease): string[] {
  return [
    rel.year ? String(rel.year) : null,
    rel.label || null,
    rel.catno || null,
  ].filter((p): p is string => Boolean(p));
}
