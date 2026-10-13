"""Scene-specific proposal interpreter. No diagnosis, tools or execution authority."""
import json

from apertus_motor_b import ApertusMotorBAdapter, unique_object

SCENE = "Rumenka je betežna."
UNKNOWN = ["subject_type", "symptoms", "onset", "severity", "cause", "farmer_vet_authority"]
QUESTIONS = ["who_is_rumenka", "what_was_observed_and_since_when", "who_is_responsible"]

# Closed vocabulary deliberately prevents model-authored diagnoses, prescriptions
# and imperative tool actions from entering the downstream gate.
CONTRACT = {
    "observed": {
        "quote": SCENE,
        "subject": "Rumenka",
        "reported_condition": "betežna",
        "source": "USER_REPORT",
    },
    "relevant_known": {
        "language": "hr",
        "reported_meaning": "unwell",
        "diagnosis_confirmed": False,
    },
    "unknown": UNKNOWN,
    "candidate_next_step": {
        "kind": "REQUEST_CONTEXT",
        "questions": QUESTIONS,
        "requires_authority": "FARMER_OR_VET",
    },
}


def validate_proposal(value):
    if not isinstance(value, dict) or set(value) != set(CONTRACT):
        raise ValueError("proposal must contain only the four permitted sections")
    for section in ("observed", "relevant_known", "candidate_next_step"):
        expected = CONTRACT[section]
        actual = value[section]
        if not isinstance(actual, dict) or set(actual) != set(expected):
            raise ValueError("invalid proposal section: " + section)
        for key, item in expected.items():
            supplied = actual[key]
            if type(supplied) is not type(item):
                raise ValueError("invalid proposal type: " + key)
            if isinstance(item, list):
                if (not all(isinstance(x, str) for x in supplied)
                        or len(supplied) != len(set(supplied))
                        or set(supplied) != set(item)):
                    raise ValueError("invalid context questions")
            elif supplied != item:
                raise ValueError("unsupported or authoritative claim: " + key)
    unknown = value["unknown"]
    if (not isinstance(unknown, list) or not all(isinstance(x, str) for x in unknown)
            or len(unknown) != len(set(unknown)) or set(unknown) != set(UNKNOWN)):
        raise ValueError("missing or invented scene knowledge")
    return value


def normalize_proposal(response, expected_model):
    if response.get("model") != expected_model:
        raise ValueError("served Motor A model differs from configured model")
    choices = response.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise ValueError("one complete proposal required")
    choice = choices[0]
    if choice.get("finish_reason") != "stop":
        raise ValueError("incomplete proposal")
    message = choice.get("message", {})
    if message.get("tool_calls") or message.get("function_call") or message.get("refusal"):
        raise ValueError("Motor A cannot call tools or supply an alternative response")
    if not isinstance(message.get("content"), str):
        raise ValueError("proposal content must be JSON text")
    return validate_proposal(json.loads(message["content"], object_pairs_hook=unique_object))


class ApertusMotorAInterpreter:
    def __init__(self, evidence_dir):
        # Reuse authenticated CSCS transport, not Motor B's authorization contract.
        self.transport = ApertusMotorBAdapter(evidence_dir)
        self.model = self.transport.model
        self.evidence_dir = evidence_dir

    def interpret(self, scene):
        if scene != SCENE:
            raise ValueError("only the requested Rumenka scene is supported")
        catalog = self.transport.request("/models")
        if self.model not in {item["id"] for item in catalog["data"]}:
            raise ValueError("configured Apertus model absent from authenticated catalog")
        if "apertus" not in self.model.lower() or "8b" not in self.model.lower():
            raise ValueError("this scene interpreter requires the existing Apertus 8B motor")
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        (self.evidence_dir / "models.json").write_text(json.dumps(catalog, indent=2), encoding="utf-8")
        instructions = (
            "Interpret only the Croatian user report. Return one JSON proposal with "
            "only observed, relevant_known, unknown, candidate_next_step. "
            "A report of being unwell is not a diagnosis. Rumenka's species, symptoms, "
            "cause and severity are unknown. Do not diagnose, treat, prescribe, "
            "execute, claim an effect, or call tools. The candidate only requests "
            "context for a responsible farmer/vet; no authority has been supplied. "
            "Use exactly the following closed vocabulary and shape, with all unknowns "
            "preserved. No prose or code fences: " + json.dumps(CONTRACT, ensure_ascii=False)
        )
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": instructions},
                         {"role": "user", "content": scene}],
            "temperature": 0,
            "max_tokens": 512,
        }
        # No local proposal substitution, repair, retries with mocks or fallback.
        response = self.transport.request("/chat/completions", body)
        (self.evidence_dir / "motor_a_completion.json").write_text(
            json.dumps({"request": body, "response": response}, indent=2, ensure_ascii=False),
            encoding="utf-8")
        return normalize_proposal(response, self.model)
