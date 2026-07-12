import type { ActionId, Modifier } from '../../input/bindings.js';
import type {
  BarPlacement,
  BarWidgetId,
  BarWidgetsConfig,
} from '../../selectors/barWidgetRegistry.js';
import type {
  LlmProviderId,
  LlmWire,
  StartupRogueModelWire,
  StartupRogueWire,
} from '../../store/settings/settingsActions.js';
import type { DefaultChatViewMode } from '../../store/settings/settingsSlice.js';
import type { TemplateRecord } from '../../store/templates/templatesSlice.js';
import type { ThemeRecord } from '../../store/themes/themesSlice.js';
import type { ThemeId } from '../../theme/palettes.js';

export type SettingsCategoryId =
  | 'appearance'
  | 'bars'
  | 'harnesses'
  | 'llm'
  | 'templates'
  | 'keybindings'
  | 'workspaces';

export type SettingsRow =
  | { readonly id: string; readonly kind: 'header'; readonly label: string }
  | { readonly id: string; readonly kind: 'modifier'; readonly value: Modifier }
  | {
      readonly id: string;
      readonly kind: 'theme';
      readonly value: ThemeId;
      readonly name: string;
      readonly builtin: boolean;
    }
  | { readonly id: string; readonly kind: 'themeImport' }
  | { readonly id: string; readonly kind: 'gap'; readonly value: number }
  | { readonly id: string; readonly kind: 'workspaceCount'; readonly value: number }
  | { readonly id: string; readonly kind: 'vim'; readonly value: boolean }
  | { readonly id: string; readonly kind: 'chatView'; readonly value: DefaultChatViewMode }
  | { readonly id: string; readonly kind: 'startupRogue'; readonly field: 'off' }
  | {
      readonly id: string;
      readonly kind: 'startupRogue';
      readonly field: 'harness' | 'model' | 'effort';
      readonly value: string;
      readonly label?: string;
    }
  | { readonly id: string; readonly kind: 'collaborator'; readonly value: string | null }
  | { readonly id: string; readonly kind: 'planner'; readonly value: string | null }
  | { readonly id: string; readonly kind: 'crow'; readonly value: string | null }
  | {
      readonly id: string;
      readonly kind: 'provider';
      readonly provider: LlmProviderId;
      readonly field: 'api_key' | 'base_url';
    }
  | { readonly id: string; readonly kind: 'llmGlobal' }
  | {
      readonly id: string;
      readonly kind: 'llmProvider';
      readonly providerId: string;
      readonly builtin: boolean;
    }
  | { readonly id: string; readonly kind: 'llmAddProvider'; readonly providerType: 'openai_compatible' | 'lemonade' }
  | { readonly id: string; readonly kind: 'llmPolicy'; readonly policyId: string; readonly builtin: boolean }
  | { readonly id: string; readonly kind: 'llmCreatePolicy' }
  | { readonly id: string; readonly kind: 'llmFeaturePolicy'; readonly feature: string }
  | { readonly id: string; readonly kind: 'tier'; readonly name: string }
  | { readonly id: string; readonly kind: 'role'; readonly role: string; readonly tier: string }
  | { readonly id: string; readonly kind: 'templateCreate' }
  | { readonly id: string; readonly kind: 'template'; readonly name: string }
  | { readonly id: string; readonly kind: 'templateEmpty' }
  | { readonly id: string; readonly kind: 'binding'; readonly action: ActionId }
  | {
      readonly id: string;
      readonly kind: 'barWidget';
      readonly widgetId: BarWidgetId;
      readonly field: 'enabled' | 'adaptive';
      readonly value: boolean;
    }
  | {
      readonly id: string;
      readonly kind: 'barWidget';
      readonly widgetId: BarWidgetId;
      readonly field: 'placement';
      readonly value: BarPlacement;
    }
  | {
      readonly id: string;
      readonly kind: 'barWidgetHarness';
      readonly widgetId: BarWidgetId;
      readonly value: string;
    };

export interface SettingsBuildContext {
  readonly llm: LlmWire;
  readonly startupRogue: StartupRogueWire | null;
  readonly startupRogueModels: Readonly<Record<string, readonly StartupRogueModelWire[]>>;
  readonly startupRogueEfforts: Readonly<Record<string, readonly string[]>>;
  readonly templates: readonly TemplateRecord[];
  readonly themes: readonly ThemeRecord[];
  readonly barWidgets: BarWidgetsConfig;
}

export interface SettingsItem {
  readonly id: string;
  readonly label: string;
  readonly rows: (context: SettingsBuildContext) => readonly SettingsRow[];
}

export interface SettingsCategory {
  readonly id: SettingsCategoryId;
  readonly label: string;
  readonly items: readonly SettingsItem[];
}

export function headerRow(item: SettingsItem): SettingsRow {
  return { id: `${item.id}:header`, kind: 'header', label: item.label };
}
