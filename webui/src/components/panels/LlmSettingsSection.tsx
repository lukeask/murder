/**
 * LLM settings — global enable, providers (CRUD + model catalog), policy groups (JSON editor /
 * create / clone / delete), tiers & role bindings, active + per-feature policies, and resolution
 * preview. Persists via `actions.settings.llm.*` and `actions.settings.update` (mirrors inktui).
 */

import { useAppStore } from '@murder/ui-core/hooks/useAppStore.js';
import {
  BUILTIN_PROVIDER_IDS,
  ENV_PROVIDERS,
  mergedTiers,
  ROLES,
  tierNames,
} from '@murder/ui-core/components/settings/items/llm.js';
import type {
  LlmModelOverrideWire,
  LlmProviderWire,
  LlmResolutionPreview,
} from '@murder/ui-core/store/settings/settingsActions.js';
import { useState } from 'react';
import { shallow } from 'zustand/shallow';
import { Button, Input, Radio, Select, Switch } from '../ds/index.js';

const BUILTIN_POLICY_IDS = [
  'local-then-free',
  'remote-free',
  'local-only',
  'oracle-smart',
] as const;

const FEATURE_TYPES = [
  'crow_classification',
  'transcript_summary',
  'codebase_file_summary',
  'codebase_rollup',
  'oracle',
] as const;

const AUTH_SOURCES = ['none', 'environment', 'key'] as const;
type AuthSource = (typeof AUTH_SOURCES)[number];

const MODEL_SOURCES = ['recommended', 'discovered', 'custom'] as const;
type ModelSource = (typeof MODEL_SOURCES)[number];

const ADD_PROVIDER_TYPES = [
  { value: 'openai_compatible', label: 'OpenAI-compatible' },
  { value: 'lemonade', label: 'Lemonade' },
] as const;

interface ProviderDraft {
  name: string;
  endpoint: string;
  authSource: AuthSource;
  apiKey: string;
  source: ModelSource;
  include: string;
  exclude: string;
  overrides: string;
}

interface PolicyDraft {
  policyId: string | null;
  name: string;
  groups: string;
}

function emptyDraft(): ProviderDraft {
  return {
    name: '',
    endpoint: '',
    authSource: 'environment',
    apiKey: '',
    source: 'recommended',
    include: '',
    exclude: '',
    overrides: '{}',
  };
}

function emptyPolicyDraft(): PolicyDraft {
  return { policyId: null, name: '', groups: '[]' };
}

function draftFromProvider(provider: LlmProviderWire | undefined, fallbackName: string): ProviderDraft {
  const authSource: AuthSource =
    provider?.auth?.source === 'none' ||
    provider?.auth?.source === 'environment' ||
    provider?.auth?.source === 'key'
      ? provider.auth.source
      : provider?.auth?.api_key || provider?.api_key
        ? 'key'
        : 'environment';
  const source: ModelSource =
    provider?.models?.source === 'discovered' || provider?.models?.source === 'custom'
      ? provider.models.source
      : 'recommended';
  return {
    name: provider?.name ?? fallbackName,
    endpoint: provider?.endpoint ?? '',
    authSource,
    apiKey: '',
    source,
    include: (provider?.models?.include ?? []).join(', '),
    exclude: (provider?.models?.exclude ?? []).join(', '),
    overrides: JSON.stringify(provider?.models?.overrides ?? {}, null, 2),
  };
}

function parseModelList(value: string): string[] {
  return [
    ...new Set(
      value
        .split(',')
        .map((model) => model.trim())
        .filter(Boolean),
    ),
  ];
}

function effectiveCatalogPreview(include: string, exclude: string, overrides: string): string {
  const enabled = parseModelList(include).filter(
    (model) => !parseModelList(exclude).includes(model),
  );
  let overrideCount = 0;
  try {
    overrideCount = Object.keys(JSON.parse(overrides || '{}') as object).length;
  } catch {
    return 'Effective catalog: overrides JSON is invalid';
  }
  return `Effective catalog: ${enabled.length ? enabled.join(', ') : 'source models'}${
    overrideCount ? `; ${overrideCount} override${overrideCount === 1 ? '' : 's'}` : ''
  }`;
}

