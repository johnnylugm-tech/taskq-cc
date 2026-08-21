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
	@TASKQ_INSIDE_VERIFY_SYSTEM=1 .venv/bin/python -m pytest 03-development/tests --cov=03-development/src --cov-report=term --tb=short -q > /tmp/verify_system_pytest.txt 2>&1; \
	  status=$$?; \
	  tail -5 /tmp/verify_system_pytest.txt; \
	  if [ $$status -eq 0 ]; then echo "verify-system: PASS"; else echo "verify-system: FAIL"; exit 1; fi

.PHONY: sbom

# sbom — regenerate 08-config/SBOM.json (NFR-07 AC-N7.4).
# One record per installed distribution (direct + transitive, --with-system
# per AC-N7.3) carrying name / version / license / scope. ``scope`` is
# "direct" when the distribution is named in requirements.txt, else
# "transitive". Regenerate after any dependency change; the shape is
# asserted by test_ac_n7_4_sbom_json_one_record_per_dep_with_required_fields.
sbom:
	@mkdir -p 08-config
	@.venv/bin/pip-licenses --format=json --with-system | .venv/bin/python -c "$$SBOM_SCRIPT" > 08-config/SBOM.json
	@echo "sbom: wrote 08-config/SBOM.json"

export SBOM_SCRIPT
define SBOM_SCRIPT
import json, re, sys, pathlib
raw = json.load(sys.stdin)
req = pathlib.Path("requirements.txt").read_text().splitlines()
direct = {
    re.split(r"[=<>~\[]", line.strip())[0].lower()
    for line in req
    if line.strip() and not line.startswith("#")
}
records = [
    {
        "name": entry["Name"],
        "version": entry["Version"],
        "license": entry["License"],
        "scope": "direct" if entry["Name"].lower() in direct else "transitive",
    }
    for entry in sorted(raw, key=lambda e: e["Name"].lower())
]
json.dump(records, sys.stdout, indent=2)
sys.stdout.write("\n")
endef
