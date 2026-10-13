"""NEW: isolated orchestration around byte-identical pre-existing CORE."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

from apertus_motor_b import ApertusMotorBAdapter

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "src/sentinel_core/sentinel.py"
EXPECTED_HASH = "3394a5b975998c802486eff3d61a8877346cb669f417bd0284fa38d0c726fce9"


def core_hash():
    return hashlib.sha256(CORE.read_bytes()).hexdigest()


def load_core(run):
    if core_hash() != EXPECTED_HASH:
        raise ValueError("CORE provenance hash mismatch")
    spec = importlib.util.spec_from_file_location("sentinel_core", CORE)
    core = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(core)
    # Redirect only filesystem locations; CORE source and transition functions stay intact.
    core.FIELD_FILE = run / "field_state.json"
    core.STATE_FILE = run / "sentinel_state.json"
    core.TRACE_FILE = run / "trace.jsonl"
    return core


def worker(args):
    core = load_core(args.run_dir)
    motor = "mock-motor-B" if args.mode == "baseline" else os.getenv("APERTUS_MODEL", "swiss-ai/Apertus-v1.5-8B")
    if args.worker == "a":
        state = core.reset_files()
        core.receive_motor_a_output(state, core.canonical_decision(), "canonical-motor-A")
        core.handoff(state, "canonical-motor-A", motor)
        state["process_a_pid"] = os.getpid()
        core.write_json(core.STATE_FILE, state)
        core.trace("HANDOFF_SAVED", run_id=state["run_id"], locked_sha256=core.locked_digest(state))
        return 0
    if args.mode == "apertus":
        adapter = ApertusMotorBAdapter(args.run_dir / "evidence")
        core.nebius_complete = adapter.core_transport
        # Existing remote worker reads this variable; the adapter uses only CSCS_API_KEY.
        os.environ["NEBIUS_API_KEY"] = "adapter-owned-transport"
    return 0 if core.motor_b_process(args.mode == "apertus") else 1


def run_demo(args):
    if core_hash() != EXPECTED_HASH:
        raise ValueError("CORE provenance hash mismatch")
    if args.mode == "apertus" and not (os.getenv("CSCS_INFERENCE_API_KEY") or os.getenv("CSCS_API_KEY")):
        print("BLOCKED: CSCS_INFERENCE_API_KEY missing; no API request or demo execution sent")
        return 2
    run = ROOT / "data" / (args.mode + "-" + str(uuid.uuid4()))
    run.mkdir(parents=True)
    receipt = {"mode": args.mode, "passed": False, "core_sha256": core_hash()}
    try:
        if args.mode == "apertus":
            ApertusMotorBAdapter(run / "evidence").probe()
        command = [sys.executable, "-B", str(Path(__file__).resolve()), "--mode", args.mode, "--run-dir", str(run)]
        subprocess.run(command + ["--worker", "a"], check=True)
        core = load_core(run)
        before = core.read_json(core.STATE_FILE)
        field_before = core.read_json(core.FIELD_FILE)
        digest = core.locked_digest(before)
        if before["phase"] != "DECIDED" or field_before["irrigation_state"] != "off" or field_before["version"] != 0:
            raise ValueError("invalid persisted checkpoint")
        core.trace("PROCESS_A_EXITED", returncode=0)
        subprocess.run(command + ["--worker", "b"], check=True)
        state, field = core.read_json(core.STATE_FILE), core.read_json(core.FIELD_FILE)
        checks = {
            "run_id_unchanged": before["run_id"] == state["run_id"],
            "locked_sha256_unchanged": digest == core.locked_digest(state),
            "different_processes": state["process_a_pid"] != state["process_b_pid"],
            "core_source_unchanged": core_hash() == EXPECTED_HASH,
            "field_on": field["irrigation_state"] == "on",
            "field_version_one": field["version"] == 1,
            "verified": state["phase"] == "VERIFIED" and state["verification"]["matches"] is True,
            "k_closed": state["k"]["status"] == "CLOSED",
            "state_keys_unchanged": set(state) == set(before) | {"process_b_pid"},
        }
        receipt["real_apertus_response"] = args.mode == "apertus" and (run / "evidence/motor_b_completion.json").is_file()
        if args.mode == "apertus":
            checks["real_response_present"] = receipt["real_apertus_response"]
        receipt.update(run_id=state["run_id"], locked_sha256=digest, checks=checks, passed=all(checks.values()))
    except Exception as error:
        receipt["error"] = type(error).__name__ + ": " + str(error)
    (run / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps({"passed": receipt["passed"], "mode": args.mode, "receipt": str(run / "receipt.json")}, indent=2))
    return 0 if receipt["passed"] else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["apertus", "baseline"], default="apertus")
    parser.add_argument("--worker", choices=["a", "b"])
    parser.add_argument("--run-dir", type=Path)
    args = parser.parse_args()
    return worker(args) if args.worker else run_demo(args)


if __name__ == "__main__":
    sys.exit(main())
