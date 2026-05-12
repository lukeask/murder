"""Harness interface and live tmux session facade."""

from __future__ import annotations

import asyncio
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar, Literal

from murder import tmux
from murder.harnesses.models import (
    HarnessPaneState,
    HarnessStartSpec,
    HarnessUsageStatus,
)
from murder.harnesses.parsing import strip_ui_chrome
from murder.harnesses.results import SimpleResult, fail_result, ok_result

ASK_RE = re.compile(r">>>\s*ASK:\s*(?P<body>.+?)(?=\n>>>|\Z)", re.DOTALL)
CHECK_RE = re.compile(r">>>\s*CHECK:\s*(?P<body>.+?)$", re.MULTILINE)
NOTE_RE = re.compile(r">>>\s*NOTE:\s*(?P<body>.+?)\n>>>\s*END\b", re.DOTALL)
DONE_RE = re.compile(r">>>\s*DONE\b")
MAX_NOTE_LINES = 20

UsageCollectionMode = Literal["none", "tmux_slash", "http"]


class HarnessSession:
    def __init__(self, adapter: HarnessAdapter, session: str, repo_root: Path) -> None:
        self.adapter = adapter
        self.session = session
        self.repo_root = repo_root

    async def start(self, spec: HarnessStartSpec | None = None) -> SimpleResult[None]:
        start_spec = spec or HarnessStartSpec(
            cwd=self.repo_root,
            startup_model=self.adapter.startup_model,
        )
        await tmux.create_session(
            self.session,
            start_spec.cwd,
            self.adapter.startup_cmd(start_spec.cwd),
        )

        attempts = max(1, int(start_spec.ready_timeout_s / start_spec.poll_interval_s))
        for _ in range(attempts):
            try:
                pane = await tmux.capture_pane(self.session, lines=120)
            except tmux.TmuxError as e:
                return fail_result(f"Session lost during startup: {e}")
            if self.adapter.is_ready(pane):
                break
            await asyncio.sleep(start_spec.poll_interval_s)
        else:
            return fail_result(f"Harness not ready in time: session={self.session}")

        desired_model = start_spec.startup_model or self.adapter.startup_model
        if desired_model:
            model_result = await self.set_model(desired_model)
            if not model_result.ok:
                return model_result
            idle_result = await self.wait_idle(timeout_s=15.0)
            if not idle_result.ok:
                return idle_result

        init_result = await self.initialize_defaults(start_spec)
        if not init_result.ok:
            return init_result
        idle_result = await self.wait_idle(timeout_s=15.0)
        if not idle_result.ok:
            return idle_result
        return ok_result()

    async def wait_ready(self, timeout_s: float = 240.0) -> SimpleResult[None]:
        attempts = max(1, int(timeout_s / 0.4))
        for _ in range(attempts):
            try:
                pane = await tmux.capture_pane(self.session, lines=120)
            except tmux.TmuxError as e:
                return fail_result(f"Session lost during ready-wait: {e}")
            if self.adapter.is_ready(pane):
                return ok_result()
            await asyncio.sleep(0.4)
        return fail_result(f"Harness not ready in time: session={self.session}")

    async def wait_idle(self, timeout_s: float = 30.0) -> SimpleResult[None]:
        attempts = max(1, int(timeout_s / 0.4))
        for _ in range(attempts):
            try:
                pane = await tmux.capture_pane(self.session, lines=120)
            except tmux.TmuxError as e:
                return fail_result(f"Session lost during idle-wait: {e}")
            if self.adapter.is_idle(pane):
                return ok_result()
            await asyncio.sleep(0.4)
        return fail_result(f"Harness not idle in time: session={self.session}")

    async def initialize_defaults(self, spec: HarnessStartSpec) -> SimpleResult[None]:
        return await self.adapter.initialize_defaults(self.session, spec)

    def status_from_pane(self, pane_text: str) -> HarnessPaneState:
        return HarnessPaneState(
            ready=self.adapter.is_ready(pane_text),
            idle=self.adapter.is_idle(pane_text),
            busy=self.adapter.is_busy(pane_text),
        )

    async def send_prompt(self, prompt: str) -> SimpleResult[None]:
        await self.adapter.send_prompt(self.session, prompt)
        return ok_result()

    async def set_model(self, model: str) -> SimpleResult[None]:
        selected = await self.adapter.set_model(self.session, model)
        if selected:
            return ok_result()
        return fail_result(
            f"{self.adapter.kind} does not support runtime model selection"
        )

    async def request_usage_status(self) -> SimpleResult[None]:
        requested = await self.adapter.request_usage_status(self.session)
        if requested:
            return ok_result()
        return fail_result(
            f"{self.adapter.kind} does not support usage/status reporting"
        )

    async def collect_usage_status(self) -> SimpleResult[HarnessUsageStatus]:
        return await self.adapter.collect_usage_status(self.session)

    async def interrupt(self) -> SimpleResult[None]:
        await self.adapter.interrupt(self.session)
        return ok_result()


