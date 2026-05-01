"""Entry point for `python -m murder`. Delegates to the Typer app."""

from murder.cli import app

if __name__ == "__main__":
    app()
