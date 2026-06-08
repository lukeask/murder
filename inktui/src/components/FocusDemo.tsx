/**
 * FocusDemo — throwaway scaffolding that exercises the C4 input/focus backbone end to end so the
 * dispatcher, stores, keymap-as-data, and the re-home invariant are demonstrably correct under
 * simulated key sequences (`ink-testing-library`). It is NOT a reference component — C5 ships the
 * real panel pattern. What is reference here is *how a panel participates in input*: it declares a
 * keymap with {@link usePanelKeymap}, reads its highlight with {@link useEffectiveFocus}, and
 * registers its rect with {@link useMeasureFocus}. Copy those three calls; throw the rest away.
 *
 * Layout mirrors the real one enough to make directional nav meaningful: two side-by-side panels in
 * a row, chat below — so `ctrl+l`/`ctrl+h` move between panels and `ctrl+j` reaches chat.
 */

import { Box, Text } from 'ink';
import { useMemo, useState } from 'react';
import {
  type InputStores,
  InputStoresProvider,
  useEffectiveFocus,
  useFocusRef,
  useMeasureFocus,
  usePanelKeymap,
  usePanelStore,
} from '../hooks/useInputStores.js';
import { useRootInput } from '../hooks/useRootInput.js';
import type { PanelKeymap } from '../input/keymap.js';
import type { PanelId } from '../input/panels.js';

/** A dummy panel: declares one keymap entry (`a` → "act"), shows a highlighted border when focused,
 * and bumps a local counter when its intent fires — proving a declared key fires only when focused. */
function DummyPanel({
  id,
  label,
}: {
  readonly id: PanelId;
  readonly label: string;
}): React.JSX.Element {
  const ref = useFocusRef();
  const focused = useEffectiveFocus() === id;
  const [acted, setActed] = useState(0);

  // The keymap-as-data declaration — the recipe a real panel copies. Memoised so the registry
  // effect doesn't churn on every render (the handler closes over the live `setActed`).
  const keymap: PanelKeymap<'act'> = useMemo(
    () => ({
      keymap: [{ chord: { input: 'a' }, intent: 'act', description: 'do the thing' }],
      onIntent: () => setActed((n) => n + 1),
    }),
    [],
  );
  usePanelKeymap(id, keymap);
  useMeasureFocus(id, ref);

  return (
    <Box ref={ref} borderStyle="round" borderColor={focused ? 'green' : 'gray'} paddingX={1}>
      <Text>{`${label}${focused ? '*' : ' '} acted=${acted}`}</Text>
    </Box>
  );
}

/** The chat input stand-in: highlighted when it holds focus (the re-home destination). */
function ChatInput(): React.JSX.Element {
  const ref = useFocusRef();
  const focused = useEffectiveFocus() === 'chat';
  useMeasureFocus('chat', ref);
  return (
    <Box ref={ref} borderStyle="round" borderColor={focused ? 'green' : 'gray'} paddingX={1}>
      <Text>{`chat${focused ? ' [focused]' : ''}`}</Text>
    </Box>
  );
}

/** Renders the demo tree and installs the one root input loop. Split from the provider so the loop
 * runs inside the context. */
function DemoTree(): React.JSX.Element {
  useRootInput();
  const visible = usePanelStore((s) => s.visible);
  return (
    <Box flexDirection="column">
      <Box flexDirection="row">
        {visible.has('plans') ? <DummyPanel id="plans" label="plans" /> : null}
        {visible.has('tickets') ? <DummyPanel id="tickets" label="tickets" /> : null}
      </Box>
      <ChatInput />
    </Box>
  );
}

/** Provide pre-built stores and render the demo. Tests build the stores so they can drive/inspect
 * them; the provider just supplies them. */
export function FocusDemo({ stores }: { readonly stores: InputStores }): React.JSX.Element {
  return (
    <InputStoresProvider value={stores}>
      <DemoTree />
    </InputStoresProvider>
  );
}