class HarnessAdapter(ABC):
    kind: ClassVar[str]
    crow_system_prompt: ClassVar[str]
    available_startup_models: ClassVar[list[tuple[str, str]]] = []
    usage_collection_mode: ClassVar[UsageCollectionMode] = "none"

    def __init__(self, startup_model: str | None = None) -> None:
        self.startup_model = startup_model

    def attach(self, session: str, repo_root: Path) -> HarnessSession:
        return HarnessSession(self, session, repo_root)

    @abstractmethod
    def startup_cmd(self, cwd: Path) -> list[str]: ...

    @abstractmethod
    def is_ready(self, pane_text: str) -> bool: ...

    @abstractmethod
    def is_idle(self, pane_text: str) -> bool: ...

    @abstractmethod
    def is_busy(self, pane_text: str) -> bool: ...

    async def initialize_defaults(
        self, session: str, spec: HarnessStartSpec
    ) -> SimpleResult[None]:
        del session, spec
        return ok_result()

    async def send_prompt(self, session: str, prompt: str) -> None:
        await tmux.send_keys(session, prompt, literal=True, enter=True)

    async def set_model(self, session: str, model: str) -> bool:
        del session, model
        return False

    async def request_usage_status(self, session: str) -> bool:
        del session
        return False

    async def collect_usage_status(
        self, session: str
    ) -> SimpleResult[HarnessUsageStatus]:
        del session
        return fail_result(
            f"{self.kind} does not support structured usage/status reporting"
        )

    @abstractmethod
    def extract_last_message(self, pane_text: str) -> str | None: ...

    def detect_ask(self, pane_text: str) -> str | None:
        m = ASK_RE.search(strip_ui_chrome(pane_text))
        return m.group("body").strip() if m else None

    def detect_asks(self, pane_text: str) -> list[str]:
        clean = strip_ui_chrome(pane_text)
        return [m.group("body").strip() for m in ASK_RE.finditer(clean)]

    def detect_checks(self, pane_text: str) -> list[str]:
        clean = strip_ui_chrome(pane_text)
        return [m.group("body").strip() for m in CHECK_RE.finditer(clean)]

    def detect_notes(self, pane_text: str) -> list[str]:
        clean = strip_ui_chrome(pane_text)
        notes: list[str] = []
        for match in NOTE_RE.finditer(clean):
            lines = match.group("body").strip().splitlines()
            notes.append("\n".join(lines[:MAX_NOTE_LINES]).strip())
        return [note for note in notes if note]

    def detect_done(self, pane_text: str) -> bool:
        return bool(DONE_RE.search(strip_ui_chrome(pane_text)))

    @abstractmethod
    def format_nudge(self, msg: str) -> str: ...

    async def interrupt(self, session: str) -> None:
        await tmux.interrupt(session)
