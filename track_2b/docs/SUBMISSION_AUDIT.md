# Submission audit
Official deadline: 16 October 2026, 12:00 CEST. Official submission: https://hackapertus.ch/online-hack/submissions (not Devpost).
The source tree preserves track_2b and excludes the other tracks, development media/toolchains and generated receipts.
Runtime: standard-library Python 3.12+. Docker build uses python:3.12-slim and runs the existing tests. Runtime external dependencies: CSCS HTTPS/API credential only. Build external dependency: Docker base-image registry.
Official LLM_NAME/LLM_BASE_URL/LLM_API_KEY names map at the packaging entrypoint to existing CSCS variables. No CORE semantics changed.
Docker build/run: UNVERIFIED because Docker is unavailable. Static COPY/CMD/context inspection and Make dry-run are not execution proof.
Native clean-state flow and independent real Motor B regression: existing LAST GOOD PASS. Existing tests are rerun in this final package.
Mandatory architecture: on-premise process/container under operator administration with sovereign Swiss-hosted CSCS inference; air-gapped not implemented. Full application deployment in a Swiss cloud has not been executed. No claim of verified end-to-end Swiss residency is made for this local test host.
Eligibility gates: age/residence/registration, Swiss student status, team completeness, ownership and compatibility of pre-existing CORE reuse require truthful human declarations. No declaration is fabricated.
Video audio redistribution rights remain UNCONFIRMED; private candidate upload requires account/extension access. A working public repo/video URL remains a live platform gate.
