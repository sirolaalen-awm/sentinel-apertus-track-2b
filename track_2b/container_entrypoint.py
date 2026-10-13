"""Environment-name bridge required by the official template; no model fallback."""
import os
from pathlib import Path
import runpy
import sys

for public, internal in (("LLM_API_KEY", "CSCS_INFERENCE_API_KEY"),
                         ("LLM_NAME", "APERTUS_MODEL"),
                         ("LLM_BASE_URL", "CSCS_BASE_URL")):
    if os.environ.get(public) and not os.environ.get(internal):
        os.environ[internal] = os.environ[public]
if not (os.environ.get("CSCS_INFERENCE_API_KEY") or os.environ.get("CSCS_API_KEY")):
    print("FAIL: supply LLM_API_KEY or CSCS_INFERENCE_API_KEY as a runtime environment secret.", file=sys.stderr)
    raise SystemExit(2)
source = Path(__file__).resolve().parent / "src"
sys.path.insert(0, str(source))
runpy.run_path(str(source / "transition_runner.py"), run_name="__main__")
