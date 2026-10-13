from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import copy

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from apertus_motor_a import CONTRACT
from generic_transition import AuthorityDecision, ExecutionPermit, TransitionProcess, TransitionReject, require_permit
from transition_adapters import IrrigationAdapter, IRRIGATION_ADAPTER, irrigation_transition, rumenka_transition


def decision(t):
    return AuthorityDecision(t.transition_id, t.digest(), t.authority_required,
                             "UNIT_TEST_OPERATOR", "unit test only", True)


class TestIrrigationAdapter:
    """Unit-test double only. Not available in the real runner registry."""
    adapter_id = IRRIGATION_ADAPTER

    def __init__(self, observed=None):
        self.calls = 0
        self.observed = observed or {"irrigation_state": "on", "version": 1}

    def validate(self, t):
        IrrigationAdapter(Path("not-used"), "not-used").validate(t)

    def execute(self, t, permit):
        require_permit(permit, self.adapter_id, t)
        self.calls += 1
        return {"executed": True, "evidence_scope": "unit_test_double"}

    def readback(self, t):
        return self.observed


class GenericTransitionTests(unittest.TestCase):
    def setUp(self):
        self.t = irrigation_transition({"execute": True, "action_id": "A1"})
        self.adapter = TestIrrigationAdapter()

    def test_unknown_adapter_cannot_execute_even_with_authority(self):
        t = replace(self.t, execution_adapter="unregistered")
        p = TransitionProcess(t, {IRRIGATION_ADAPTER: self.adapter})
        self.assertFalse(p.authorize(decision(t)))
        self.assertEqual(p.transition.status, "AWAITING_AUTHORITY")
        with self.assertRaises(TransitionReject):
            p.execute()
        self.assertEqual(self.adapter.calls, 0)

    def test_missing_authority_never_executes(self):
        p = TransitionProcess(self.t, {IRRIGATION_ADAPTER: self.adapter})
        self.assertFalse(p.authorize())
        with self.assertRaises(TransitionReject):
            p.execute()
        self.assertIsNone(p.execution)
        self.assertEqual(self.adapter.calls, 0)

    def test_real_adapter_cannot_bypass_gate_with_missing_or_forged_permit(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = IrrigationAdapter(Path(directory) / "core", "swiss-ai/Apertus-v1.5-8B")
            with patch("transition_adapters.subprocess.run") as worker:
                for permit in (None, object(), ExecutionPermit()):
                    with self.subTest(permit=type(permit).__name__), self.assertRaises(TransitionReject):
                        adapter.execute(replace(self.t, status="AUTHORIZED"), permit)
                worker.assert_not_called()
            self.assertFalse(adapter.run.exists())

    def test_invalid_transition_never_enters_core(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = IrrigationAdapter(Path(directory) / "core", "swiss-ai/Apertus-v1.5-8B")
            with patch("transition_adapters.subprocess.run") as worker:
                for t in [replace(self.t, subject=""), replace(self.t, proposed_action={}),
                          replace(self.t, transition_id="invalid"), replace(self.t, status="EXECUTED")]:
                    with self.subTest(t=t), self.assertRaises(TransitionReject):
                        TransitionProcess(t, {IRRIGATION_ADAPTER: adapter})
                for action, observed in [({"action_type": "unauthorized_action"}, self.t.observed_state),
                                         (self.t.proposed_action, {"irrigation_state": "off", "version": False})]:
                    t = replace(self.t, proposed_action=action, observed_state=observed)
                    p = TransitionProcess(t, {IRRIGATION_ADAPTER: adapter})
                    with self.assertRaises(TransitionReject):
                        p.authorize(decision(t))
                worker.assert_not_called()
            self.assertFalse(adapter.run.exists())

    def test_authority_is_bound_to_lock_actor_and_scope(self):
        for d in [replace(decision(self.t), approved=False), replace(decision(self.t), approved=1),
                  replace(decision(self.t), transition_sha256="wrong"),
                  replace(decision(self.t), authority="other"), replace(decision(self.t), actor="")]:
            p = TransitionProcess(self.t, {IRRIGATION_ADAPTER: self.adapter})
            with self.subTest(d=d), self.assertRaises(TransitionReject):
                p.authorize(d)
        self.assertEqual(self.adapter.calls, 0)

    def test_mutated_locked_transition_cannot_execute(self):
        p = TransitionProcess(self.t, {IRRIGATION_ADAPTER: self.adapter})
        p.authorize(decision(self.t))
        p.transition.proposed_action["to"] = "off"
        with self.assertRaises(TransitionReject):
            p.execute()
        self.assertEqual(self.adapter.calls, 0)

    def test_readback_mismatch_never_verifies(self):
        adapter = TestIrrigationAdapter({"irrigation_state": "off", "version": 0})
        p = TransitionProcess(self.t, {IRRIGATION_ADAPTER: adapter})
        p.authorize(decision(self.t))
        p.execute()
        self.assertFalse(p.verify())
        self.assertEqual(p.transition.status, "EXECUTED")

    def test_separate_phases_and_no_second_execution(self):
        p = TransitionProcess(self.t, {IRRIGATION_ADAPTER: self.adapter})
        with self.assertRaises(TransitionReject):
            p.verify()
        p.authorize(decision(self.t))
        self.assertEqual(p.transition.status, "AUTHORIZED")
        self.assertIsNone(p.execution)
        p.execute()
        self.assertEqual(p.transition.status, "EXECUTED")
        self.assertIsNone(p.readback)
        self.assertTrue(p.verify())
        with self.assertRaises(TransitionReject):
            p.execute()
        self.assertEqual([e["status"] for e in p.trace], ["PROPOSED", "AUTHORIZED", "EXECUTED", "VERIFIED"])
        self.assertEqual(len({e["locked_sha256"] for e in p.trace}), 1)
        self.assertEqual(self.adapter.calls, 1)

    def test_rumenka_same_contract_stops_without_carrier_or_core(self):
        t = rumenka_transition(copy.deepcopy(CONTRACT))
        self.assertEqual(set(t.__dataclass_fields__), set(self.t.__dataclass_fields__))
        p = TransitionProcess(t, {IRRIGATION_ADAPTER: self.adapter})
        self.assertFalse(p.authorize())
        self.assertEqual(p.transition.status, "AWAITING_AUTHORITY")
        with self.assertRaises(TransitionReject):
            p.execute()
        self.assertIsNone(p.execution)
        self.assertIsNone(p.readback)
        self.assertEqual(self.adapter.calls, 0)
