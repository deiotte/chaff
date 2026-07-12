.PHONY: check test examples run-api clean notices license-check

check: test
	@echo "--- validating example specs ---"
	@for f in examples/*.json; do python -m chaff.cli validate $$f; done
	@echo "CHECK GREEN"

# Regenerate THIRD-PARTY-NOTICES.txt from the bundled dependency set. Run in an
# env with the bundled extras installed: pip install -e '.[api,formats-extra,nl,nl-openai]' pip-licenses
notices:
	python packaging/gen_notices.py

# Fail if any bundled dependency is under a non-permissive (GPL/LGPL/AGPL/...) license.
license-check:
	python packaging/gen_notices.py --check

test:
	python -m pytest tests/ -q

examples:
	@for f in examples/*.json; do python -m chaff.cli generate $$f; done
	@ls -la out/

run-api:
	uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

clean:
	rm -rf out/ .pytest_cache
