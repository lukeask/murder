import { render } from 'ink-testing-library';
import { App } from '../src/components/App.js';

// The component-test idiom C5 copies: render with ink-testing-library, assert on the painted
// frame. Components are pure functions of their slice, so a frame snapshot is a real assertion.
describe('App', () => {
  it('renders the scaffold banner', () => {
    const { lastFrame } = render(<App />);
    expect(lastFrame()).toContain('inktui');
  });
});
