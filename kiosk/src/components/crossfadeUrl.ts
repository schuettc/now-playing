// Cache-bust suffix for retry attempts on a failed <img> src. Attempt 0
// is the original URL; later attempts append ?v=N (or &v=N when a query
// string already exists) so the browser refetches instead of serving the
// failed-load result from cache.
export function appendAttemptSuffix(
  src: string | undefined,
  attempt: number,
): string | undefined {
  if (!src || attempt === 0) return src;
  const sep = src.includes('?') ? '&' : '?';
  return `${src}${sep}v=${attempt}`;
}
