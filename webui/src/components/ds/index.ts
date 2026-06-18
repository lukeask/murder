/**
 * Design-system barrel. Re-exports every ported `.mds-*` primitive + the shared helpers so consumers
 * import from one place: `import { Button, Panel, Icon } from '@/components/ds'`.
 *
 * Phase B: add each new component's export here.
 */

export { cx } from './cx.js';
export { Icon } from './Icon.js';
export type { IconName, IconProps } from './Icon.js';

export { Button } from './Button.js';
export type { ButtonProps } from './Button.js';

export { Panel } from './Panel.js';
export type { PanelProps } from './Panel.js';

export { Tabs } from './Tabs.js';
export type { TabItem, TabsProps } from './Tabs.js';

export { Dialog } from './Dialog.js';
export type { DialogProps } from './Dialog.js';

// ── forms ──
export { IconButton } from './IconButton.js';
export type { IconButtonProps } from './IconButton.js';
export { Input } from './Input.js';
export type { InputProps } from './Input.js';
export { Select } from './Select.js';
export type { SelectOption, SelectProps } from './Select.js';
export { Checkbox } from './Checkbox.js';
export type { CheckboxProps } from './Checkbox.js';
export { Radio } from './Radio.js';
export type { RadioOption, RadioProps } from './Radio.js';
export { Switch } from './Switch.js';
export type { SwitchProps } from './Switch.js';

// ── data ──
export { ListRow } from './ListRow.js';
export type { ListRowProps } from './ListRow.js';
export { Badge } from './Badge.js';
export type { BadgeProps, BadgeTone } from './Badge.js';
export { StatusDot } from './StatusDot.js';
export type { StatusDotProps, StatusDotStatus } from './StatusDot.js';
export { Avatar } from './Avatar.js';
export type { AvatarProps } from './Avatar.js';
export { Tag } from './Tag.js';
export type { TagProps } from './Tag.js';
export { KeyHint } from './KeyHint.js';
export type { KeyHintProps } from './KeyHint.js';

// ── navigation ──
export { NavBar } from './NavBar.js';
export type { NavBarProps, NavItem } from './NavBar.js';
export { KeybindBar } from './KeybindBar.js';
export type { KeybindBarProps, KeybindHint } from './KeybindBar.js';

// ── feedback ──
export { Toast } from './Toast.js';
export type { ToastProps, ToastTone } from './Toast.js';
export { Tooltip } from './Tooltip.js';
export type { TooltipProps } from './Tooltip.js';
