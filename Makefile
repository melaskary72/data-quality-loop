PY := .venv/bin/python

.PHONY: help venv demo generate induce label qa review improve evaluate report export status check clean

help:
	@echo "make venv      create the Python 3.13 venv and install allowlisted deps"
	@echo "make demo      run every stage except review, then stop for the human"
	@echo "make review    open the human review queue"
	@echo "make check     contract check, seed verification, yamlio selftest"
	@echo "make status    pipeline state and cumulative spend"

venv:
	uv venv --python 3.13 .venv
	uv pip install --python $(PY) -e .

demo: ; $(PY) -m dql run-all
generate: ; $(PY) -m dql generate
induce: ; $(PY) -m dql induce
label: ; $(PY) -m dql label --source both
qa: ; $(PY) -m dql qa
review: ; $(PY) -m dql review
improve: ; $(PY) -m dql improve
evaluate: ; $(PY) -m dql evaluate --version both
report: ; $(PY) -m dql report
export: ; $(PY) -m dql export
status: ; $(PY) -m dql status

check:
	$(PY) scripts/check_contract.py
	$(PY) scripts/verify_seeds.py
	$(PY) -m dql.yamlio

clean:
	rm -f data/dql.db
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
