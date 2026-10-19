"""Validate, plan, queue and run actions (specs/actionserver.md, Runs and Phases)."""

from __future__ import annotations

import logging
import shutil
import uuid
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from typing import Any

import jsonschema

from tegel.actionserver.auth import has_scope
from tegel.actionserver.context import RunContext
from tegel.actionserver.destage import Destager, RunFacts
from tegel.actionserver.executor import Executor
from tegel.actionserver.loading import ActionTable, Runnable
from tegel.actionserver.runs import RunStore, TransitionError, now
from tegel.actionserver.staging import BIND_SCOPE, Stager
from tegel.actionserver.tokens import TokenBroker, TokenVault, redact
from tegel.core.contracts.actions import (
    ActionCard,
    Batch,
    BatchRequest,
    InputRoute,
    Plan,
    Run,
    RunPhase,
    RunRequest,
    RunState,
    batch_port,
    check_inputs,
    request_key,
)
from tegel.core.contracts.capabilities import Principal

_log = logging.getLogger("tegel.actionserver")
RESTART_ERROR = "the action server restarted before this run finished"


class RequestError(Exception):
    """A request refused before anything is queued."""

    def __init__(self, status: int, detail: str, plan: Plan | None = None) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.plan = plan


@dataclass
class Checked:
    action: Runnable
    inputs: dict[str, Any]
    parameters: dict[str, Any]


