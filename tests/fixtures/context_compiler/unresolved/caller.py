"""References a symbol that does not exist anywhere in the worktree."""


def call_missing():
    return TotallyMissingSymbol()
