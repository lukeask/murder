"""Unchanged source that imports a symbol from target."""

from target import Greeter


def greet():
    return Greeter().hello()
