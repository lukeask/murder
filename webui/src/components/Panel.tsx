/**
 * Panel — the DOM analog of inktui's {@link Pane}: a bordered, titled container with a focus-colored
 * border and a scrollable body. Every domain screen wraps its content in a Panel so the chrome is
 * uniform and lives in ONE place. Styling is entirely in app.css (`.panel*`); this component only
 * supplies structure + the `data-focused` hook the CSS keys its focus border off.
 *
 * Native browser scroll replaces inktui's manual overflow windowing — the body just scrolls. The
 * selection/cursor model is kept (panels track a selected id), but we never window rows by hand.
 */

import type { ReactNode } from 'react';

export function Panel({
  title,
  actions,
  children,
  bodyClassName,
}: {
  readonly title: string;
  /** Optional right-aligned header controls (e.g. a mode toggle). */
  readonly actions?: ReactNode;
  readonly children: ReactNode;
  readonly bodyClassName?: string;
}): React.JSX.Element {
  return (
    <section className="panel">
      <header className="panel__header">
        <h2 className="panel__title">{title}</h2>
        {actions !== undefined ? <div className="panel__actions">{actions}</div> : null}
      </header>
      <div className={bodyClassName !== undefined ? `panel__body ${bodyClassName}` : 'panel__body'}>
        {children}
      </div>
    </section>
  );
}