function providerPatch(draft: ProviderDraft): Record<string, unknown> | string {
  let overrides: Record<string, LlmModelOverrideWire>;
  try {
    const parsed: unknown = JSON.parse(draft.overrides || '{}');
    if (parsed === null || Array.isArray(parsed) || typeof parsed !== 'object') {
      throw new Error();
    }
    overrides = parsed as Record<string, LlmModelOverrideWire>;
  } catch {
    return 'Model overrides must be a JSON object';
  }
  return {
    name: draft.name.trim(),
    endpoint: draft.endpoint.trim(),
    auth: {
      source: draft.authSource,
      // "***" = leave unchanged (masked key on wire); empty clears when source is key.
      api_key:
        draft.authSource === 'key'
          ? draft.apiKey === ''
            ? '***'
            : draft.apiKey
          : '***',
    },
    models: {
      source: draft.source,
      include: parseModelList(draft.include),
      exclude: parseModelList(draft.exclude),
      overrides,
    },
  };
}

function featureLabel(feature: string): string {
  return feature.replaceAll('_', ' ');
}

function policyChoices(customPolicyIds: readonly string[]): { value: string; label: string }[] {
  const ids = [...BUILTIN_POLICY_IDS, ...customPolicyIds];
  return ids.map((id) => ({ value: id, label: id }));
}

function cloneName(policyId: string): string {
  return `${policyId.replaceAll('-', ' ')} copy`;
}

