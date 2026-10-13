"""Small hack layer: real Motor A proposal -> existing CORE gate -> authority wait."""
import hashlib
import json
import os
import uuid

from apertus_motor_a import ApertusMotorAInterpreter, SCENE, validate_proposal
from demo_runner import ROOT, EXPECTED_HASH, core_hash, load_core

def proposal_gate(core, proposal, motor):
    validate_proposal(proposal)
    state = core.read_json(core.STATE_FILE)
    if state["phase"] != "READY" or state["decision"] is not None or state["pending_action"] is not None:
        raise core.GovernorReject("scene proposal requires a fresh undecided CORE fixture")
    # CORE has a fixed irrigation-only decision contract. Submit the proposal to
    # its original intake, retaining the real rejection; never coerce a health
    # report into canonical_decision() or give it a fictitious execution owner.
    try:
        core.receive_motor_a_output(state, proposal, motor)
    except ValueError as error:
        core.trace("PROPOSAL_AWAITING_AUTHORITY", motor=motor,
                   reason="no_farmer_vet_execution_carrier", core_gate="K_INCOMPLETE")
        return {
            "status": "AWAITING_AUTHORITY",
            "result": "PARTIAL",
            "core_admitted_decision": False,
            "core_gate_reason": str(error),
            "execution_carrier": None,
            "limitations": ["NO_FARMER_VET_AUTHORITY_OR_EXECUTION_CARRIER",
                            "CORE_ACCEPTS_ONLY_LOCKED_IRRIGATION_DECISIONS"],
        }
    raise core.GovernorReject("unexpected admission of scene proposal as an executable decision")


def run_scene():
    if core_hash() != EXPECTED_HASH:
        raise ValueError("CORE provenance hash mismatch")
    if not (os.getenv("CSCS_INFERENCE_API_KEY") or os.getenv("CSCS_API_KEY")):
        print(json.dumps({"result": "UNKNOWN", "reason": "temporary CSCS credential unavailable"}))
        return 2
    run = ROOT / "data" / ("rumenka-" + str(uuid.uuid4()))
    run.mkdir(parents=True)
    receipt = {
        "scene": SCENE, "result": "FAIL", "passed": False,
        "scope": "Motor A proposal and authority gate; no animal/patient execution",
        "core_sha256": core_hash(), "mock_fallback": False,
    }
    try:
        interpreter = ApertusMotorAInterpreter(run / "evidence")
        proposal = interpreter.interpret(SCENE)
        (run / "proposal.json").write_text(json.dumps(proposal, indent=2, ensure_ascii=False), encoding="utf-8")
        core = load_core(run)
        # This remains the existing benchmark fixture, not a representation of
        # Rumenka's health. No physical/clinical FIELD is supplied or fabricated.
        core.reset_files()
        field_before = core.FIELD_FILE.read_bytes()
        gate = proposal_gate(core, proposal, interpreter.model)
        core.write_json(run / "authority_gate.json", gate)
        state = core.read_json(core.STATE_FILE)
        trace = [json.loads(line) for line in core.TRACE_FILE.read_text(encoding="utf-8").splitlines()]
        events = [item["event"] for item in trace]
        checks = {
            "proposal_from_real_apertus": (run / "evidence/motor_a_completion.json").is_file(),
            "proposal_contract_valid": validate_proposal(proposal) == proposal,
            "core_source_unchanged": core_hash() == EXPECTED_HASH,
            "core_rejected_executable_decision": "K_INCOMPLETE" in events and not gate["core_admitted_decision"],
            "awaiting_authority": gate["status"] == "AWAITING_AUTHORITY" and gate["execution_carrier"] is None,
            "no_locked_action": state["decision"] is None and state["pending_action"] is None,
            "no_execution_or_verification": state["execution"] is None and state["verification"] is None
                and not any(e in events for e in ("DECIDED", "EXECUTED", "VERIFIED", "EXECUTION_AUTHORIZED", "MOTOR_SWITCH")),
            "core_ready_k_open": state["phase"] == "READY" and state["k"]["status"] == "OPEN",
            "benchmark_fixture_unchanged": core.FIELD_FILE.read_bytes() == field_before,
        }
        if not all(checks.values()):
            raise ValueError("proposal/authority readback failed")
        receipt.update(result="PARTIAL", status="AWAITING_AUTHORITY", checks=checks,
                       interpreter_and_gate_pass=True, run_id=state["run_id"], model=interpreter.model,
                       benchmark_field_role="existing irrigation harness fixture, not Rumenka",
                       first_unconfirmed="subject identity/context and legitimate farmer/vet carrier; scene is not an executable CORE contract",
                       next_k="human clarification and named authority/carrier before any scene action is considered")
    except Exception as error:
        # Do not retain arbitrary exception text that might contain transport secrets.
        receipt["error_type"] = type(error).__name__
    core_hash_after = core_hash()
    receipt["core_sha256_after"] = core_hash_after
    (run / "receipt.json").write_text(json.dumps(receipt, indent=2, ensure_ascii=False), encoding="utf-8")
    readback = json.loads((run / "receipt.json").read_text(encoding="utf-8"))
    print(json.dumps({"result": readback["result"], "status": readback.get("status"),
                      "interpreter_and_gate_pass": readback.get("interpreter_and_gate_pass", False),
                      "receipt": str(run / "receipt.json")}, indent=2))
    return 0 if readback.get("interpreter_and_gate_pass") else 1


if __name__ == "__main__":
    raise SystemExit(run_scene())
