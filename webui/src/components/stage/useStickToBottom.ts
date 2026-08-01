/**
 * Stick-to-bottom scroll: follow new content only when the user is already near the bottom.
 * Mirrors TUI TranscriptController's wasNearBottomRef / CHAT_NEAR_BOTTOM_THRESHOLD behavior.
 */

import { useCallback, useEffect, useRef, type RefObject } from 'react';

const DEFAULT_NEAR_PX = 48;

export function useStickToBottom(
  containerRef: RefObject<HTMLElement | null>,
  /** Bump when content grows (e.g. turns.length). */
  contentKey: number | string,
  nearPx: number = DEFAULT_NEAR_PX,
): { readonly onScroll: () => void } {
  const stickRef = useRef(true);

  const onScroll = useCallback((): void => {
    const el = containerRef.current;
    if (el === null) return;
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    stickRef.current = distance <= nearPx;
  }, [containerRef, nearPx]);

  useEffect(() => {
    const el = containerRef.current;
    if (el === null || !stickRef.current) return;
    el.scrollTop = el.scrollHeight;
  }, [containerRef, contentKey]);

  return { onScroll };
}
