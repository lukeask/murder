# Integration tests

Put tests here when they validate a real boundary:

- tmux sessions
- subprocess behavior
- filesystem sync across components
- database round-trips plus service logic

Mark them with `@pytest.mark.integration` when they require live local infrastructure.
