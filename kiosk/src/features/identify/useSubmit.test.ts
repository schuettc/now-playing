/**
 * Tests for postIdentify — the pure async POST helper extracted from
 * useSubmit for testability. Verifies that onPickSuccess is called only
 * on POST success and that showToast receives the right messages.
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { postIdentify } from './useSubmit';

describe('postIdentify — onPickSuccess threading', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  const runWithFetch = async (
    fetchMock: ReturnType<typeof vi.fn>,
    opts: { includeOnPickSuccess?: boolean } = {},
  ) => {
    vi.stubGlobal('fetch', fetchMock);
    const showToast = vi.fn();
    const onPickSuccess = opts.includeOnPickSuccess === false ? undefined : vi.fn();
    const result = await postIdentify({
      releaseId: 10,
      trackPosition: 'A1',
      showToast,
      onPickSuccess,
    });
    return { result, showToast, onPickSuccess };
  };

  it('calls onPickSuccess and returns ok:true on POST success', async () => {
    const { result, showToast, onPickSuccess } = await runWithFetch(
      vi.fn().mockResolvedValue({ ok: true, status: 200 }),
    );

    expect(result).toEqual({ ok: true });
    expect(onPickSuccess).toHaveBeenCalledOnce();
    expect(showToast).toHaveBeenCalledWith('Set as playing · A1');
  });

  it('does NOT call onPickSuccess when the POST returns a non-ok status', async () => {
    const { result, showToast, onPickSuccess } = await runWithFetch(
      vi.fn().mockResolvedValue({ ok: false, status: 503 }),
    );

    expect(result).toEqual({ ok: false, error: 'HTTP 503' });
    expect(onPickSuccess).not.toHaveBeenCalled();
    expect(showToast).toHaveBeenCalledWith('Failed: HTTP 503', true);
  });

  it('does NOT call onPickSuccess when fetch rejects (network error)', async () => {
    const { result, showToast, onPickSuccess } = await runWithFetch(
      vi.fn().mockRejectedValue(new Error('Network error')),
    );

    expect(result).toEqual({ ok: false, error: 'Network error' });
    expect(onPickSuccess).not.toHaveBeenCalled();
    expect(showToast).toHaveBeenCalledWith('Failed: Network error', true);
  });

  it('works without onPickSuccess (undefined)', async () => {
    const { result, showToast } = await runWithFetch(
      vi.fn().mockResolvedValue({ ok: true, status: 200 }),
      { includeOnPickSuccess: false },
    );

    expect(result).toEqual({ ok: true });
    expect(showToast).toHaveBeenCalledWith('Set as playing · A1');
  });
});
