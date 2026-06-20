/**
 * Whether ink-testing-library color assertions should run. Matches chalk's FORCE_COLOR parsing:
 * unset/0/false → off; level must be 3 because probes assert truecolor `48;2;…` SGR sequences.
 */
export function inkTestColorOn(): boolean {
  // biome-ignore lint/complexity/useLiteralKeys: tsc's noPropertyAccessFromIndexSignature requires bracket access on process.env.
  const fc = process.env['FORCE_COLOR'];
  if (fc === undefined || fc === '0' || fc === 'false') {
    return false;
  }
  const level = fc.length === 0 ? 1 : Math.min(Number.parseInt(fc, 10) || 0, 3);
  return level >= 3;
}
