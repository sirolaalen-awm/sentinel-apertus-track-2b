import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from apertus_motor_b import normalize
from demo_runner import load_core


def response(value, finish="stop"):
    return {"choices": [{"finish_reason": finish, "message": {"content": value}}]}


class BoundaryTests(unittest.TestCase):
    def test_remote_core_seam_and_rejection(self):
        # Simulated transport only; this is explicitly not a real Apertus result.
        for authorization, accepted in [({"execute": True, "action_id": "A1"}, True),
                                        ({"execute": False, "action_id": "A1"}, False)]:
            with tempfile.TemporaryDirectory() as directory:
                core = load_core(Path(directory))
                state = core.reset_files()
                core.receive_motor_a_output(state, core.canonical_decision(), "A")
                core.handoff(state, "A", "B")
                state["process_a_pid"] = -1
                core.write_json(core.STATE_FILE, state)
                with patch.dict("os.environ", {"NEBIUS_API_KEY": "test-only"}), patch.object(
                        core, "nebius_complete", return_value=authorization):
                    if accepted:
                        self.assertTrue(core.motor_b_process(True))
                        self.assertEqual(core.read_json(core.STATE_FILE)["phase"], "VERIFIED")
                    else:
                        with self.assertRaises(core.GovernorReject):
                            core.motor_b_process(True)
                        self.assertEqual(core.read_json(core.FIELD_FILE)["version"], 0)
                        self.assertEqual(core.read_json(core.STATE_FILE)["phase"], "DECIDED")

    def test_exact_response(self):
        self.assertEqual(normalize(response('{"execute":true,"action_id":"A1"}')), {"execute": True, "action_id": "A1"})

    def test_reject_invalid_authorization(self):
        for value in ['{"execute":1,"action_id":"A1"}', '{"execute":false,"action_id":"A1"}',
                      '{"execute":true,"action_id":"A2"}', '{"execute":true,"action_id":"A1","to":"off"}',
                      '```json\n{"execute":true,"action_id":"A1"}\n```', '{}',
                      '{"execute":false,"execute":true,"action_id":"A1"}']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize(response(value))
        with self.assertRaises(ValueError):
            normalize(response('{"execute":true,"action_id":"A1"}', "length"))

    def test_false_tool_success_never_closes_k(self):
        with tempfile.TemporaryDirectory() as directory:
            core = load_core(Path(directory))
            state = core.reset_files()
            core.receive_motor_a_output(state, core.canonical_decision(), "A")
            core.handoff(state, "A", "B")
            core.execute_pending(state, "B", core.lying_file_tool)
            self.assertFalse(core.verify_readback(state))
            self.assertEqual(state["phase"], "EXECUTED")
            self.assertEqual(state["k"]["status"], "OPEN")

    def test_wrong_owner_cannot_execute(self):
        with tempfile.TemporaryDirectory() as directory:
            core = load_core(Path(directory))
            state = core.reset_files()
            core.receive_motor_a_output(state, core.canonical_decision(), "A")
            core.handoff(state, "A", "B")
            with self.assertRaises(core.GovernorReject):
                core.execute_pending(state, "C", core.real_file_tool)
            self.assertEqual(core.read_json(core.FIELD_FILE)["version"], 0)
