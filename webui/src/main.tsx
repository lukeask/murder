/** Web entrypoint: mount {@link Boot}. */

import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { Boot } from './Boot.js';
import './styles/theme.css';
// Design-system token foundation: imported AFTER theme.css (so DS values win on overlapping names
// like --space-*/--radius/--font-mono) and BEFORE app.css (so the existing app chrome still reads
// the runtime --color-* vars unchanged). ds.css holds the ported .mds-* component rules.
import './styles/tokens.css';
import './styles/ds.css';
import './styles/ds-forms.css';
import './styles/ds-data.css';
import './styles/ds-navigation.css';
import './styles/ds-feedback.css';
import './styles/ds-sheet.css';
import './styles/app.css';
// Cockpit shell layout (the DS reskin frame) + panel CSS. Imported AFTER the ds-*.css component
// sheets (so `.mds-*` rules exist) and after app.css (so the new `.cockpit*`/`.mw-*`/`.ticket-meta*`
// shell + panel rules win where intent overlaps). Later C2 panel groups each add their own
// `panels-<group>.css` import here during integration.
import './styles/cockpit.css';
import './styles/picker.css';
import './styles/panels.css';
import './styles/panels-roster.css';
import './styles/panels-history.css';
import './styles/panels-docs.css';
import './styles/panels-usage.css';
import './styles/panels-transit.css';
import './styles/panels-settings.css';
import './styles/panels-stage.css';
import './styles/panels-workflows.css';
import './styles/panels-modes.css';

const rootEl = document.getElementById('root');
if (rootEl === null) {
  throw new Error('missing #root element');
}

createRoot(rootEl).render(
  <StrictMode>
    <Boot />
  </StrictMode>,
);
