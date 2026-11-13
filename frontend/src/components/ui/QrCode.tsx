import qrcode from 'qrcode-generator';

/** SVG path of a QR code's dark modules, plus its width in modules. */
export function qrModules(text: string): { path: string; count: number } {
  // Type 0 = smallest version that fits; M = 15 % error correction, the
  // usual choice for a code read from a screen.
  const qr = qrcode(0, 'M');
  qr.addData(text);
  qr.make();
  const count = qr.getModuleCount();
  let path = '';
  for (let row = 0; row < count; row += 1) {
    for (let col = 0; col < count; col += 1) {
      if (qr.isDark(row, col)) path += `M${col} ${row}h1v1h-1z`;
    }
  }
  return { path, count };
}

/**
 * A QR code as inline SVG. Always dark on white with a quiet zone, also in
 * the dark theme: scanners need the contrast and the light border.
 */
export function QrCode({ value, size = 176, label }: { value: string; size?: number; label: string }) {
  const { path, count } = qrModules(value);
  const quiet = 4;
  const box = count + quiet * 2;
  return (
    <svg
      role="img"
      aria-label={label}
      width={size}
      height={size}
      viewBox={`${-quiet} ${-quiet} ${box} ${box}`}
      shapeRendering="crispEdges"
      className="rounded-ctl"
    >
      {/* Literal black and white: a QR code is not themed. */}
      <rect x={-quiet} y={-quiet} width={box} height={box} fill="white" />
      <path d={path} fill="black" />
    </svg>
  );
}
