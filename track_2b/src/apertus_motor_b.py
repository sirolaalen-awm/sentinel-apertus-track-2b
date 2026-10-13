"""NEW: CSCS transport; no decision or execution authority."""
import json
import os
import urllib.request

CONTRACT = {"execute": True, "action_id": "A1"}


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def normalize(response):
    choice = response["choices"][0]
    if choice.get("finish_reason") != "stop":
        raise ValueError("incomplete completion")
    # Strict JSON, no prose extraction, tools, extra fields, or coercion.
    value = json.loads(choice["message"]["content"], object_pairs_hook=unique_object)
    if (not isinstance(value, dict) or set(value) != set(CONTRACT)
            or value["execute"] is not True or value["action_id"] != "A1"):
        raise ValueError("Motor B authorization rejected")
    return value


class ApertusMotorBAdapter:
    def __init__(self, evidence_dir):
        self.base = os.getenv("CSCS_BASE_URL", "https://api.inference.cscs.ch/v1").rstrip("/")
        self.model = os.getenv("APERTUS_MODEL", "swiss-ai/Apertus-v1.5-8B")
        self.key = os.getenv("CSCS_INFERENCE_API_KEY") or os.environ["CSCS_API_KEY"]
        self.evidence_dir = evidence_dir

    def request(self, route, body=None):
        request = urllib.request.Request(self.base + route,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Authorization": "Bearer " + self.key, "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=60) as handle:
            return json.load(handle)

    def complete(self, messages, name):
        body = {"model": self.model, "messages": messages, "temperature": 0, "max_tokens": 128}
        response = self.request("/chat/completions", body)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        (self.evidence_dir / (name + ".json")).write_text(
            json.dumps({"request": body, "response": response}, indent=2), encoding="utf-8")
        return normalize(response)

    def probe(self):
        catalog = self.request("/models")
        if self.model not in {item["id"] for item in catalog["data"]}:
            raise ValueError("configured model absent from authenticated catalog")
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        (self.evidence_dir / "models.json").write_text(json.dumps(catalog, indent=2), encoding="utf-8")
        messages = [{"role": "user", "content": 'Return only {"execute":true,"action_id":"A1"}.'}]
        self.complete(messages, "minimal_completion")
        self.complete([
            {"role": "system", "content": 'Confirm locked A1 without replanning. Return only {"execute":true,"action_id":"A1"}.'},
            {"role": "user", "content": json.dumps({"phase": "DECIDED", "pending_action": {"action_id": "A1"}})}
        ], "contract_probe")

    def core_transport(self, ignored_key, model, messages):
        if model != self.model:
            raise ValueError("motor identity differs from configured Apertus model")
        return self.complete(messages, "motor_b_completion")
