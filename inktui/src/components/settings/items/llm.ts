import type { LlmEnvWire, LlmProviderId, LlmTierWire, LlmWire } from '../../../store/settings/settingsActions.js';
import type { SettingsItem, SettingsRow } from '../types.js';
import { headerRow } from '../types.js';

/** Canonical remote adapters always appear, even before the user configures one. */
export const BUILTIN_PROVIDER_IDS = ['groq', 'cerebras', 'openrouter', 'openai', 'anthropic'] as const;
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
  for (const [name, builtin] of Object.entries(BUILTIN_TIERS)) {
    out.push([name, userTiers[name] ?? builtin]);
  }
  for (const [name, tier] of Object.entries(userTiers)) if (!(name in BUILTIN_TIERS)) out.push([name, tier]);
  return out;
}

export function tierNames(llm: LlmWire): readonly string[] {
  return mergedTiers(llm).map(([name]) => name);
}

export function llmEnvValue(env: LlmEnvWire, provider: LlmProviderId): boolean {
  return provider !== 'local' && env[provider];
}

const llmNavigationItem: SettingsItem = {
  id: 'llm.navigation',
  label: 'LLM Functionality',
  rows: ({ llm }) => {
    const configured = llm.providers ?? {};
    const customIds = Object.keys(configured).filter(
      (id) => !BUILTIN_PROVIDER_IDS.includes(id as (typeof BUILTIN_PROVIDER_IDS)[number]),
    );
    const customPolicies = Object.entries(llm.policies ?? {});
    return [
      headerRow(llmNavigationItem),
      { id: 'llm:global', kind: 'llmGlobal' },
      { id: 'llm:providers', kind: 'header', label: 'Providers' },
      ...BUILTIN_PROVIDER_IDS.map(
        (providerId): SettingsRow => ({ id: `llm:provider:${providerId}`, kind: 'llmProvider', providerId, builtin: true }),
      ),
      ...customIds.map(
        (providerId): SettingsRow => ({ id: `llm:provider:${providerId}`, kind: 'llmProvider', providerId, builtin: false }),
      ),
      { id: 'llm:add:openai-compatible', kind: 'llmAddProvider', providerType: 'openai_compatible' },
      { id: 'llm:add:lemonade', kind: 'llmAddProvider', providerType: 'lemonade' },
      { id: 'llm:policies', kind: 'header', label: 'Policies' },
      ...(['local-then-free', 'remote-free', 'local-only', 'oracle-smart'] as const).map(
        (policyId): SettingsRow => ({ id: `llm:policy:${policyId}`, kind: 'llmPolicy', policyId, builtin: true }),
      ),
      ...customPolicies.map(
        ([policyId]): SettingsRow => ({ id: `llm:policy:${policyId}`, kind: 'llmPolicy', policyId, builtin: false }),
      ),
      { id: 'llm:create-policy', kind: 'llmCreatePolicy' },
      { id: 'llm:features', kind: 'header', label: 'Feature Policies' },
      ...['crow_classification', 'transcript_summary', 'codebase_file_summary', 'codebase_rollup', 'oracle'].map(
        (feature): SettingsRow => ({ id: `llm:feature:${feature}`, kind: 'llmFeaturePolicy', feature }),
      ),
    ];
  },
};

export const LLM_ITEMS: readonly SettingsItem[] = [llmNavigationItem];