export function LlmSettingsSection(): React.JSX.Element {
  const llm = useAppStore((s) => s.settings.llm, shallow);
  const llmEnv = useAppStore((s) => s.settings.llmEnv, shallow);
  const update = useAppStore((s) => s.actions.settings.update);
  const loadSettings = useAppStore((s) => s.actions.settings.load);
  const llmActions = useAppStore((s) => s.actions.settings.llm);

  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState<ProviderDraft>(emptyDraft);
  const [formError, setFormError] = useState<string | null>(null);
  const [discoverBusy, setDiscoverBusy] = useState(false);

  const [addType, setAddType] = useState<(typeof ADD_PROVIDER_TYPES)[number]['value']>(
    'openai_compatible',
  );
  const [addDraft, setAddDraft] = useState<ProviderDraft>(emptyDraft);
  const [addError, setAddError] = useState<string | null>(null);

  const [policyDraft, setPolicyDraft] = useState<PolicyDraft>(emptyPolicyDraft);
  const [policyError, setPolicyError] = useState<string | null>(null);
  const [policyOpen, setPolicyOpen] = useState(false);

  const [previewFeature, setPreviewFeature] = useState<(typeof FEATURE_TYPES)[number]>(
    'crow_classification',
  );
  const [previewBusy, setPreviewBusy] = useState(false);
  const [previewResult, setPreviewResult] = useState<LlmResolutionPreview | null>(null);

  const providers = llm.providers ?? {};
  const customIds = Object.keys(providers).filter(
    (id) => !BUILTIN_PROVIDER_IDS.includes(id as (typeof BUILTIN_PROVIDER_IDS)[number]),
  );
  const customPolicies = Object.entries(llm.policies ?? {});
  const customPolicyIds = customPolicies.map(([id]) => id);
  const featurePolicyOptions = policyChoices(customPolicyIds);
  const activePolicyOptions = [
    ...BUILTIN_POLICY_IDS.map((id) => ({ value: id, label: `${id} (built-in)` })),
    ...customPolicies.map(([id, policy]) => ({
      value: id,
      label: policy.name ?? id,
    })),
  ];

  const llmEnabled = llm.disabled !== true;
  const editingProvider = editingId !== null ? providers[editingId] : undefined;

  const openEdit = (providerId: string, builtin: boolean): void => {
    const provider = providers[providerId];
    setEditingId(providerId);
    setDraft(
      draftFromProvider(
        provider,
        builtin ? providerId : (provider?.name ?? providerId.replaceAll('-', ' ')),
      ),
    );
    setFormError(null);
  };

  const cancelEdit = (): void => {
    setEditingId(null);
    setDraft(emptyDraft());
    setFormError(null);
  };

  const toggleProvider = (providerId: string): void => {
    const existing = providers[providerId];
    void llmActions.updateProvider(providerId, { enabled: !(existing?.enabled ?? false) });
  };

  const saveEdit = (): void => {
    if (editingId === null) {
      return;
    }
    if (draft.name.trim() === '') {
      setFormError('Provider name is required');
      return;
    }
    const patch = providerPatch(draft);
    if (typeof patch === 'string') {
      setFormError(patch);
      return;
    }
    setFormError(null);
    void llmActions.updateProvider(editingId, patch).then(() => {
      cancelEdit();
    });
  };

  const removeCustom = (providerId: string): void => {
    void llmActions.deleteProvider(providerId).then(() => {
      if (editingId === providerId) {
        cancelEdit();
      }
    });
  };

  const discoverModels = (): void => {
    if (editingId === null || discoverBusy) {
      return;
    }
    setDiscoverBusy(true);
    void llmActions
      .discoverModels(editingId)
      .then(() => loadSettings())
      .finally(() => setDiscoverBusy(false));
  };

  const submitAdd = (): void => {
    if (addDraft.name.trim() === '' || addDraft.endpoint.trim() === '') {
      setAddError('Name and endpoint are required');
      return;
    }
    setAddError(null);
    void llmActions
      .createProvider({
        type: addType,
        enabled: false,
        name: addDraft.name.trim(),
        endpoint: addDraft.endpoint.trim(),
        // On create, omit api_key when unset — do not send "***".
        auth: {
          source: addDraft.authSource,
          ...(addDraft.authSource === 'key' && addDraft.apiKey !== ''
            ? { api_key: addDraft.apiKey }
            : {}),
        },
      })
      .then((id) => {
        if (id !== null) {
          setAddDraft(emptyDraft());
          setAddType('openai_compatible');
        }
      });
  };

  const setFeaturePolicy = (feature: string, policyId: string): void => {
    void update({ llm: { feature_policies: { [feature]: policyId } } });
  };

  const selectRole = (role: string, tier: string): void => {
    void update({ llm: { roles: { [role]: tier } } });
  };

  const openCreatePolicy = (): void => {
    setPolicyDraft(emptyPolicyDraft());
    setPolicyError(null);
    setPolicyOpen(true);
  };

  const openEditPolicy = (policyId: string): void => {
    const policy = llm.policies?.[policyId];
    setPolicyDraft({
      policyId,
      name: policy?.name ?? policyId,
      groups: JSON.stringify(policy?.groups ?? [], null, 2),
    });
    setPolicyError(null);
    setPolicyOpen(true);
  };

  const cancelPolicy = (): void => {
    setPolicyDraft(emptyPolicyDraft());
    setPolicyError(null);
    setPolicyOpen(false);
  };

  const savePolicy = (): void => {
    if (policyDraft.name.trim() === '') {
      setPolicyError('Policy name is required');
      return;
    }
    let groups: unknown;
    try {
      groups = JSON.parse(policyDraft.groups);
      if (!Array.isArray(groups)) {
        throw new Error('groups must be an array');
      }
    } catch {
      setPolicyError('Groups must be valid JSON array');
      return;
    }
    setPolicyError(null);
    if (policyDraft.policyId === null) {
      void llmActions.createPolicy(policyDraft.name.trim(), { groups }).then((id) => {
        if (id !== null) {
          cancelPolicy();
        }
      });
    } else {
      void llmActions
        .updatePolicy(policyDraft.policyId, {
          name: policyDraft.name.trim(),
          groups,
        })
        .then(() => {
          cancelPolicy();
        });
    }
  };

  const deletePolicy = (policyId: string): void => {
    const references = Object.entries(llm.feature_policies ?? {})
      .filter(([, id]) => id === policyId)
      .map(([feature]) => featureLabel(feature));
    if (references.length > 0) {
      setPolicyError(`Policy is used by: ${references.join(', ')}`);
      setPolicyOpen(true);
      setPolicyDraft({
        policyId,
        name: llm.policies?.[policyId]?.name ?? policyId,
        groups: JSON.stringify(llm.policies?.[policyId]?.groups ?? [], null, 2),
      });
      return;
    }
    void llmActions.deletePolicy(policyId).then(() => {
      if (policyDraft.policyId === policyId) {
        cancelPolicy();
      }
    });
  };

  const clonePolicy = (policyId: string): void => {
    void llmActions.clonePolicy(policyId, cloneName(policyId));
  };

  const runPreview = (): void => {
    setPreviewBusy(true);
    void llmActions
      .previewResolution(previewFeature)
      .then((result) => {
        setPreviewResult(result);
      })
      .finally(() => setPreviewBusy(false));
  };

  const tiers = mergedTiers(llm);
  const roleTier = tierNames(llm);

  const renderProviderRow = (providerId: string, builtin: boolean): React.JSX.Element => {
    const provider = providers[providerId];
    const enabled = provider?.enabled ?? false;
    const label = provider?.name ?? providerId.replaceAll('-', ' ');
    const viaEnv = ENV_PROVIDERS.has(providerId) && Boolean(llmEnv[providerId as keyof typeof llmEnv]);
    const keySet = Boolean(provider?.auth?.api_key ?? provider?.api_key);
    const status = viaEnv ? 'env key' : keySet || provider !== undefined ? 'configured' : 'unset';
    const isEditing = editingId === providerId;

    return (
      <li key={providerId} className="settings__provider-block">
        <div className="settings__provider-item">
          <Switch
            label={builtin ? providerId : label}
            checked={enabled}
            onChange={() => toggleProvider(providerId)}
          />
          <span className="settings__value">{builtin ? status : 'custom'}</span>
          <Button
            type="button"
            size="sm"
            onClick={() => (isEditing ? cancelEdit() : openEdit(providerId, builtin))}
          >
            {isEditing ? `close ${providerId}` : `edit ${providerId}`}
          </Button>
          {!builtin ? (
            <Button type="button" size="sm" onClick={() => removeCustom(providerId)}>
              {`remove ${providerId}`}
            </Button>
          ) : null}
        </div>
        {isEditing ? (
          <div className="settings__provider-form">
            <Input
              label="name"
              value={draft.name}
              onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
            />
            <Input
              label="endpoint"
              value={draft.endpoint}
              onChange={(e) => setDraft((d) => ({ ...d, endpoint: e.target.value }))}
              {...(builtin ? { placeholder: 'default' } : {})}
            />
            <Select
              label="credential source"
              options={AUTH_SOURCES.map((s) => ({ value: s, label: s }))}
              value={draft.authSource}
              onChange={(e) =>
                setDraft((d) => ({ ...d, authSource: e.target.value as AuthSource }))
              }
            />
            {draft.authSource === 'key' ? (
              <Input
                label="api key"
                type="password"
                value={draft.apiKey}
                onChange={(e) => setDraft((d) => ({ ...d, apiKey: e.target.value }))}
                {...(keySet
                  ? {
                      hint: 'Leave blank to keep the stored key; type a new value to replace.',
                    }
                  : {})}
              />
            ) : null}

            <h5 className="settings__subheading">model catalog</h5>
            <Select
              label="models source"
              options={MODEL_SOURCES.map((s) => ({ value: s, label: s }))}
              value={draft.source}
              onChange={(e) =>
                setDraft((d) => ({ ...d, source: e.target.value as ModelSource }))
              }
            />
            <Input
              label="include models"
              value={draft.include}
              onChange={(e) => setDraft((d) => ({ ...d, include: e.target.value }))}
              hint="Comma-separated model ids"
            />
            <Input
              label="exclude models"
              value={draft.exclude}
              onChange={(e) => setDraft((d) => ({ ...d, exclude: e.target.value }))}
              hint="Comma-separated model ids"
            />
            <Input
              multiline
              rows={4}
              label="model overrides JSON"
              value={draft.overrides}
              onChange={(e) => setDraft((d) => ({ ...d, overrides: e.target.value }))}
            />
            <p className="settings__hint">
              {effectiveCatalogPreview(draft.include, draft.exclude, draft.overrides)}
            </p>
            <p className="settings__hint">
              {editingProvider?.models?.discovery_error
                ? `Catalog refresh error: ${editingProvider.models.discovery_error}`
                : `Available models: ${
                    (editingProvider?.models?.discovered ?? []).length
                      ? (editingProvider?.models?.discovered ?? []).join(', ')
                      : 'not fetched yet'
                  }`}
            </p>
            <div className="settings__inline">
              <Button type="button" onClick={discoverModels} disabled={discoverBusy}>
                {discoverBusy ? 'Discovering…' : 'Discover models'}
              </Button>
            </div>

            {formError !== null ? (
              <p className="settings__hint settings__hint--error">{formError}</p>
            ) : null}
            <div className="settings__inline">
              <Button type="button" onClick={saveEdit}>
                Save provider
              </Button>
              <Button type="button" onClick={cancelEdit}>
                Cancel
              </Button>
            </div>
          </div>
        ) : null}
      </li>
    );
  };

  return (
    <section className="settings__group">
      <h3 className="settings__heading">LLM functionality</h3>
      <Switch
        label="enable LLM features"
        checked={llmEnabled}
        onChange={(e) => void llmActions.setDisabled(!e.target.checked)}
      />

      <h4 className="settings__subheading">providers</h4>
      <ul className="settings__provider-list">
        {BUILTIN_PROVIDER_IDS.map((id) => renderProviderRow(id, true))}
        {customIds.map((id) => renderProviderRow(id, false))}
      </ul>

      <h4 className="settings__subheading">add provider</h4>
      <div className="settings__provider-form">
        <Select
          label="type"
          options={[...ADD_PROVIDER_TYPES]}
          value={addType}
          onChange={(e) =>
            setAddType(e.target.value as (typeof ADD_PROVIDER_TYPES)[number]['value'])
          }
        />
        <Input
          label="name"
          value={addDraft.name}
          onChange={(e) => setAddDraft((d) => ({ ...d, name: e.target.value }))}
        />
        <Input
          label="endpoint"
          value={addDraft.endpoint}
          onChange={(e) => setAddDraft((d) => ({ ...d, endpoint: e.target.value }))}
        />
        <Select
          label="credential source"
          options={AUTH_SOURCES.map((s) => ({ value: s, label: s }))}
          value={addDraft.authSource}
          onChange={(e) =>
            setAddDraft((d) => ({ ...d, authSource: e.target.value as AuthSource }))
          }
        />
        {addDraft.authSource === 'key' ? (
          <Input
            label="api key"
            type="password"
            value={addDraft.apiKey}
            onChange={(e) => setAddDraft((d) => ({ ...d, apiKey: e.target.value }))}
          />
        ) : null}
        {addError !== null ? (
          <p className="settings__hint settings__hint--error">{addError}</p>
        ) : null}
        <Button type="button" onClick={submitAdd}>
          Add provider
        </Button>
      </div>

      <h4 className="settings__subheading">tiers</h4>
      <ul className="settings__provider-list">
        {tiers.map(([name, tier]) => (
          <li key={name} className="settings__provider-item">
            <span className="settings__value">
              {name} → {tier.provider}/{tier.model}
              {tier.auto_free ? ' (auto-free)' : ''}
            </span>
          </li>
        ))}
      </ul>

      <h4 className="settings__subheading">role bindings</h4>
      <div className="settings__bindings">
        {ROLES.map((role) => {
          const mapped = llm.roles?.[role];
          return (
            <div key={role} className="settings__role-binding">
              <span className="settings__binding-label">{role}</span>
              <Radio
                aria-label={`${role} tier`}
                options={roleTier.map((tier) => ({ value: tier, label: tier }))}
                {...(mapped !== undefined ? { value: mapped } : {})}
                onChange={(tier) => selectRole(role, tier)}
              />
              {mapped === undefined ? (
                <p className="settings__hint">no mapping yet → default</p>
              ) : null}
            </div>
          );
        })}
      </div>

      <h4 className="settings__subheading">active policy</h4>
      <Select
        label="active policy"
        options={activePolicyOptions}
        value={llm.active_policy ?? 'local-then-free'}
        onChange={(e) => void llmActions.activatePolicy(e.target.value)}
      />

      <h4 className="settings__subheading">policies</h4>
      <ul className="settings__provider-list">
        {BUILTIN_POLICY_IDS.map((id) => (
          <li key={id} className="settings__provider-item">
            <span className="settings__value">{id} (built-in)</span>
            <Button type="button" size="sm" onClick={() => clonePolicy(id)}>
              {`clone ${id}`}
            </Button>
          </li>
        ))}
        {customPolicies.map(([id, policy]) => (
          <li key={id} className="settings__provider-item">
            <span className="settings__value">{policy.name ?? id}</span>
            <Button type="button" size="sm" onClick={() => openEditPolicy(id)}>
              {`edit policy ${id}`}
            </Button>
            <Button type="button" size="sm" onClick={() => clonePolicy(id)}>
              {`clone ${id}`}
            </Button>
            <Button type="button" size="sm" onClick={() => deletePolicy(id)}>
              {`delete ${id}`}
            </Button>
          </li>
        ))}
      </ul>
      <div className="settings__inline">
        <Button type="button" onClick={openCreatePolicy}>
          Create policy
        </Button>
      </div>
      {policyOpen ? (
        <div className="settings__provider-form">
          <Input
            label="policy name"
            value={policyDraft.name}
            onChange={(e) => setPolicyDraft((d) => ({ ...d, name: e.target.value }))}
          />
          <Input
            multiline
            rows={6}
            label="groups JSON"
            value={policyDraft.groups}
            onChange={(e) => setPolicyDraft((d) => ({ ...d, groups: e.target.value }))}
            hint="Array of { selectors: [...] } groups"
          />
          {policyError !== null ? (
            <p className="settings__hint settings__hint--error">{policyError}</p>
          ) : null}
          <div className="settings__inline">
            <Button type="button" onClick={savePolicy}>
              {policyDraft.policyId === null ? 'Save new policy' : 'Save policy'}
            </Button>
            <Button type="button" onClick={cancelPolicy}>
              Cancel
            </Button>
          </div>
        </div>
      ) : null}

      <h4 className="settings__subheading">feature policies</h4>
      <div className="settings__bindings">
        {FEATURE_TYPES.map((feature) => {
          const value =
            llm.feature_policies?.[feature] ?? llm.active_policy ?? 'local-then-free';
          return (
            <Select
              key={feature}
              label={featureLabel(feature)}
              options={featurePolicyOptions}
              value={value}
              onChange={(e) => setFeaturePolicy(feature, e.target.value)}
            />
          );
        })}
      </div>

      <h4 className="settings__subheading">resolution preview</h4>
      <div className="settings__provider-form">
        <Select
          label="preview feature"
          options={FEATURE_TYPES.map((f) => ({ value: f, label: featureLabel(f) }))}
          value={previewFeature}
          onChange={(e) =>
            setPreviewFeature(e.target.value as (typeof FEATURE_TYPES)[number])
          }
        />
        <Button type="button" onClick={runPreview} disabled={previewBusy}>
          {previewBusy ? 'Previewing…' : 'Preview resolution'}
        </Button>
        {previewResult !== null ? (
          <>
            <p className="settings__hint">
              status: {previewResult.status ?? '—'}
              {' · '}
              policy: {previewResult.policy_id ?? '—'}
            </p>
            {previewResult.candidates.length === 0 ? (
              <p className="settings__hint">No candidates for this feature.</p>
            ) : (
              <ul className="settings__provider-list">
                {previewResult.candidates.map((c) => (
                  <li key={`${c.provider_id}:${c.model_id}`} className="settings__provider-item">
                    <span className="settings__value">
                      {c.provider_id} / {c.model_id}
                      {c.locality !== undefined || c.cost_class !== undefined
                        ? ` (${[c.locality, c.cost_class].filter(Boolean).join(', ')})`
                        : ''}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </>
        ) : null}
      </div>
    </section>
  );
}
