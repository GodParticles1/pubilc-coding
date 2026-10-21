"""Run one consistent offline suite and optionally write a machine-readable receipt."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import sys
import unittest

p = argparse.ArgumentParser()
p.add_argument("--report")
p.add_argument("--pattern", default="test_*.py")
args = p.parse_args()
here = Path(__file__).resolve().parent
release = here.parent
checked = [p for p in release.rglob("*")
           if p.is_file() and p.suffix in (".sh", ".py", ".groovy", ".txt")
           and "__pycache__" not in p.parts and not any(s.startswith(".") for s in p.relative_to(release).parts)]
before = {str(p.relative_to(release)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest() for p in checked}
suite = unittest.defaultTestLoader.discover(str(here), pattern=args.pattern)
result = unittest.TextTestRunner(verbosity=2).run(suite)
changed = [name for name, sha in before.items() if hashlib.sha256((release / name).read_bytes()).hexdigest() != sha]
if changed:
    print("SOURCE_CHANGED_DURING_TEST_RUN=" + ",".join(changed), file=sys.stderr)
if args.report:
    receipt = dict(scope="OFFLINE_EQUIVALENT_LOGIC_ONLY", platform=platform.platform(),
                   tests_run=result.testsRun, passed=result.testsRun-len(result.failures)-len(result.errors)-len(result.skipped),
                   failures=len(result.failures), errors=len(result.errors),
                   skipped=[dict(test=str(test), reason=reason) for test, reason in result.skipped],
                   source_sha256=before, source_changed_during_run=changed,
                   field_armv7="NOT_RUN", real_databases="NOT_RUN",
                   live_jenkins="NOT_VERIFIED", business_acceptance="PENDING")
    Path(args.report).write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
sys.exit(0 if result.wasSuccessful() and result.testsRun and not changed else 1)

