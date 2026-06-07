"""Store base contract — no Textual imports; pure data objects."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Generic, Protocol, TypeVar

S = TypeVar("S")


class Store(Protocol[S]):
    def subscribe(self, callback: Callable[[], None]) -> Callable[[], None]: ...
    def get_snapshot(self) -> S: ...


class BaseStore(Generic[S]):
    """Concrete base implementing the Store contract.

    _set(new) compares by value equality; only fires subscribers and replaces
    the stored snapshot when the value actually changed. On equal snapshots the
    old object is kept so get_snapshot() returns the same identity each idle
    tick — the web-port's spurious-re-render guard.
    """

    def __init__(self, initial: S) -> None:
        self._snapshot: S = initial
        self._subs: dict[int, Callable[[], None]] = {}
        self._next_token: int = 0

    def subscribe(self, callback: Callable[[], None]) -> Callable[[], None]:
        token = self._next_token
        self._next_token += 1
        self._subs[token] = callback

        def _unsubscribe() -> None:
            self._subs.pop(token, None)

        return _unsubscribe

    def get_snapshot(self) -> S:
        return self._snapshot

    def _set(self, new: S) -> None:
        if new == self._snapshot:
            return
        self._snapshot = new
        for cb in list(self._subs.values()):
            cb()


class StoreRegistry:
    """Locates stores by string key for the coordinator and components."""

    def __init__(self) -> None:
        self._stores: dict[str, Any] = {}

    def register(self, key: str, store: Any) -> None:
        self._stores[key] = store

    def get(self, key: str) -> Any:
        return self._stores[key]

    def __contains__(self, key: str) -> bool:
        return key in self._stores
