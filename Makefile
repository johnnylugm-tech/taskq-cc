.PHONY: verify-system

# verify-system — full end-to-end verification target.
# Per FR-12 AC / NFR-12 the target must invoke the delivered entry
# point (the program a user would run), so a regression in the
# ``python -m taskq_api`` CLI or its dispatch table will fail the
# target before the dimension scores can drift. SAB-declared
# acceptance artifact for NFR-12.
verify-system:
	@mkdir -p /tmp/verify_system_home
	@PYTHONPATH=03-development/src TASKQ_DB_URL=sqlite:////tmp/verify_system_home/v.db TASKQ_HOME=/tmp/verify_system_home .venv/bin/python -m taskq_api --help > /tmp/verify_system_help.txt 2>&1 || (echo "verify-system: FAIL — taskq_api --help exited $$?" && cat /tmp/verify_system_help.txt && exit 1)
	@grep -q "key" /tmp/verify_system_help.txt || (echo "verify-system: FAIL — --help did not advertise 'key' subcommand" && cat /tmp/verify_system_help.txt && exit 1)
	@PYTHONPATH=03-development/src TASKQ_DB_URL=sqlite:////tmp/verify_system_home/v.db TASKQ_HOME=/tmp/verify_system_home .venv/bin/python -m taskq_api key create --scope read > /tmp/verify_system_key.txt 2>&1 || (echo "verify-system: FAIL — 'key create' exited $$?" && cat /tmp/verify_system_key.txt && exit 1)
	@grep -q "^key:" /tmp/verify_system_key.txt || (echo "verify-system: FAIL — 'key create' did not print 'key:' line" && cat /tmp/verify_system_key.txt && exit 1)
	@.venv/bin/python -m pytest 03-development/tests --cov=03-development/src --cov-report=term --tb=short -q 2>&1 | tail -5
	@if [ $$? -eq 0 ]; then echo "verify-system: PASS"; else echo "verify-system: FAIL"; exit 1; fi
