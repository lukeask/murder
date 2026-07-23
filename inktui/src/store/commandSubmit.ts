/**
 * Shared application-command helper.
 *
 * The application protocol exposes each supported command as a closed generated command name.
 * There is deliberately no generic orchestrator envelope or client-side command polling: the
 * application server owns dispatch and returns the command's typed result directly.
 */

import type { ApplicationPayload, ApplicationClient } from '../application/ApplicationClient.js';
import type { CommandName } from '../generated/applicationProtocol.js';

/**
 * Execute one generated application command.
 *
 * `ApplicationPayload` remains intentionally open while feature result DTOs are migrated; the
 * command name itself is closed by the generated public contract.
 */
export async function submitCommand(
  client: ApplicationClient,
  name: CommandName,
  payload: ApplicationPayload,
): Promise<ApplicationPayload> {
  return client.command(name, payload);
}
