import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from apertus_motor_a import CONTRACT, SCENE, ApertusMotorAInterpreter, normalize_proposal, validate_proposal
from demo_runner import load_core
from rumenka_runner import proposal_gate

MODEL = "swiss-ai/Apertus-v1.5-8B"


def envelope(value, **message_extra):
    return {"model": MODEL, "choices": [{"finish_reason": "stop", "message": {
        "content": json.dumps(value, ensure_ascii=False), **message_extra}}]}


class MotorATests(unittest.TestCase):
    def test_valid_proposal_preserves_report_and_unknowns(self):
        proposal = normalize_proposal(envelope(CONTRACT), MODEL)
        self.assertEqual(proposal["observed"]["quote"], SCENE)
        self.assertIn("subject_type", proposal["unknown"])
        self.assertIs(proposal["relevant_known"]["diagnosis_confirmed"], False)

    def test_authoritative_or_invalid_output_rejected_before_core(self):
        invalid = []
        value = copy.deepcopy(CONTRACT)
        value["relevant_known"]["diagnosis_confirmed"] = True
        invalid.append(value)
        value = copy.deepcopy(CONTRACT)
        value["relevant_known"]["diagnosis"] = "mastitis"
        invalid.append(value)
        value = copy.deepcopy(CONTRACT)
        value["candidate_next_step"]["kind"] = "ADMINISTER_ANTIBIOTICS"
        invalid.append(value)
        value = copy.deepcopy(CONTRACT)
        value["candidate_next_step"]["execute"] = True
        invalid.append(value)
        value = copy.deepcopy(CONTRACT)
        value["observed"]["subject_type"] = "cow"
        invalid.append(value)
        value = copy.deepcopy(CONTRACT)
        value["unknown"].remove("cause")
        invalid.append(value)
        value = copy.deepcopy(CONTRACT)
        value["relevant_known"]["diagnosis_confirmed"] = 0
        invalid.append(value)
        with tempfile.TemporaryDirectory() as directory:
            core = load_core(Path(directory))
            core.reset_files()
            before = core.STATE_FILE.read_bytes(), core.FIELD_FILE.read_bytes(), core.TRACE_FILE.read_bytes()
            for value in invalid:
                with self.subTest(value=value), self.assertRaises(ValueError):
                    proposal_gate(core, value, MODEL)
                self.assertEqual(before, (core.STATE_FILE.read_bytes(), core.FIELD_FILE.read_bytes(), core.TRACE_FILE.read_bytes()))

    def test_untrusted_envelopes_rejected(self):
        cases = [envelope(CONTRACT, tool_calls=[{"function": {"name": "treat"}}]),
                 envelope(CONTRACT, function_call={"name": "treat"}),
                 envelope(CONTRACT, refusal="refused")]
        value = envelope(CONTRACT)
        value["model"] = "other-model"
        cases.append(value)
        value = envelope(CONTRACT)
        value["choices"][0]["finish_reason"] = "length"
        cases.append(value)
        for content in ["```json\n" + json.dumps(CONTRACT) + "\n```", '{"observed":{},"observed":{}}', "not JSON"]:
            value = envelope(CONTRACT)
            value["choices"][0]["message"]["content"] = content
            cases.append(value)
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_proposal(value, MODEL)

    def test_actual_core_gate_waits_without_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            core = load_core(Path(directory))
            core.reset_files()
            before = core.FIELD_FILE.read_bytes()
            gate = proposal_gate(core, copy.deepcopy(CONTRACT), MODEL)
            state = core.read_json(core.STATE_FILE)
            self.assertEqual(gate["status"], "AWAITING_AUTHORITY")
            self.assertEqual(gate["result"], "PARTIAL")
            self.assertFalse(gate["core_admitted_decision"])
            self.assertEqual(state["phase"], "READY")
            self.assertEqual(state["k"]["status"], "OPEN")
            self.assertIsNone(state["pending_action"])
            self.assertIsNone(state["execution"])
            self.assertIsNone(state["verification"])
            self.assertEqual(core.FIELD_FILE.read_bytes(), before)
            with self.assertRaises(core.GovernorReject):
                core.execute_pending(state, MODEL, core.real_file_tool)
            with self.assertRaises(core.GovernorReject):
                core.verify_readback(state)

    def test_transport_failure_has_no_local_proposal_fallback(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"CSCS_INFERENCE_API_KEY": "unit-test-only", "APERTUS_MODEL": MODEL}):
            interpreter = ApertusMotorAInterpreter(Path(directory))
            with patch.object(interpreter.transport, "request", side_effect=[
                    {"data": [{"id": MODEL}]}, OSError("completion transport unavailable")]) as request:
                with self.assertRaises(OSError):
                    interpreter.interpret(SCENE)
                self.assertEqual(request.call_count, 2)
                self.assertEqual(request.call_args.args[0], "/chat/completions")
            self.assertFalse((Path(directory) / "motor_a_completion.json").exists())

    def test_scope_and_existing_decision_cannot_be_replaced(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"CSCS_INFERENCE_API_KEY": "unit-test-only"}):
            interpreter = ApertusMotorAInterpreter(Path(directory))
            with self.assertRaises(ValueError):
                interpreter.interpret("another scene")
            core = load_core(Path(directory))
            state = core.reset_files()
            core.receive_motor_a_output(state, core.canonical_decision(), "canonical-motor-A")
            before = core.STATE_FILE.read_bytes()
            with self.assertRaises(core.GovernorReject):
                proposal_gate(core, copy.deepcopy(CONTRACT), MODEL)
            self.assertEqual(core.STATE_FILE.read_bytes(), before)
