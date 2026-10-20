"""Binder conformance. Reports passed, failed, and skipped checks."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tegel.register.bind import Mode
from tegel.register.context import BindContext
from tegel.register.errors import ModeNotAvailableError


@dataclass
class ConformanceResult:
    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)


class ConformanceFailure(AssertionError):
    def __init__(self, result: ConformanceResult) -> None:
        self.result = result
        super().__init__(_format(result))


@dataclass
class ConformanceCase:
    """One binder exercised against one site and one Tiled container."""

    binder: Any
    uri: str
    into: Any
    context: BindContext
    infeasible: Mode
    sentinel: str = "SENTINEL-DO-NOT-LEAK-"
    acquire_uri: str | None = None
    acquire_key: str | None = None
    landing: Path | None = None


def run_conformance(case: ConformanceCase, *, raise_on_failure: bool = True) -> ConformanceResult:
    """Run the binder checks. Failed checks raise when ``raise_on_failure`` is set."""
    result = ConformanceResult()

    def _run(name: str, fn) -> None:
        try:
            fn()
        except _Skip as exc:
            result.skipped.append((name, str(exc)))
        except Exception as exc:
            result.failed.append(f"{name}: {exc}")
        else:
            result.passed.append(name)

    _run("api_version", lambda: _api_version(case))
    _run("modes_once", lambda: _modes_once(case))
    _run("plan_no_side_effects", lambda: _plan_quiet(case))
    _run("infeasible_mode", lambda: _infeasible(case))
    _run("acquire_atomic", lambda: _acquire_atomic(case))
    _run("no_sentinel", lambda: _no_sentinel(case))
    if raise_on_failure and result.failed:
        raise ConformanceFailure(result)
    return result


class _Skip(Exception):
    pass


def _format(result: ConformanceResult) -> str:
    lines = [
        f"conformance: {len(result.passed)} passed, {len(result.failed)} failed, "
        f"{len(result.skipped)} skipped"
    ]
    for name in result.failed:
        lines.append(f"  FAILED: {name}")
    for name, reason in result.skipped:
        lines.append(f"  SKIPPED: {name} ({reason})")
    return "\n".join(lines)


def _api_version(case: ConformanceCase) -> None:
    if case.binder.api_version != 2:
        raise AssertionError(f"api_version is {case.binder.api_version!r}")


def _modes_once(case: ConformanceCase) -> None:
    source = case.context.site.source_for(case.uri) if case.context.site is not None else None
    reported = case.binder.feasible_modes(case.uri, source, case.into, case.context)
    modes = [item[0] for item in reported]
    expected = list(Mode)
    if modes != expected and sorted(modes, key=lambda mode: mode.value) != sorted(expected, key=lambda mode: mode.value):
        raise AssertionError(f"modes {modes} do not list each Mode once")
    if len(modes) != len(set(modes)) or set(modes) != set(expected):
        raise AssertionError(f"modes {modes} do not list each Mode once")


def _plan_quiet(case: ConformanceCase) -> None:
    from tegel.register import plan

    before_keys = _keys(case.into)
    before_files = _files(case.landing)
    plan(case.uri, case.into, context=case.context)
    if _keys(case.into) != before_keys:
        raise AssertionError("plan() changed the container")
    if _files(case.landing) != before_files:
        raise AssertionError("plan() wrote a landing file")


def _infeasible(case: ConformanceCase) -> None:
    from tegel.register import plan

    try:
        plan(case.uri, case.into, mode=case.infeasible, context=case.context)
    except ModeNotAvailableError:
        return
    raise AssertionError(f"mode {case.infeasible.value} did not raise ModeNotAvailableError")


def _acquire_atomic(case: ConformanceCase) -> None:
    from tegel.register import register

    if case.acquire_uri is None or case.landing is None:
        raise _Skip("no acquire fixture")
    register(
        case.acquire_uri,
        case.into,
        key=case.acquire_key,
        mode="acquire",
        context=case.context,
    )
    partials = [path for path in case.landing.rglob("*") if path.name.endswith(".partial")]
    if partials:
        raise AssertionError(f"acquire left partial files: {partials}")


def _no_sentinel(case: ConformanceCase) -> None:
    from tegel.register import plan

    planned = plan(case.uri, case.into, context=case.context)
    texts = [planned.to_json(), repr(planned)]
    try:
        plan(case.uri, case.into, mode=case.infeasible, context=case.context)
    except ModeNotAvailableError as exc:
        texts.append(str(exc))
        texts.append(repr(exc))
    for text in texts:
        if case.sentinel in text:
            raise AssertionError("sentinel secret appeared in a plan or error")


def _keys(into: Any) -> list[str]:
    try:
        return sorted(str(key) for key in into)
    except Exception:
        return []


def _files(landing: Path | None) -> set[str]:
    if landing is None or not landing.exists():
        return set()
    return {path.relative_to(landing).as_posix() for path in landing.rglob("*") if path.is_file()}
