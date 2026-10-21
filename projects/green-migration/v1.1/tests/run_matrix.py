"""Run independent matrix components in isolated Python processes."""
import argparse
import concurrent.futures
import json
from pathlib import Path
import subprocess
import sys
import tempfile

p = argparse.ArgumentParser()
p.add_argument("--report")
args = p.parse_args()
here = Path(__file__).resolve().parent
modules = sorted(here.glob("test_*.py"))
with tempfile.TemporaryDirectory(prefix="green-v11-matrix-") as temporary:
    def run(path):
        report = Path(temporary) / (path.stem + ".json")
        process = subprocess.run([sys.executable, str(here / "run_suite.py"),
                                  "--pattern", path.name, "--report", str(report)],
                                 capture_output=True, text=True, encoding="utf-8", errors="replace")
        result = json.loads(report.read_text(encoding="utf-8")) if report.exists() else None
        return path.name, process, result

    results = []
    # Separate processes isolate os/mock patches. Every component owns its temp
    # roots; no service host, shared target data or production process is used.
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(run, path) for path in modules]
        for future in concurrent.futures.as_completed(futures):
            name, process, result = future.result()
            print(name + ": " + ("PASS" if process.returncode == 0 else "FAIL"), flush=True)
            if process.returncode:
                print(process.stdout + process.stderr, file=sys.stderr, flush=True)
            results.append((name, process, result))
    results.sort(key=lambda value: value[0])
    good = bool(results) and all(process.returncode == 0 and result for _, process, result in results)
    receipts = [result for _, _, result in results if result]
    if receipts:
        reference = receipts[0]["source_sha256"]
        good = good and all(result["source_sha256"] == reference and not result["source_changed_during_run"]
                            for result in receipts)
        combined = dict(receipts[0])
        combined.update(tests_run=sum(x["tests_run"] for x in receipts),
                        passed=sum(x["passed"] for x in receipts),
                        failures=sum(x["failures"] for x in receipts),
                        errors=sum(x["errors"] for x in receipts),
                        skipped=[s for x in receipts for s in x["skipped"]],
                        source_changed_during_run=[s for x in receipts for s in x["source_changed_during_run"]],
                        modules=[dict(name=name, exit_code=process.returncode,
                                      tests_run=result["tests_run"] if result else 0)
                                 for name, process, result in results],
                        matrix="PASS_WITH_EXPLICIT_SKIPS" if good else "FAIL")
        if args.report:
            Path(args.report).write_text(json.dumps(combined, ensure_ascii=False, indent=2) + "\n",
                                         encoding="utf-8", newline="\n")
        print("OFFLINE_MATRIX=" + combined["matrix"] + "; RUN=" + str(combined["tests_run"]) +
              "; PASS=" + str(combined["passed"]) + "; SKIP=" + str(len(combined["skipped"])))
    else:
        print("OFFLINE_MATRIX=FAIL_NO_TEST_RECEIPT", file=sys.stderr)
sys.exit(0 if good else 1)

