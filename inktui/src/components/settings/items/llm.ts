import type {
  LlmEnvWire,
  LlmProviderId,
  LlmTierWire,
  LlmWire,
} from '../../../store/settings/settingsActions.js';
import type { SettingsItem, SettingsRow } from '../types.js';
import { headerRow } from '../types.js';

export const PROVIDERS: readonly LlmProviderId[] = ['groq', 'cerebras', 'openrouter', 'local'];

export const ENV_PROVIDERS: ReadonlySet<string> = new Set(['groq', 'cerebras', 'openrouter']);

export const ROLES: readonly string[] = ['notetaker', 'crow_handler', 'codebase_map'];

export const BUILTIN_TIERS: Readonly<Record<string, LlmTierWire>> = {
  cheap: { provider: 'groq', model: 'openai/gpt-oss-120b', auto_free: true },
  smart: { provider: 'cerebras', model: 'openai/gpt-oss-120b' },
};

export function mergedTiers(llm: LlmWire): Array<[string, LlmTierWire]> {
  const out: Array<[string, LlmTierWire]> = [];
  const userTiers = llm.tiers ?? {};
  for (const name of Object.keys(BUILTIN_TIERS)) {
    const tier = userTiers[name] ?? BUILTIN_TIERS[name];
    if (tier !== undefined) {
      out.push([name, tier]);
    }
  }
  for (const [name, tier] of Object.entries(userTiers)) {
    if (!(name in BUILTIN_TIERS)) {
      out.push([name, tier]);
    }
  }
  return out;
}

export function tierNames(llm: LlmWire): readonly string[] {
  return mergedTiers(llm).map(([name]) => name);
}

export function llmEnvValue(env: LlmEnvWire, provider: LlmProviderId): boolean {
  if (provider === 'local') {
    return false;
  }
  return env[provider];
}

const providersItem: SettingsItem = {
  id: 'llm.providers',
  label: 'Providers',
  rows: () => {
    const rows: SettingsRow[] = [headerRow(providersItem)];
    for (const provider of PROVIDERS) {
      rows.push({
        id: `llm.providers:${provider}:api_key`,
        kind: 'provider',
        provider,
        field: 'api_key',
      });
      if (provider === 'local') {
        rows.push({
          id: 'llm.providers:local:base_url',
          kind: 'provider',
          provider,
          field: 'base_url',
        });
      }
    }
    return rows;
  },
};

const tiersItem: SettingsItem = {
  id: 'llm.tiers',
  label: 'Tiers',
  rows: ({ llm }) => [
    headerRow(tiersItem),
    ...mergedTiers(llm).map(
      ([name]): SettingsRow => ({ id: `llm.tiers:${name}`, kind: 'tier', name }),
    ),
  ],
};

const rolesItem: SettingsItem = {
  id: 'llm.roles',
  label: 'Role-to-Tier Mappings',
  rows: ({ llm }) => {
    const choices = tierNames(llm);
    return [
      headerRow(rolesItem),
      ...ROLES.flatMap((role) =>
        choices.map(
          (tier): SettingsRow => ({
            id: `llm.roles:${role}:${tier}`,
            kind: 'role',
            role,
            tier,
          }),
        ),
      ),
    ];
  },
};

export const LLM_ITEMS: readonly SettingsItem[] = [providersItem, tiersItem, rolesItem];
