/**
 * ink-mouse hit-tests every registered handler whose bounds contain the click — there is no
 * stopPropagation. List-row handlers {@link claimMouseClick claim} the event so deferred pane-focus
 * handlers can skip and leave focus with the row action (open doc / ticket / transcript).
 */

const claimedClicks = new WeakSet<object>();

/** Mark a mouse event as handled by a nested target (e.g. a Ledger row). */
export function claimMouseClick(event: object): void {
  claimedClicks.add(event);
}

/** True when a nested handler already claimed this click. */
export function wasMouseClickClaimed(event: object): boolean {
  return claimedClicks.has(event);
}
