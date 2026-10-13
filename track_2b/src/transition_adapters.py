"""The two existing domains; only irrigation has an execution carrier."""
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
import uuid

from apertus_motor_a import validate_proposal
from demo_runner import load_core, core_hash, EXPECTED_HASH, ROOT
from generic_transition import GenericTransition, TransitionReject, require_permit

IRRIGATION_ADAPTER = "sentinel_irrigation_v1"


def irrigation_transition(proposal):
    if proposal != {"execute": True, "action_id": "A1"} or proposal.get("execute") is not True:
        raise TransitionReject("invalid Apertus irrigation proposal")
    return GenericTransition(str(uuid.uuid4()), "EU-AGRI-HR-001",
        {"irrigation_state": "off", "version": 0},
        {"action_type": "set_irrigation", "action_id": "A1", "from": "off", "to": "on"},
        "BENCHMARK_OPERATOR", IRRIGATION_ADAPTER,
        {"irrigation_state": "on", "version": 1})


def rumenka_transition(proposal):
    validate_proposal(proposal)
    return GenericTransition(str(uuid.uuid4()), proposal["observed"]["subject"],
        {"observed": proposal["observed"], "relevant_known": proposal["relevant_known"],
         "unknown": proposal["unknown"]},
        {"action_type": "request_context", **proposal["candidate_next_step"]},
        proposal["candidate_next_step"]["requires_authority"], None, None)


class IrrigationAdapter:
    adapter_id = IRRIGATION_ADAPTER

    def __init__(self, run, model):
        self.run, self.model = Path(run), model
        self.execution_receipt = None

    def validate(self, transition):
        transition.validate()
        expected = asdict(irrigation_transition({"execute": True, "action_id": "A1"}))
        actual = asdict(transition)
        for key in ("transition_id", "status"):
            del expected[key], actual[key]
        if (json.dumps(actual, sort_keys=True, allow_nan=False) != json.dumps(expected, sort_keys=True)
                or core_hash() != EXPECTED_HASH):
            raise TransitionReject("transition outside existing irrigation CORE contract")

    def execute(self, transition, permit):
        require_permit(permit, self.adapter_id, transition)
        self.validate(transition)
        self.run.mkdir(parents=True, exist_ok=True)
        runner = Path(__file__).with_name("demo_runner.py")
        command = [sys.executable, "-B", str(runner), "--mode", "apertus", "--run-dir", str(self.run)]
        # Reuse the existing real A/B worker path without changing its semantics.
        subprocess.run(command + ["--worker", "a"], check=True)
        core = load_core(self.run)
        before, field = core.read_json(core.STATE_FILE), core.read_json(core.FIELD_FILE)
        if (before["phase"] != "DECIDED" or field != core.initial_field()
                or before["pending_action"]["action_id"] != transition.proposed_action["action_id"]
                or before["decision"] != core.canonical_decision()
                or before["active_motor"] != self.model):
            raise TransitionReject("persisted CORE checkpoint differs from locked transition")
        digest = core.locked_digest(before)
        core.trace("PROCESS_A_EXITED", returncode=0)
        require_permit(permit, self.adapter_id, transition)
        subprocess.run(command + ["--worker", "b"], check=True)
        state, field = core.read_json(core.STATE_FILE), core.read_json(core.FIELD_FILE)
        checks = {
            "run_id_unchanged": before["run_id"] == state["run_id"],
            "locked_sha256_unchanged": digest == core.locked_digest(state),
            "different_processes": before["process_a_pid"] != state["process_b_pid"],
            "core_source_unchanged": core_hash() == EXPECTED_HASH,
            "field_on": field["irrigation_state"] == "on",
            "field_version_one": field["version"] == 1,
            "verified": state["phase"] == "VERIFIED" and state["verification"]["matches"] is True,
            "k_closed": state["k"]["status"] == "CLOSED",
            "state_keys_unchanged": set(state) == set(before) | {"process_b_pid"},
            "real_response_present": (self.run / "evidence/motor_b_completion.json").is_file(),
        }
        self.execution_receipt = {"mode": "apertus", "passed": all(checks.values()),
            "real_apertus_response": checks["real_response_present"], "checks": checks,
            "core_sha256": core_hash(), "locked_sha256": digest, "run_id": state["run_id"],
            "generic_locked_sha256": transition.digest()}
        core.write_json(self.run / "receipt.json", self.execution_receipt)
        if not all(checks.values()):
            raise TransitionReject("existing CORE execution verification failed")
        return {"executed": True, "core_receipt": str((self.run / "receipt.json").relative_to(ROOT)),
                "core_locked_sha256": digest, "run_id": state["run_id"]}

    def readback(self, transition):
        self.validate(transition)
        core = load_core(self.run)
        state, field = core.read_json(core.STATE_FILE), core.read_json(core.FIELD_FILE)
        if (not self.execution_receipt or state["run_id"] != self.execution_receipt["run_id"]
                or core.locked_digest(state) != self.execution_receipt["locked_sha256"]
                or state["phase"] != "VERIFIED" or state["k"]["status"] != "CLOSED"
                or state["verification"]["matches"] is not True):
            raise TransitionReject("CORE readback/provenance mismatch")
        return {"irrigation_state": field["irrigation_state"], "version": field["version"]}
