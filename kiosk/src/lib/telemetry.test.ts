import { afterEach, describe, expect, it, vi } from 'vitest';
import { track } from './telemetry';

describe('telemetry.track', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('emits the event name and dims via console.log', () => {
    const spy = vi.spyOn(console, 'log').mockImplementation(() => {});
    track('identify_lookup_open', {
      variant: 'unknown',
      entry: 'undo',
      scoped_to_release: true,
    });
    expect(spy).toHaveBeenCalledWith(
      '[telemetry]',
      'identify_lookup_open',
      { variant: 'unknown', entry: 'undo', scoped_to_release: true },
    );
  });

  it('uses an empty dims object when no dims are passed', () => {
    const spy = vi.spyOn(console, 'log').mockImplementation(() => {});
    track('identify_pin_timeout');
    expect(spy).toHaveBeenCalledWith('[telemetry]', 'identify_pin_timeout', {});
  });

  it('accepts all 11 event names without TS errors', () => {
    const spy = vi.spyOn(console, 'log').mockImplementation(() => {});
    track('identify_guess_shown');
    track('identify_guess_confirm');
    track('identify_guess_reject');
    track('identify_guess_pick_another');
    track('identify_guess_timeout');
    track('identify_tracklist_tap');
    track('identify_lookup_open');
    track('identify_lookup_pick');
    track('identify_lookup_dismiss');
    track('identify_pin_4xx');
    track('identify_pin_timeout');
    expect(spy).toHaveBeenCalledTimes(11);
  });
});
