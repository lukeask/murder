/** Tiny ledger chip showing a live `g` / `g39` capture. */

export function GotoLineOverlay({
  pending,
}: {
  readonly pending: string | null;
}): React.JSX.Element | null {
  if (pending === null) return null;
  return (
    <div className="mds-goto" role="status" aria-live="polite">
      <span className="mds-goto__chord">g</span>
      <span className="mds-goto__digits">{pending.length === 0 ? '…' : pending}</span>
    </div>
  );
}