class ActionService:
    def __init__(
        self,
        *,
        actions: ActionTable,
        store: RunStore,
        stager: Stager,
        destager: Destager,
        broker: TokenBroker,
        executor: Executor,
        transfer_warn_seconds: float,
    ) -> None:
        self.actions = actions
        self.store = store
        self.stager = stager
        self.destager = destager
        self.broker = broker
        self.executor = executor
        self.vault = TokenVault()
        self.transfer_warn_seconds = transfer_warn_seconds
        failed = store.fail_unfinished(RESTART_ERROR)
        if failed:
            _log.warning("marked %d unfinished runs failed after a restart", failed)

    # -- cards ----------------------------------------------------------------------------------

    def cards(self, principal: Principal) -> list[ActionCard]:
        return [a.card for a in self.actions.current() if has_scope(principal, a.card.scope)]

    def card(self, action_id: str, principal: Principal) -> ActionCard:
        try:
            found = self.actions.get(action_id)
        except KeyError as exc:
            raise RequestError(404, str(exc.args[0])) from exc
        if not has_scope(principal, found.card.scope):
            raise RequestError(404, f"unknown action {action_id!r}")
        return found.card

    # -- requests -------------------------------------------------------------------------------

    def check(self, action: str, inputs: dict[str, Any], parameters: dict[str, Any],
              principal: Principal) -> Checked:
        try:
            found = self.actions.get(action)
        except KeyError as exc:
            raise RequestError(400, str(exc.args[0])) from exc
        card = found.card
        if not has_scope(principal, card.scope):
            raise RequestError(403, f"{card.ref} needs scope {card.scope}")
        problems = check_inputs(card, inputs)
        if problems:
            raise RequestError(400, "; ".join(problems))
        filled = _with_defaults(card.parameters, parameters)
        try:
            jsonschema.validate(filled, card.parameters)
        except jsonschema.ValidationError as exc:
            where = "/".join(str(p) for p in exc.absolute_path) or "parameters"
            raise RequestError(400, f"{where}: {exc.message}") from exc
        return Checked(found, dict(inputs), filled)

    def plan(self, request: RunRequest, principal: Principal, authorization: str | None) -> Plan:
        checked = self.check(request.action, request.inputs, request.parameters, principal)
        return self._plan(checked, request.force, principal, authorization)

    def _plan(self, checked: Checked, force: bool, principal: Principal,
              authorization: str | None) -> Plan:
        card = checked.action.card
        key = request_key(card, checked.inputs, checked.parameters)
        cached = None if force else self.store.cached(key, principal.subject)
        inputs = []
        if cached is None:
            def auth_for(origin: str) -> str | None:
                return self.broker.authorization_for(authorization, origin)

            for name, value in checked.inputs.items():
                for uri in value if isinstance(value, list) else [value]:
                    inputs.append(self.stager.plan_input(name, uri, auth_for))
        moving = [i for i in inputs if i.moves_bytes]
        total = sum(i.size_bytes or 0 for i in moving)
        known = [i.seconds for i in moving if i.seconds is not None]
        seconds = sum(known) if known else (0.0 if not moving else None)
        warnings: list[str] = []
        transfer_first = False
        if seconds is not None and seconds > self.transfer_warn_seconds:
            transfer_first = True
            warnings.append(
                f"inputs need about {seconds:.0f} s to transfer ({total} bytes), more than "
                f"{self.transfer_warn_seconds:.0f} s; stage them first or resubmit with "
                "allow_transfer"
            )
        unknown = [i.port for i in moving if i.size_bytes is None]
        if unknown:
            warnings.append(f"transfer size unknown for inputs {sorted(set(unknown))}")
        if any(i.route is InputRoute.bind for i in inputs) and not has_scope(principal,
                                                                            BIND_SCOPE):
            warnings.append(f"inputs must be bound by tegel-register, which needs {BIND_SCOPE}")
        return Plan(
            action=card.ref, inputs=inputs, moves_bytes=total, seconds=seconds,
            threshold_seconds=self.transfer_warn_seconds, transfer_first=transfer_first,
            cached=cached, warnings=warnings,
        )

    def submit(self, request: RunRequest, principal: Principal, authorization: str | None,
               *, batch: str | None = None, checked: Checked | None = None) -> Run:
        checked = checked or self.check(request.action, request.inputs, request.parameters,
                                        principal)
        card = checked.action.card
        key = request_key(card, checked.inputs, checked.parameters)
        plan = self._plan(checked, request.force, principal, authorization)
        if plan.cached:
            return self.store.add(action=card.ref, inputs=checked.inputs,
                                  parameters=checked.parameters, principal=principal.subject,
                                  request_key=key, batch=batch, cached_result=plan.cached)
        if any(i.route is InputRoute.bind for i in plan.inputs) and not has_scope(principal,
                                                                                 BIND_SCOPE):
            raise RequestError(403, f"an input must be bound by tegel-register; that needs "
                                    f"scope {BIND_SCOPE}", plan)
        if plan.transfer_first and not request.allow_transfer:
            raise RequestError(409, plan.warnings[0], plan)
        run = self.store.add(action=card.ref, inputs=checked.inputs,
                             parameters=checked.parameters, principal=principal.subject,
                             request_key=key, batch=batch)
        self.vault.put(run.id, authorization)
        self.executor.submit(run.id, list(card.resources), lambda: self._execute(run.id, checked))
        return run

    def submit_batch(self, request: BatchRequest, principal: Principal,
                     authorization: str | None) -> Batch:
        try:
            found = self.actions.get(request.action)
        except KeyError as exc:
            raise RequestError(400, str(exc.args[0])) from exc
        try:
            fan = batch_port(found.card, request)
        except ValueError as exc:
            raise RequestError(400, str(exc)) from exc
        requests = []
        for uri in request.inputs[fan]:
            single = RunRequest(action=request.action, inputs={**request.inputs, fan: uri},
                                parameters=request.parameters, force=request.force,
                                allow_transfer=request.allow_transfer)
            checked = self.check(single.action, single.inputs, single.parameters, principal)
            requests.append((single, checked))
        batch = "b" + uuid.uuid4().hex
        runs = [self.submit(single, principal, authorization, batch=batch, checked=checked)
                for single, checked in requests]
        return Batch(batch=batch, action=found.card.ref, runs=runs)

    # -- runs -----------------------------------------------------------------------------------

    def visible(self, run_id: str, principal: Principal) -> Run:
        try:
            run = self.store.get(run_id)
        except KeyError as exc:
            raise RequestError(404, f"unknown run {run_id!r}") from exc
        if run.principal != principal.subject and not has_scope(principal, "actions:admin"):
            raise RequestError(404, f"unknown run {run_id!r}")
        return run

    def list(self, principal: Principal, **filters: Any) -> list[Run]:
        who = None if has_scope(principal, "actions:admin") else principal.subject
        return self.store.list(principal=who, **filters)

    def cancel(self, run_id: str, principal: Principal) -> Run:
        run = self.visible(run_id, principal)
        scope = self.actions.get(run.action).card.scope
        if not has_scope(principal, scope):
            raise RequestError(403, f"cancelling {run.action} needs scope {scope}")
        if run.state is not RunState.queued or not self.executor.cancel(run_id):
            raise RequestError(409, f"run {run_id} is {self.store.get(run_id).state.value}; "
                                    "only a queued run can be cancelled")
        self.vault.drop(run_id)
        try:
            return self.store.move(run_id, RunState.cancelled)
        except TransitionError as exc:
            raise RequestError(409, str(exc)) from exc

    def wait(self, run_id: str, principal: Principal, timeout: float) -> Run:
        self.visible(run_id, principal)
        return self.store.wait(run_id, min(max(timeout, 0.0), 600.0))

    # -- execution ------------------------------------------------------------------------------

    def _execute(self, run_id: str, checked: Checked) -> None:
        card = checked.action.card
        authorization = self.vault.get(run_id)
        held = [authorization.partition(" ")[2]] if authorization else []
        try:
            run = self.store.move(run_id, RunState.running)
        except TransitionError:  # cancelled between dispatch and start
            self.vault.drop(run_id)
            return
        work_dir = self.stager.config.work_dir / run_id
        work_dir.mkdir(parents=True, exist_ok=True)
        facts = RunFacts(
            run_id=run_id, card=card, principal=run.principal, inputs=checked.inputs,
            parameters=checked.parameters, request_key=self.store.request_key(run_id),
            submitted=run.submitted, started=run.started, ended="", state="succeeded",
            software=_versions(card.software),
        )
        outputs: dict[str, Any] | None = None
        try:
            def auth_for(origin: str) -> str | None:
                return self.broker.authorization_for(authorization, origin)

            values = {
                port.name: self.stager.resolve(port, checked.inputs[port.name], auth_for,
                                               work_dir)
                for port in card.inputs if port.name in checked.inputs
            }
            # The token is not needed after staging; drop it before the action runs.
            self.vault.drop(run_id)
            self.broker.forget(authorization)
            authorization = None
            self.store.set_phase(run_id, RunPhase.work)
            context = RunContext(run_id=run_id, action=card.ref, principal=run.principal,
                                 work_dir=work_dir,
                                 log=logging.LoggerAdapter(_log, {"run": run_id}))
            outputs = checked.action.run(context, values, checked.parameters)
            self.store.set_phase(run_id, RunPhase.destage)
            facts.ended = now()
            result = self.destager.store(facts, outputs)
            self.store.move(run_id, RunState.succeeded, result=result)
        except Exception as exc:
            message = redact(f"{type(exc).__name__}: {exc}", held)
            _log.info("run %s failed: %s", run_id, message)
            facts.state, facts.error, facts.ended = "failed", message, now()
            result = None
            try:
                result = self.destager.store(facts, None)
            except Exception as store_exc:  # the run still fails, with both reasons
                message = f"{message}; storing the failed run also failed: " + redact(
                    str(store_exc), held)
            self.store.move(run_id, RunState.failed, result=result, error=message)
        finally:
            self.vault.drop(run_id)
            self.broker.forget(authorization)
            shutil.rmtree(work_dir, ignore_errors=True)

    def close(self) -> None:
        for run_id in self.executor.close():
            self.vault.drop(run_id)
        self.store.fail_unfinished("the action server stopped before this run finished")


def _with_defaults(schema: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
    """Top-level defaults from the parameter schema, under the given values."""
    filled = {
        name: spec["default"]
        for name, spec in (schema.get("properties") or {}).items()
        if isinstance(spec, dict) and "default" in spec
    }
    filled.update(parameters)
    return filled


def _versions(packages: list[str]) -> dict[str, str]:
    found = {}
    for name in packages:
        try:
            found[name] = version(name)
        except PackageNotFoundError:
            found[name] = "not installed"
    return found
