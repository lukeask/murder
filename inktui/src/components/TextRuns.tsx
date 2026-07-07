import { Text } from 'ink';
import type { TextRun } from '../render/cellSurface.js';

/** Map {@link TextRun} segments to Ink `<Text>` children with stable keys for duplicate runs. */
export function TextRuns({ runs }: { readonly runs: readonly TextRun[] }): React.JSX.Element {
  const occurrences = new Map<string, number>();
  const keyedRuns = runs.map((run) => {
    const identity = JSON.stringify([
      run.text,
      run.style.fg,
      run.style.bg,
      run.style.bold,
      run.style.dim,
    ]);
    const occurrence = occurrences.get(identity) ?? 0;
    occurrences.set(identity, occurrence + 1);
    return { key: `${identity}:${occurrence}`, run };
  });
  return (
    <Text>
      {keyedRuns.map(({ key, run }) => {
        const props = {
          ...(run.style.fg !== undefined ? { color: run.style.fg } : {}),
          ...(run.style.bg !== undefined ? { backgroundColor: run.style.bg } : {}),
          ...(run.style.bold !== undefined ? { bold: run.style.bold } : {}),
          ...(run.style.dim !== undefined ? { dimColor: run.style.dim } : {}),
        };
        return (
          <Text key={key} {...props}>
            {run.text}
          </Text>
        );
      })}
    </Text>
  );
}
