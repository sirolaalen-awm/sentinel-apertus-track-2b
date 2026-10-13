"""Real irrigation execution + prior real scene proposal through the same gate."""
from dataclasses import asdict
import hashlib
import json
import uuid

from apertus_motor_a import ApertusMotorAInterpreter, SCENE
from apertus_motor_b import ApertusMotorBAdapter
from demo_runner import ROOT, EXPECTED_HASH, core_hash
from generic_transition import AuthorityDecision, TransitionProcess
from transition_adapters import IrrigationAdapter, IRRIGATION_ADAPTER, irrigation_transition, rumenka_transition

def write(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def run():
    if core_hash() != EXPECTED_HASH:
        raise ValueError("CORE hash mismatch")
    run_dir = ROOT / "data" / ("generic-" + str(uuid.uuid4()))
    run_dir.mkdir(parents=True)
    receipt = {"result": "FAIL", "passed": False, "core_sha256": core_hash(),
               "mock_fallback": False, "provenance": {
                   str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                   for path in sorted((ROOT / "src").rglob("*.py"))}}
    try:
        transport = ApertusMotorBAdapter(run_dir / "evidence")
        transport.probe()
        proposal = transport.complete([
            {"role": "system", "content": 'Propose the existing locked irrigation action A1 only. Return only {"execute":true,"action_id":"A1"}. This is a proposal, not human authority.'},
            {"role": "user", "content": '{"subject":"EU-AGRI-HR-001","irrigation_state":"off","version":0,"candidate_action":"A1"}'}
        ], "irrigation_proposal")
        irrigation = irrigation_transition(proposal)
        adapter = IrrigationAdapter(run_dir / "irrigation", transport.model)
        process = TransitionProcess(irrigation, {IRRIGATION_ADAPTER: adapter})
        write(run_dir / "irrigation_proposed.json", process.receipt())
        # Explicit current user instruction authorizes this isolated benchmark.
        # Model proposal/confirmation cannot supply this operator decision.
        decision = AuthorityDecision(irrigation.transition_id, irrigation.digest(),
            irrigation.authority_required, "CLI_OPERATOR",
            "operator invocation of documented isolated benchmark; no physical farm authority", True)
        process.authorize(decision)
        write(run_dir / "irrigation_authorized.json", process.receipt())
        process.execute()
        write(run_dir / "irrigation_executed.json", process.receipt())
        if not process.verify():
            raise ValueError("generic irrigation readback mismatch")
        write(run_dir / "irrigation_transition.json", process.receipt())
        # Clean submission runs must create their own real proposal evidence.
        interpreter = ApertusMotorAInterpreter(run_dir / "rumenka/evidence")
        scene_proposal = interpreter.interpret(SCENE)
        write(run_dir / "rumenka/proposal.json", scene_proposal)
        scene = TransitionProcess(rumenka_transition(scene_proposal), {IRRIGATION_ADAPTER: adapter})
        write(run_dir / "rumenka_proposed.json", scene.receipt())
        scene.authorize()
        write(run_dir / "rumenka_transition.json", scene.receipt())
        checks = {
            "existing_real_irrigation_pass": adapter.execution_receipt["passed"],
            "irrigation_generic_verified": process.transition.status == "VERIFIED",
            "generic_lock_unchanged": len({item["locked_sha256"] for item in process.trace}) == 1,
            "irrigation_status_order": [item["status"] for item in process.trace] == ["PROPOSED", "AUTHORIZED", "EXECUTED", "VERIFIED"],
            "same_transition_contract": set(asdict(process.transition)) == set(asdict(scene.transition)),
            "rumenka_awaiting_authority": scene.transition.status == "AWAITING_AUTHORITY",
            "rumenka_no_execution_readback": scene.execution is None and scene.readback is None and scene.authority is None,
            "rumenka_never_entered_core": not (run_dir / "rumenka/sentinel_state.json").exists(),
            "core_unchanged": core_hash() == EXPECTED_HASH,
        }
        if not all(checks.values()):
            raise ValueError("transition acceptance checks failed")
        receipt.update(result="PASS", passed=True, checks=checks,
            irrigation_result="PASS", rumenka_result="PARTIAL / AWAITING_AUTHORITY",
            rumenka_source="fresh real Apertus Motor A inference in this run",
            execution_adapter_count=1, core_generalization_evidence=False)
    except Exception as error:
        receipt["error_type"] = type(error).__name__
    receipt["core_sha256_after"] = core_hash()
    write(run_dir / "receipt.json", receipt)
    readback = json.loads((run_dir / "receipt.json").read_text())
    print(json.dumps({"result": readback["result"], "irrigation": readback.get("irrigation_result"),
                     "rumenka": readback.get("rumenka_result"),
                     "receipt": str((run_dir / "receipt.json").relative_to(ROOT))}, indent=2))
    return 0 if readback["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(run())
