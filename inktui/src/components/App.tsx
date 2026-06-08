import { Box, Text } from 'ink';

/**
 * Placeholder root component. C5 ("App shell + component vertical") replaces this with the
 * real top-bar / panels / chat-input composition. It lives here, and stays a pure function of
 * props (rule 1: components are pure functions of a slice), so the seed already obeys the cake.
 */
export function App(): React.JSX.Element {
  return (
    <Box flexDirection="column" padding={1}>
      <Text color="magenta">inktui</Text>
      <Text dimColor>Ink scaffold is alive. Replace this in C5 with the real app shell.</Text>
    </Box>
  );
}
