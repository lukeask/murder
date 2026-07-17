"""Small interactive console for manually probing a live harness.

Run with ``uv run python tools/harness_probe.py codex``.  It starts the
selected CLI in a detached tmux session, so the commands exercise the same
verified harness-control boundary that production agents use.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import sqlite3
from pathlib import Path
from typing import cast

from murder.llm.harness_control.capabilities.model_selection import ModelTarget
from murder.llm.harness_control.capabilities.permissions import (
    PermissionAnswerRequest,
    PermissionDecisionKind,
    PermissionResponseTarget,
    permission_fingerprint,
)
from murder.llm.harness_control.capabilities.questions import (
    QuestionAnswerRequest,
    question_fingerprint,
)
from murder.llm.harness_control.capabilities.resume import ResumePickerTarget
from murder.llm.harness_control.capabilities.session_settings import SessionSettingsTarget
from murder.llm.harness_control.model.actions import (
    InputChunk,
    InputProvenance,
    QuestionAnswerMode,
    QuestionChoiceSelection,
)
from murder.llm.harness_control.model.observations import ChoiceState, Knowledge
from murder.llm.harness_control.runtime.manual_input import emit_manual_input
from murder.llm.harness_control.runtime.session import VerifiedHarnessControlSession
from murder.llm.harnesses.antigravity import AntigravityAdapter
from murder.llm.harnesses.base import HarnessAdapter
from murder.llm.harnesses.claude_code import ClaudeCodeAdapter
from murder.llm.harnesses.codex import CodexAdapter
from murder.llm.harnesses.cursor import CursorAdapter
from murder.llm.harnesses.models import HarnessStartSpec
from murder.llm.harnesses.pi_harness import PiAdapter
from murder.runtime.terminal import tmux
from murder.state.persistence.schema import init_db

HARNESS_NAMES = ("agy", "antigravity", "claude_code", "codex", "cursor", "pi")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("harness", choices=HARNESS_NAMES)
    parser.add_argument("--cwd", type=Path, default=Path.cwd(), help="harness working directory")
    parser.add_argument("--session", help="tmux session name (default: probe_<harness>_<pid>)")
    parser.add_argument("--attach", action="store_true", help="use an existing tmux session")
    parser.add_argument("--binary", help="override the harness executable (currently Cursor only)")
    parser.add_argument(
        "--keep", action="store_true", help="leave a session started by this probe alive"
    )
    return parser


def _render(value: object) -> str:
    return repr(value)


def _adapter(kind: str, binary: str | None) -> HarnessAdapter:
    kind = "antigravity" if kind == "agy" else kind
    if kind == "codex":
        return CodexAdapter(binary=binary)
    if kind == "claude_code":
        return ClaudeCodeAdapter(binary=binary)
    if kind == "cursor":
        return CursorAdapter(binary=binary)
    if kind == "antigravity":
        return AntigravityAdapter(binary=binary)
    if kind == "pi":
        return PiAdapter(binary=binary)
    raise ValueError(f"unsupported harness: {kind}")


def _choice(choices: tuple[ChoiceState, ...], selector: str) -> ChoiceState:
    """Resolve one exact visible choice by stable id, number, or label."""

    matches = [
        choice
        for choice in choices
        if selector == choice.stable_choice_id
        or (choice.number is not None and selector == str(choice.number))
        or selector == choice.label
    ]
    if len(matches) != 1:
        raise ValueError(f"choice {selector!r} is absent or ambiguous")
    return matches[0]


async def _answer_question(
    control: VerifiedHarnessControlSession, selectors: list[str]
) -> bool:
    snapshot = await control.observe_once()
    observed = snapshot.question
    if observed.knowledge is not Knowledge.PRESENT or observed.value is None:
        raise ValueError("no structured question is currently visible")
    question = observed.value
    choices = tuple(_choice(question.choices, selector) for selector in selectors)
    mode = QuestionAnswerMode.MULTIPLE if len(choices) > 1 else QuestionAnswerMode.SINGLE
    request = QuestionAnswerRequest(
        question.question_id_hint,
        question_fingerprint(question),
        mode,
        tuple(QuestionChoiceSelection(choice.stable_choice_id, choice.label) for choice in choices),
    )
    return cast(bool, await control.answer_question(request))


async def _answer_custom_question(
    control: VerifiedHarnessControlSession, answer: str
) -> bool:
    snapshot = await control.observe_once()
    observed = snapshot.question
    if observed.knowledge is not Knowledge.PRESENT or observed.value is None:
        raise ValueError("no structured question is currently visible")
    question = observed.value
    candidates = [
        choice for choice in question.choices if "none of the above" in choice.label.casefold()
    ]
    if len(candidates) != 1:
        raise ValueError("question has no unique 'None of the above' notes choice")
    choice = candidates[0]
    request = QuestionAnswerRequest(
        question.question_id_hint,
        question_fingerprint(question),
        QuestionAnswerMode.SINGLE,
        (QuestionChoiceSelection(choice.stable_choice_id, choice.label),),
        note=answer,
    )
    return cast(bool, await control.answer_question(request))


async def _answer_question_with_note(
    control: VerifiedHarnessControlSession, selector: str, note: str
) -> bool:
    snapshot = await control.observe_once()
    observed = snapshot.question
    if observed.knowledge is not Knowledge.PRESENT or observed.value is None:
        raise ValueError("no structured question is currently visible")
    question = observed.value
    choice = _choice(question.choices, selector)
    request = QuestionAnswerRequest(
        question.question_id_hint,
        question_fingerprint(question),
        QuestionAnswerMode.SINGLE,
        (QuestionChoiceSelection(choice.stable_choice_id, choice.label),),
        note=note,
    )
    return cast(bool, await control.answer_question(request))


async def _decline_question(control: VerifiedHarnessControlSession) -> bool:
    snapshot = await control.observe_once()
    observed = snapshot.question
    if observed.knowledge is not Knowledge.PRESENT or observed.value is None:
        raise ValueError("no structured question is currently visible")
    question = observed.value
    return cast(
        bool,
        await control.answer_question(
            QuestionAnswerRequest(
                question.question_id_hint,
                question_fingerprint(question),
                QuestionAnswerMode.DECLINE,
            )
        ),
    )


async def _answer_permission(control: VerifiedHarnessControlSession, selector: str) -> bool:
    snapshot = await control.observe_once()
    observed = snapshot.permission_request
    if observed.knowledge is not Knowledge.PRESENT or observed.value is None:
        raise ValueError("no permission request is currently visible")
    permission = observed.value
    choice = _choice(permission.choices, selector)
    label = choice.label.casefold()
    kind = (
        PermissionDecisionKind.DENY
        if any(word in label for word in ("deny", "reject", "no"))
        else PermissionDecisionKind.ALLOW_FOR_SESSION
        if any(word in label for word in ("session", "always"))
        else PermissionDecisionKind.ALLOW_ONCE
        if any(word in label for word in ("allow", "approve", "yes"))
        else PermissionDecisionKind.HARNESS_SPECIFIC
    )
    request = PermissionAnswerRequest(
        permission.request_id_hint,
        permission_fingerprint(permission),
        PermissionResponseTarget(choice.stable_choice_id, choice.label, kind),
        permission.risk_attributes,
    )
    return cast(bool, await control.answer_permission(request))


async def _show_pane(session: str, lines: int = 80) -> None:
    print(await tmux.capture_pane(session, lines=lines), end="")


async def _repl(  # noqa: PLR0912, PLR0915 - deliberately a tiny command console
    control: VerifiedHarnessControlSession, session: str
) -> None:
    print("ready; /help lists commands")
    while True:
        try:
            command = input("probe> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not command:
            continue
        verb, _, argument = command.partition(" ")
        try:
            if verb in {"/quit", "/exit"}:
                return
            if verb == "/help":
                print(
                    "/pane [lines] | /state | /send TEXT | /model MODEL [EFFORT] | "
                    "/models | /question CHOICE[,CHOICE] | /permission CHOICE | /usage | "
                    "/mode default|accept-edits|plan | /fast on|off | /interrupt | "
                    "/resume [CHOICE] | /update CHOICE | /custom TEXT | /note CHOICE TEXT | "
                    "/decline | /dismiss | "
                    "/resume-config cwd|all updated|created [SEARCH] | /info | "
                    "/keys TEXT | /key NAME | /quit"
                )
            elif verb == "/pane":
                await _show_pane(session, int(argument or "80"))
            elif verb == "/state":
                print(_render(await control.observe_once()))
            elif verb == "/info":
                snapshot = await control.observe_once()
                print(
                    _render(
                        {
                            "surface": snapshot.surface,
                            "generation": snapshot.generation,
                            "model": snapshot.active_model,
                            "settings": snapshot.settings,
                            "info": snapshot.info,
                            "usage": snapshot.usage,
                            "question": snapshot.question,
                            "permission": snapshot.permission_request,
                        }
                    )
                )
            elif verb == "/send":
                if not argument:
                    raise ValueError("/send needs text")
                digest = hashlib.sha256(argument.encode()).hexdigest()
                result = await control.submit_prompt(
                    (InputChunk(argument, InputProvenance.USER_PASTE_BLOCK, f"probe:{digest}"),)
                )
                print(result.outcome.name.lower())
            elif verb == "/model":
                model, _, effort = argument.partition(" ")
                if not model:
                    raise ValueError("/model needs a model id")
                selection = await control.select_model(ModelTarget(model, effort=effort or None))
                print(_render(selection))
            elif verb == "/models":
                discovery = await control.discover_models()
                print(_render(discovery))
            elif verb == "/mode":
                if argument not in {"default", "accept-edits", "plan"}:
                    raise ValueError("/mode needs default, accept-edits, or plan")
                print(
                    "configured"
                    if await control.configure_settings(SessionSettingsTarget(run_mode=argument))
                    else "not verified"
                )
            elif verb == "/fast":
                if argument not in {"on", "off"}:
                    raise ValueError("/fast needs on or off")
                print(
                    "configured"
                    if await control.configure_settings(
                        SessionSettingsTarget(fast_enabled=argument == "on")
                    )
                    else "not verified"
                )
            elif verb == "/usage":
                print(_render(await control.collect_usage(trigger="manual_probe")))
            elif verb == "/question":
                selectors = [item.strip() for item in argument.split(",") if item.strip()]
                if not selectors:
                    raise ValueError(
                        "/question needs one or more comma-separated choice ids/labels"
                    )
                print("answered" if await _answer_question(control, selectors) else "not verified")
            elif verb == "/custom":
                if not argument:
                    raise ValueError("/custom needs answer text")
                print(
                    "answered"
                    if await _answer_custom_question(control, argument)
                    else "not verified"
                )
            elif verb == "/note":
                selector, separator, note = argument.partition(" ")
                if not separator or not note:
                    raise ValueError("/note needs a choice id/number/label and note text")
                print(
                    "answered"
                    if await _answer_question_with_note(control, selector, note)
                    else "not verified"
                )
            elif verb == "/decline":
                print(
                    "declined" if await _decline_question(control) else "not verified"
                )
            elif verb == "/update":
                if not argument:
                    raise ValueError("/update needs a choice id/number/label")
                print(
                    "selected"
                    if await _answer_question(control, [argument.strip()])
                    else "not verified"
                )
            elif verb == "/resume":
                if argument:
                    print(
                        "resumed"
                        if await _answer_question(control, [argument.strip()])
                        else "not verified"
                    )
                else:
                    print(
                        "opened" if await control.open_resume_picker() else "not verified"
                    )
            elif verb == "/resume-config":
                filter_mode, separator, remainder = argument.partition(" ")
                sort_mode, _, search_text = remainder.partition(" ")
                if not separator or not sort_mode:
                    raise ValueError(
                        "/resume-config needs cwd|all updated|created [search text]"
                    )
                configured = await control.configure_resume_picker(
                    ResumePickerTarget(search_text, filter_mode, sort_mode)
                )
                print("configured" if configured else "not verified")
            elif verb == "/permission":
                if not argument:
                    raise ValueError("/permission needs a choice id/number/label")
                print(
                    "answered"
                    if await _answer_permission(control, argument.strip())
                    else "not verified"
                )
            elif verb == "/interrupt":
                print("interrupted" if await control.interrupt() else "not verified")
            elif verb == "/dismiss":
                print("dismissed" if await control.restore_composer() else "not verified")
            elif verb == "/keys":
                if not argument:
                    raise ValueError("/keys needs text")
                receipt = await emit_manual_input(
                    control, text=argument, literal=True, append_enter=True, source="harness_probe"
                )
                print("accepted" if receipt.accepted_by_terminal_transport else "transport failed")
            elif verb == "/key":
                if not argument:
                    raise ValueError("/key needs a tmux key name, e.g. Escape or Enter")
                receipt = await emit_manual_input(
                    control,
                    text=argument,
                    literal=False,
                    append_enter=False,
                    source="harness_probe",
                )
                print("accepted" if receipt.accepted_by_terminal_transport else "transport failed")
            else:
                print("unknown command; use /help")
        except Exception as exc:  # Manual exploration should keep the console alive.
            print(f"error: {exc}")


async def _run(args: argparse.Namespace) -> None:
    cwd = args.cwd.resolve()
    if not cwd.is_dir():
        raise ValueError(f"not a directory: {cwd}")
    harness_kind = "antigravity" if args.harness == "agy" else args.harness
    session = args.session or f"probe_{harness_kind}_{os.getpid()}"
    adapter = _adapter(harness_kind, args.binary)
    started_here = False
    if args.attach:
        if not await tmux.session_exists(session):
            raise ValueError(f"tmux session does not exist: {session}")
    else:
        result = await adapter.attach(session, cwd).start(
            HarnessStartSpec(cwd=cwd, binary=args.binary)
        )
        if not result.ok:
            await tmux.kill_session(session)
            raise RuntimeError(result.message or "harness did not become ready")
        started_here = True

    # The controller journals every action; an in-memory schema keeps this probe
    # isolated from the application's live database while preserving that path.
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    init_db(connection)
    control = VerifiedHarnessControlSession.from_tmux(
        harness_kind=harness_kind,
        terminal_session=session,
        connection=connection,
        persistence_session_id=f"manual-probe:{session}",
    )
    print(f"tmux session: {session}")
    try:
        await _repl(control, session)
    finally:
        connection.close()
        if started_here and not args.keep:
            await tmux.kill_session(session)


def main() -> None:
    try:
        asyncio.run(_run(_parser().parse_args()))
    except (RuntimeError, ValueError, tmux.TmuxError) as exc:
        raise SystemExit(f"harness probe: {exc}") from exc


if __name__ == "__main__":
    main()
