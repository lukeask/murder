/**
 * Whether the built-in launch-oriented ``ticket`` workflow can start with configured defaults.
 *
 * Mirrors the backend ``configured_execution_defaults`` rule: a Startup Rogue harness plus a
 * resolvable model (explicit or first catalog entry) is required. When this is false, the TUI keeps
 * ``ticket.quick_create`` as the fallback for unconfigured planned tickets.
 */

import { defaultModelFor } from '../../components/settings/items/harnesses.js';
import type { SettingsState } from '../settings/settingsSlice.js';

export function canLaunchBuiltinTicketWorkflow(settings: SettingsState): boolean {
  const sr = settings.startupRogue;
  if (sr === null) return false;
  const harness = sr.harness.trim();
  if (!harness) return false;
  const model = sr.model.trim() || defaultModelFor(harness, settings.startupRogueModels);
  return model.length > 0;
}
