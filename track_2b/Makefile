PYTHON ?= python3
.PHONY: run run-native test docker-build docker-run
run: docker-build docker-run
run-native:
	$(PYTHON) -B container_entrypoint.py
test:
	$(PYTHON) -B -m unittest discover -s tests -v
docker-build:
	docker build -t sentinel-apertus-2b .
docker-run:
	docker run --rm -e LLM_API_KEY -e LLM_NAME -e LLM_BASE_URL -e CSCS_INFERENCE_API_KEY -e APERTUS_MODEL -e CSCS_BASE_URL -v "$(CURDIR)/data:/app/data" sentinel-apertus-2b
