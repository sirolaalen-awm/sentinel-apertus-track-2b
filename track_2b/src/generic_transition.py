"""Thin process gate. Domain meaning and effects belong to registered adapters."""
from dataclasses import dataclass, asdict, replace
import copy
import hashlib
import json
import uuid
import weakref

_PERMIT_GUARDS = weakref.WeakKeyDictionary()


class TransitionReject(ValueError):
    pass


@dataclass(frozen=True)
class GenericTransition:
    transition_id: str
    subject: str
    observed_state: dict
    proposed_action: dict
    authority_required: str
    execution_adapter: str | None
    expected_effect: dict | None
    status: str = "PROPOSED"

    def validate(self):
        try:
            uuid.UUID(self.transition_id)
        except (ValueError, TypeError, AttributeError):
            raise TransitionReject("invalid transition identity") from None
        if (not isinstance(self.subject, str) or not self.subject.strip()
                or not isinstance(self.authority_required, str) or not self.authority_required.strip()
                or not isinstance(self.observed_state, dict) or not self.observed_state
                or not isinstance(self.proposed_action, dict)
                or not isinstance(self.proposed_action.get("action_type"), str)
                or not self.proposed_action["action_type"].strip()
                or (self.execution_adapter is not None and
                    (not isinstance(self.execution_adapter, str) or not self.execution_adapter.strip()))
                or (self.expected_effect is not None and
                    (not isinstance(self.expected_effect, dict) or not self.expected_effect))
                or self.status not in {"PROPOSED", "AWAITING_AUTHORITY", "AUTHORIZED", "EXECUTED", "VERIFIED"}):
            raise TransitionReject("invalid transition shape")
        json.dumps(asdict(self), allow_nan=False)
        return self

    def digest(self):
        self.validate()
        value = asdict(self)
        del value["status"]
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class AuthorityDecision:
    transition_id: str
    transition_sha256: str
    authority: str
    actor: str
    source: str
    approved: bool


class ExecutionPermit:
    """In-process capability; never persisted or accepted from model output."""
    def require(self, adapter_id, transition):
        guard = _PERMIT_GUARDS.get(self)
        if guard is None:
            raise TransitionReject("permit was not issued by a transition gate")
        guard(self, adapter_id, transition)


def require_permit(permit, adapter_id, transition):
    if not isinstance(permit, ExecutionPermit):
        raise TransitionReject("execution requires a gate-issued permit")
    ExecutionPermit.require(permit, adapter_id, transition)


class TransitionProcess:
    def __init__(self, transition, adapters):
        transition.validate()
        if transition.status != "PROPOSED":
            raise TransitionReject("intake accepts only PROPOSED")
        self.transition = copy.deepcopy(transition)
        self.adapters = dict(adapters)
        self.locked_sha256 = transition.digest()
        self.authority = None
        self._permit = None
        self.execution = None
        self.readback = None
        self.trace = [{"status": "PROPOSED", "locked_sha256": self.locked_sha256}]

    def _intact(self):
        if self.transition.digest() != self.locked_sha256:
            raise TransitionReject("locked transition changed")

    def _move(self, status, **evidence):
        self._intact()
        self.transition = replace(self.transition, status=status)
        self.trace.append({"status": status, "locked_sha256": self.locked_sha256, **evidence})

    def authorize(self, decision=None):
        self._intact()
        if self.transition.status not in {"PROPOSED", "AWAITING_AUTHORITY"}:
            raise TransitionReject("authorization is out of sequence")
        adapter = self.adapters.get(self.transition.execution_adapter)
        if adapter is None:
            self._move("AWAITING_AUTHORITY", reason="execution_adapter_unavailable")
            return False
        # Validate domain meaning before a permit or any CORE invocation.
        adapter.validate(self.transition)
        if decision is None:
            self._move("AWAITING_AUTHORITY", reason="authority_unavailable")
            return False
        if (not isinstance(decision, AuthorityDecision) or decision.approved is not True
                or decision.transition_id != self.transition.transition_id
                or decision.transition_sha256 != self.locked_sha256
                or decision.authority != self.transition.authority_required
                or not isinstance(decision.actor, str) or not decision.actor.strip()
                or not isinstance(decision.source, str) or not decision.source.strip()):
            raise TransitionReject("authority decision does not authorize this locked transition")
        self.authority = decision
        self._move("AUTHORIZED", authority=asdict(decision))
        self._permit = ExecutionPermit()
        _PERMIT_GUARDS[self._permit] = self._guard
        return True

    def _guard(self, permit, adapter_id, transition):
        self._intact()
        if (permit is not self._permit or self.transition.status != "AUTHORIZED"
                or self.authority is None or adapter_id != self.transition.execution_adapter
                or transition.digest() != self.locked_sha256
                or transition.status != "AUTHORIZED"):
            raise TransitionReject("adapter cannot bypass the authority/lock gate")

    def execute(self):
        self._guard(self._permit, self.transition.execution_adapter, self.transition)
        adapter = self.adapters[self.transition.execution_adapter]
        adapter.validate(self.transition)
        result = adapter.execute(copy.deepcopy(self.transition), self._permit)
        self._intact()
        if not isinstance(result, dict) or result.get("executed") is not True:
            raise TransitionReject("adapter has not supplied execution evidence")
        self.execution = result
        self._move("EXECUTED", evidence=result)
        self._permit = None

    def verify(self):
        self._intact()
        if self.transition.status != "EXECUTED":
            raise TransitionReject("readback requires EXECUTED")
        observed = self.adapters[self.transition.execution_adapter].readback(self.transition)
        self._intact()
        self.readback = observed
        if (self.transition.expected_effect is None or not isinstance(observed, dict)
                or observed != self.transition.expected_effect):
            self.trace.append({"status": "EXECUTED", "event": "READBACK_MISMATCH",
                               "locked_sha256": self.locked_sha256, "observed": observed})
            return False
        self._move("VERIFIED", observed=observed)
        return True

    def receipt(self):
        self._intact()
        return {"transition": asdict(self.transition), "locked_sha256": self.locked_sha256,
                "authority_decision": None if self.authority is None else asdict(self.authority),
                "execution": self.execution, "readback": self.readback, "trace": self.trace}
