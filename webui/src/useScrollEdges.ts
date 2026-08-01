/**
 * Track whether a scrollport has content clipped above/below — drives subtle edge fades
 * (`.mds-scroll-edges--more-above` / `--more-below`) without heavy chrome.
 */

import { useCallback, useLayoutEffect, useState, type RefObject } from 'react';

const EPSILON_PX = 2;

export type ScrollEdges = {
  readonly moreAbove: boolean;
  readonly moreBelow: boolean;
};

/** Observe scroll + size/content changes on `ref`; returns clip flags for CSS edge hints. */
export function useScrollEdges(
  ref: RefObject<HTMLElement | null>,
  /** Remeasure when content identity changes (e.g. list length, open doc). */
  contentKey?: string | number,
): ScrollEdges {
  const [moreAbove, setMoreAbove] = useState(false);
  const [moreBelow, setMoreBelow] = useState(false);

  const measure = useCallback((): void => {
    const el = ref.current;
    if (el === null) {
      setMoreAbove(false);
      setMoreBelow(false);
      return;
    }
    const { scrollTop, scrollHeight, clientHeight } = el;
    setMoreAbove(scrollTop > EPSILON_PX);
    setMoreBelow(scrollTop + clientHeight < scrollHeight - EPSILON_PX);
  }, [ref]);

  useLayoutEffect(() => {
    const el = ref.current;
    if (el === null) {
      setMoreAbove(false);
      setMoreBelow(false);
      return;
    }
    measure();
    el.addEventListener('scroll', measure, { passive: true });
    const ro =
      typeof ResizeObserver !== 'undefined' ? new ResizeObserver(() => measure()) : null;
    ro?.observe(el);
    const mo =
      typeof MutationObserver !== 'undefined'
        ? new MutationObserver(() => measure())
        : null;
    mo?.observe(el, { childList: true, subtree: true, characterData: true });
    return () => {
      el.removeEventListener('scroll', measure);
      ro?.disconnect();
      mo?.disconnect();
    };
  }, [ref, measure, contentKey]);

  return { moreAbove, moreBelow };
}

/** Class string for the two edge flags (empty when neither). */
export function scrollEdgesClassName(edges: ScrollEdges): string {
  const parts: string[] = ['mds-scroll-edges'];
  if (edges.moreAbove) parts.push('mds-scroll-edges--more-above');
  if (edges.moreBelow) parts.push('mds-scroll-edges--more-below');
  return parts.join(' ');
}
