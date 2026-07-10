.PHONY: check test examples run-api clean

check: test
	@echo "--- validating example specs ---"
	@for f in examples/*.json; do python -m chaff.cli validate $$f; done
	@echo "CHECK GREEN"

test:
	python -m pytest tests/ -q

examples:
	@for f in examples/*.json; do python -m chaff.cli generate $$f; done
	@ls -la out/

run-api:
	uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

clean:
	rm -rf out/ .pytest_cache
