import { describe, expect, it } from 'vitest';
import { heartbeatSummary, type HeartbeatState } from './HeartbeatSection';

const base: HeartbeatState = {
  mode: 'set', source: 'setting', last_ok_seconds_ago: 20, failures_in_a_row: 0, last_error: null,
};

describe('heartbeatSummary', () => {
  it('warns when no monitor is set up', () => {
    const s = heartbeatSummary({ ...base, mode: 'unset', source: 'none', last_ok_seconds_ago: null });
    expect(s.tone).toBe('warning');
    expect(s.text).toContain('reported by nobody');
  });

  it('does not call running without a monitor healthy', () => {
    const s = heartbeatSummary({ ...base, mode: 'off', last_ok_seconds_ago: null });
    expect(s.tone).toBe('info');
    expect(s.text).toContain('reported by nobody');
  });

  it('reports a monitor that answers', () => {
    expect(heartbeatSummary(base)).toEqual({ tone: 'ok', text: 'The monitor answered the last request 20 s ago.' });
  });

  it('waits for the first answer after a URL was saved', () => {
    expect(heartbeatSummary({ ...base, last_ok_seconds_ago: null }).tone).toBe('info');
  });

  it('shows a monitor that rejects the requests as failing, with the error', () => {
    const s = heartbeatSummary({ ...base, failures_in_a_row: 7, last_error: 'the monitor answered HTTP 404' });
    expect(s.tone).toBe('down');
    expect(s.text).toBe('The last 7 requests failed: the monitor answered HTTP 404.');
  });

  it('keeps a short fault quiet', () => {
    expect(heartbeatSummary({ ...base, failures_in_a_row: 2, last_error: 'ConnectTimeout' }).tone).toBe('ok');
  });
});
