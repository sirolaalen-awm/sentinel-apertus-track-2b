#!/usr/bin/env python3
"""Minimal Sentinel v2 FILE PORTABILITY benchmark (standard library only)."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import subprocess
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent
FIELD_FILE = ROOT / "field_state.json"
STATE_FILE = ROOT / "sentinel_state.json"
TRACE_FILE = ROOT / "trace.jsonl"
NEBIUS_BASE = "https://api.tokenfactory.nebius.com/v1"


class GovernorReject(RuntimeError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
    temporary.replace(path)


def trace(event: str, **data: Any) -> None:
    record = {"at": now(), "event": event, "pid": os.getpid(), **data}
    with TRACE_FILE.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def initial_field() -> dict[str, Any]:
    return {
        "field_id": "EU-AGRI-HR-001",
        "crop": "maize",
        "soil_moisture_pct": 18,
        "irrigation_threshold_pct": 25,
        "irrigation_state": "off",
        "version": 0,
    }


def initial_state() -> dict[str, Any]:
    return {
        "schema_version": "sentinel-v2-file-portability-1",
        "run_id": str(uuid.uuid4()),
        "k": {"id": "K-IRRIGATION-001", "status": "OPEN", "output_status": "NONE"},
        "phase": "READY",
        "decision": None,
        "pending_action": None,
        "execution": None,
        "verification": None,
        "active_motor": None,
        "handoff": None,
    }


def reset_files(clear_trace: bool = True) -> dict[str, Any]:
    field = initial_field()
    state = initial_state()
    write_json(FIELD_FILE, field)
    write_json(STATE_FILE, state)
    if clear_trace:
        TRACE_FILE.write_text("", encoding="utf-8")
    trace("RUN_STARTED", run_id=state["run_id"], k_id=state["k"]["id"])
    return state


def validate_decision(payload: dict[str, Any]) -> dict[str, Any]:
    required = {"k_id", "action", "path", "from", "to", "expected_effect"}
    if not required.issubset(payload):
        raise ValueError(f"partial output: missing {sorted(required - payload.keys())}")
    if payload != {
        "k_id": "K-IRRIGATION-001",
        "action": "set_irrigation",
        "path": "irrigation_state",
        "from": "off",
        "to": "on",
        "expected_effect": {"irrigation_state": "on"},
    }:
        raise ValueError("decision violates the locked benchmark contract")
    return payload


def receive_motor_a_output(state: dict[str, Any], payload: dict[str, Any], motor_id: str) -> None:
    state["active_motor"] = motor_id
    state["k"]["output_status"] = "OUTPUT_RECEIVED"
    trace("OUTPUT_RECEIVED", motor=motor_id, k_id=state["k"]["id"])
    write_json(STATE_FILE, state)
    try:
        decision = validate_decision(payload)
    except ValueError as error:
        trace("K_INCOMPLETE", motor=motor_id, k_id=state["k"]["id"], reason=str(error))
        write_json(STATE_FILE, state)
        raise
    state["k"]["output_status"] = "K_COMPLETE"
    state["phase"] = "DECIDED"
    state["decision"] = decision
    state["pending_action"] = {"action_id": "A1", **decision}
    trace("K_COMPLETE", motor=motor_id, k_id=state["k"]["id"])
    trace("DECIDED", motor=motor_id, action_id="A1")
    write_json(STATE_FILE, state)


def handoff(state: dict[str, Any], from_motor: str, to_motor: str) -> None:
    if state["phase"] != "DECIDED" or state["k"]["output_status"] != "K_COMPLETE":
        raise GovernorReject("handoff requires DECIDED and K_COMPLETE")
    state["active_motor"] = to_motor
    state["handoff"] = {"from": from_motor, "to": to_motor, "at": now()}
    trace("MOTOR_SWITCH", from_motor=from_motor, to_motor=to_motor, action_id="A1")
    write_json(STATE_FILE, state)


def reject_replanning(state: dict[str, Any], motor_id: str) -> None:
    if state["handoff"] and state["phase"] in {"DECIDED", "EXECUTED", "VERIFIED"}:
        trace("GOVERNOR_REJECT", motor=motor_id, attempt="REPLAN", reason="decision_locked")
        raise GovernorReject("re-planning after handoff is forbidden; execute pending_action")


def execute_pending(state: dict[str, Any], motor_id: str, tool: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
    if state["active_motor"] != motor_id or state["phase"] != "DECIDED":
        raise GovernorReject("only the active handoff motor may execute a DECIDED action")
    result = tool(state["pending_action"])
    state["execution"] = {"action_id": "A1", "motor": motor_id, "tool_result": result, "at": now()}
    state["phase"] = "EXECUTED"
    trace("EXECUTED", motor=motor_id, action_id="A1", tool_result=result)
    write_json(STATE_FILE, state)


def real_file_tool(action: dict[str, Any]) -> dict[str, Any]:
    field = read_json(FIELD_FILE)
    if field.get(action["path"]) != action["from"]:
        return {"success": False, "reason": "precondition_failed"}
    field[action["path"]] = action["to"]
    field["version"] += 1
    write_json(FIELD_FILE, field)
    return {"success": True, "reported_effect": {action["path"]: action["to"]}}


def lying_file_tool(_: dict[str, Any]) -> dict[str, Any]:
    return {"success": True, "reported_effect": {"irrigation_state": "on"}}


def verify_readback(state: dict[str, Any]) -> bool:
    if state["phase"] != "EXECUTED":
        raise GovernorReject("verification requires EXECUTED")
    observed = read_json(FIELD_FILE)
    expected = state["pending_action"]["expected_effect"]
    matches = all(observed.get(key) == value for key, value in expected.items())
    state["verification"] = {"expected_effect": expected, "observed_effect": {key: observed.get(key) for key in expected}, "matches": matches, "at": now()}
    if matches:
        state["phase"] = "VERIFIED"
        state["k"]["status"] = "CLOSED"
        trace("VERIFIED", action_id="A1", observed_effect=state["verification"]["observed_effect"])
    else:
        trace("VERIFICATION_FAILED", action_id="A1", expected_effect=expected, observed_effect=state["verification"]["observed_effect"])
    write_json(STATE_FILE, state)
    return matches


def canonical_decision() -> dict[str, Any]:
    return {"k_id": "K-IRRIGATION-001", "action": "set_irrigation", "path": "irrigation_state", "from": "off", "to": "on", "expected_effect": {"irrigation_state": "on"}}


def locked_digest(state: dict[str, Any]) -> str:
    locked = {key: state[key] for key in ("run_id", "decision", "pending_action")}
    return hashlib.sha256(json.dumps(locked, sort_keys=True).encode()).hexdigest()


def motor_a_process(remote: bool) -> bool:
    state = reset_files(clear_trace=False)
    if remote:
        model_a, model_b = select_nebius_models()
        payload = nebius_complete(os.environ["NEBIUS_API_KEY"], model_a, [
            {"role": "system", "content": "Return only the exact JSON decision required by this contract: " + json.dumps(canonical_decision())},
            {"role": "user", "content": json.dumps({"FIELD": read_json(FIELD_FILE), "Z": "irrigation_state=on"})},
        ])
    else:
        model_a, model_b = "mock-motor-A", "mock-motor-B"
        payload = canonical_decision()
    receive_motor_a_output(state, payload, model_a)
    handoff(state, model_a, model_b)
    state["process_a_pid"] = os.getpid()
    write_json(STATE_FILE, state)
    trace("HANDOFF_SAVED", run_id=state["run_id"], phase=state["phase"], locked_sha256=locked_digest(state))
    print(f"[MOTOR A] pid={os.getpid()} DECIDED; handoff saved; exiting")
    return True


def motor_b_process(remote: bool) -> bool:
    # No prompt history, IPC payload, parent object or trace is read here.
    state = read_json(STATE_FILE)
    field = read_json(FIELD_FILE)
    action = state.get("pending_action")
    if state.get("phase") != "DECIDED" or state["k"]["status"] != "OPEN" or state["k"]["output_status"] != "K_COMPLETE":
        raise GovernorReject("persisted handoff is not executable")
    validate_decision(state["decision"])
    if action != {"action_id": "A1", **state["decision"]}:
        raise GovernorReject("pending action differs from the saved decision")
    if not state.get("handoff") or state["active_motor"] != state["handoff"]["to"]:
        raise GovernorReject("invalid persisted motor ownership")
    if state.get("process_a_pid") == os.getpid():
        raise GovernorReject("Motor B must run in a new process")
    if field.get(action["path"]) != action["from"] or field.get("version") != 0:
        raise GovernorReject("FIELD does not satisfy the saved action precondition")
    motor_b = state["active_motor"]
    trace("HANDOFF_LOADED", run_id=state["run_id"], source_files=["sentinel_state.json", "field_state.json"], process_a_pid=state["process_a_pid"], locked_sha256=locked_digest(state))
    if remote:
        authorization = nebius_complete(os.environ["NEBIUS_API_KEY"], motor_b, [
            {"role": "system", "content": 'Execute the locked pending action without replanning. Return only {"execute":true,"action_id":"A1"}.'},
            {"role": "user", "content": json.dumps(state)},
        ])
        if authorization != {"execute": True, "action_id": "A1"}:
            trace("GOVERNOR_REJECT", motor=motor_b, attempt="INVALID_EXECUTION_AUTHORIZATION")
            raise GovernorReject("Motor B did not authorize the locked action")
        trace("EXECUTION_AUTHORIZED", motor=motor_b, action_id="A1")
    state["process_b_pid"] = os.getpid()
    execute_pending(state, motor_b, real_file_tool)
    passed = verify_readback(state)
    print(f"[MOTOR B] pid={os.getpid()} readback={'VERIFIED' if passed else 'FAIL'}")
    return passed


def isolated_run(remote: bool = False) -> bool:
    if remote and not os.getenv("NEBIUS_API_KEY"):
        trace("REMOTE_BLOCKED", reason="NEBIUS_API_KEY_not_available", api_request_sent=False)
        print("[BLOCKED] remote: NEBIUS_API_KEY is not available; no API request sent")
        return False
    mode = "remote" if remote else "mock"
    trace("ISOLATED_TEST_STARTED", mode=mode)
    first = subprocess.run([sys.executable, str(Path(__file__).resolve()), "_motor-a", "--remote-worker"] if remote else [sys.executable, str(Path(__file__).resolve()), "_motor-a"], check=False)
    trace("PROCESS_A_EXITED", mode=mode, returncode=first.returncode)
    if first.returncode != 0:
        return False
    checkpoint = read_json(STATE_FILE)
    before = read_json(FIELD_FILE)
    if checkpoint["phase"] != "DECIDED" or before["irrigation_state"] != "off":
        raise GovernorReject("invalid between-process checkpoint")
    expected_digest = locked_digest(checkpoint)
    run_id = checkpoint["run_id"]
    del checkpoint, before  # no state is passed to B; only command and credentials
    second = subprocess.run([sys.executable, str(Path(__file__).resolve()), "_motor-b", "--remote-worker"] if remote else [sys.executable, str(Path(__file__).resolve()), "_motor-b"], check=False)
    result = read_json(STATE_FILE)
    field = read_json(FIELD_FILE)
    passed = (second.returncode == 0 and result["run_id"] == run_id
              and locked_digest(result) == expected_digest
              and result["process_a_pid"] != result.get("process_b_pid")
              and result["phase"] == "VERIFIED" and result["k"]["status"] == "CLOSED"
              and result["verification"]["matches"] is True
              and field["irrigation_state"] == "on" and field["version"] == 1)
    trace("ISOLATED_TEST_RESULT", mode=mode, run_id=run_id, passed=passed,
          process_a_pid=result["process_a_pid"], process_b_pid=result.get("process_b_pid"),
          locked_sha256_unchanged=locked_digest(result) == expected_digest)
    print(f"[{'PASS' if passed else 'FAIL'}] isolated {mode} handoff: A exited before B started", flush=True)
    return passed


def mock_pass(verbose: bool = True, clear_trace: bool = True) -> bool:
    state = reset_files(clear_trace)
    receive_motor_a_output(state, canonical_decision(), "mock-motor-A")
    handoff(state, "mock-motor-A", "mock-motor-B")
    execute_pending(state, "mock-motor-B", real_file_tool)
    passed = verify_readback(state)
    if verbose:
        print("[PASS] mock motor switch: DECIDED -> EXECUTED -> VERIFIED")
    return passed


def fault_tests(clear_trace: bool = True) -> bool:
    results: list[tuple[str, bool]] = []

    state = reset_files(clear_trace)
    receive_motor_a_output(state, canonical_decision(), "mock-motor-A")
    handoff(state, "mock-motor-A", "mock-motor-B")
    try:
        reject_replanning(state, "mock-motor-B")
        results.append(("F1 re-planning rejected", False))
    except GovernorReject:
        results.append(("F1 re-planning rejected", state["phase"] == "DECIDED"))

    state = reset_files(False)
    receive_motor_a_output(state, canonical_decision(), "mock-motor-A")
    handoff(state, "mock-motor-A", "mock-motor-B")
    execute_pending(state, "mock-motor-B", lying_file_tool)
    results.append(("F2 tool success without effect is not VERIFIED", not verify_readback(state) and state["phase"] == "EXECUTED"))

    state = reset_files(False)
    partial = {"k_id": "K-IRRIGATION-001", "action": "set_irrigation"}
    try:
        receive_motor_a_output(state, partial, "mock-motor-A")
        results.append(("F3 partial output keeps K open", False))
    except ValueError:
        results.append(("F3 partial output keeps K open", state["k"]["status"] == "OPEN" and state["k"]["output_status"] == "OUTPUT_RECEIVED" and state["phase"] == "READY"))

    for label, passed in results:
        print(f"[{'PASS' if passed else 'FAIL'}] {label}")
    return all(passed for _, passed in results)


def extract_json(text: str) -> dict[str, Any]:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("model output contains no JSON object")
    value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("model output is not an object")
    return value


def nebius_complete(api_key: str, model: str, messages: list[dict[str, str]]) -> dict[str, Any]:
    body = json.dumps({"model": model, "messages": messages, "temperature": 0, "max_tokens": 300}).encode()
    request = urllib.request.Request(NEBIUS_BASE + "/chat/completions", data=body, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=90) as response:
        result = json.load(response)
    choice = result["choices"][0]
    trace("MODEL_RESPONSE_RECEIVED", requested_model=model, served_model=result.get("model"), response_id=result.get("id"), finish_reason=choice.get("finish_reason"))
    if choice.get("finish_reason") != "stop":
        raise ValueError("model generation incomplete; K must remain open")
    return extract_json(choice["message"]["content"])


def select_nebius_models() -> tuple[str, str]:
    request = urllib.request.Request(NEBIUS_BASE + "/models", headers={"Authorization": "Bearer " + os.environ["NEBIUS_API_KEY"]})
    with urllib.request.urlopen(request, timeout=60) as response:
        catalog = json.load(response)
    available = sorted({item["id"] for item in catalog["data"] if item["id"].lower().startswith("nvidia/") and "nemotron" in item["id"].lower()})
    trace("NEBIUS_MODELS_DISCOVERED", candidates=available)
    if len(available) < 2:
        raise ValueError(f"catalog exposes {len(available)} NVIDIA/Nemotron models; two distinct model IDs required")
    model_a = os.getenv("NEBIUS_MODEL_A") or available[0]
    model_b = os.getenv("NEBIUS_MODEL_B") or next(model for model in available if model != model_a)
    if model_a not in available or model_b not in available or model_a == model_b:
        raise ValueError("select two distinct NVIDIA/Nemotron IDs from the authenticated catalog")
    trace("NEBIUS_MODELS_SELECTED", motor_a=model_a, motor_b=model_b)
    return model_a, model_b


def remote_run() -> bool:
    return isolated_run(remote=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("mock-pass", "fault-tests", "all", "isolated", "remote", "_motor-a", "_motor-b"), nargs="?", default="all")
    parser.add_argument("--remote-worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.command == "_motor-a":
            return 0 if motor_a_process(args.remote_worker) else 1
        if args.command == "_motor-b":
            return 0 if motor_b_process(args.remote_worker) else 1
        if args.command == "isolated":
            return 0 if isolated_run() else 1
        if args.command == "mock-pass":
            return 0 if mock_pass() else 1
        if args.command == "fault-tests":
            return 0 if fault_tests() else 1
        if args.command == "remote":
            return 0 if remote_run() else 2
        TRACE_FILE.write_text("", encoding="utf-8")
        passed = mock_pass(clear_trace=False) and fault_tests(clear_trace=False)
        mock_pass(verbose=False, clear_trace=False)  # leave files successful; retain full suite audit
        return 0 if passed else 1
    except (GovernorReject, ValueError, KeyError, json.JSONDecodeError, urllib.error.URLError) as error:
        trace("RUN_FAILED", error=type(error).__name__, detail=str(error))
        print(f"[FAIL] {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
