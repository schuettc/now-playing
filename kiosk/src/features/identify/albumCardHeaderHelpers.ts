import type { CSSProperties } from 'react';

export interface HeaderStyles {
  button: string;
  textBlock: string;
  title: string;
  titleStyle: CSSProperties | undefined;
  artist: string;
  caption: string;
}

const CLAMP_STYLE: CSSProperties = {
  display: '-webkit-box',
  WebkitLineClamp: 2,
  WebkitBoxOrient: 'vertical',
  overflow: 'hidden',
};

export function buildHeaderStyles(expanded: boolean, hasTracks: boolean): HeaderStyles {
  const layout = expanded ? 'flex-row items-start gap-5 p-5 pr-16' : 'flex-col';
  const cursor = hasTracks ? 'cursor-pointer' : 'cursor-default';
  return {
    button: `flex w-full text-left ${layout} ${cursor}`,
    textBlock: `flex min-w-0 flex-1 flex-col ${expanded ? '' : 'gap-1 px-3 pb-3 pt-3'}`,
    title: `font-semibold leading-tight text-[#e9e9ee] ${expanded ? 'text-[20px]' : 'text-[15px]'}`,
    titleStyle: expanded ? undefined : CLAMP_STYLE,
    artist: `truncate text-[#8a8a95] ${expanded ? 'text-[15px]' : 'text-[13px]'}`,
    caption: `truncate text-[#6a6a73] ${expanded ? 'mt-0.5 text-[13px]' : 'text-[11px]'}`,
  };
}
