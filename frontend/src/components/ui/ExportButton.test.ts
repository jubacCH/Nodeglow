import { describe, expect, it } from 'vitest';
import { csvCell, toCSV } from './ExportButton';

describe('CSV export', () => {
  it('keeps cells a spreadsheet would run as a formula as text', () => {
    // Hostnames and syslog messages come from the network.
    expect(csvCell('=HYPERLINK("http://evil.test","x")')).toBe(`"'=HYPERLINK(""http://evil.test"",""x"")"`);
    expect(csvCell('+cmd')).toBe(`"'+cmd"`);
    expect(csvCell('-2+3')).toBe(`"'-2+3"`);
    expect(csvCell('@SUM(A1)')).toBe(`"'@SUM(A1)"`);
  });

  it('leaves ordinary values and numbers alone', () => {
    expect(csvCell('srv-01')).toBe('"srv-01"');
    expect(csvCell(-12.5)).toBe('"-12.5"');
    expect(csvCell(null)).toBe('');
    expect(csvCell('say "hi"')).toBe('"say ""hi"""');
  });

  it('writes header and rows', () => {
    const csv = toCSV([{ name: 'a', msg: '=1+1' }], [{ key: 'name', label: 'Name' }, { key: 'msg', label: 'Message' }]);
    expect(csv).toBe(`"Name","Message"\n"a","'=1+1"`);
  });
});
