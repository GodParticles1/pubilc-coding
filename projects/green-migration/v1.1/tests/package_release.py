"""Rebuild the single deterministic runtime ZIP from an exact clean checkout.

Verification code stays in the repository and full source manifest. Runtime
hosts never receive the synthetic drivers or test runner.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import zipfile

root = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--output", type=Path, required=True)
args = p.parse_args()
output = args.output.resolve()
if output == root or root in output.parents or output.exists():
    p.error("output must be new and outside the source directory")
status = subprocess.check_output(["git", "-C", str(root), "status", "--porcelain", "--", "."], text=True)
if status.strip():
    p.error("source checkout must be clean; commit exact reviewed bytes first")
head = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
sha = lambda data: hashlib.sha256(data).hexdigest()
source = {}
for row in (root / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
    digest, name = row.split("  ", 1)
    if not re.fullmatch(r"[a-f0-9]{64}", digest) or "\\" in name:
        p.error("invalid source manifest")
    path = root / name
    if path.is_symlink() or root not in path.resolve().parents or sha(path.read_bytes()) != digest:
        p.error("source digest/path mismatch: " + name)
    source[name] = path.read_bytes()
runtime = {name: data for name, data in source.items()
           if not name.startswith("tests/") and name != "verify_offline.sh"}
runtime["SOURCE_SHA256SUMS"] = (root / "SHA256SUMS").read_bytes()
runtime["PACKAGE.json"] = (json.dumps(dict(candidate="RC2", source_commit=head,
    runtime_installation_excludes_tests=True, source_manifest="SOURCE_SHA256SUMS",
    test_receipt="OFFLINE_TEST_REPORT.json"), indent=2) + "\n").encode()
runtime["SHA256SUMS"] = "".join(sha(data) + "  " + name + "\n"
    for name, data in sorted(runtime.items())).encode()
output.parent.mkdir(parents=True, exist_ok=True)
with output.open("xb") as destination:
    # Stored entries avoid platform/zlib-version-dependent compressed bytes.
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_STORED) as z:
        for name, data in sorted(runtime.items()):
            entry = zipfile.ZipInfo(name, date_time=(2026, 10, 22, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_STORED
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            z.writestr(entry, data)
with zipfile.ZipFile(output) as z:
    assert z.testzip() is None
    assert not any(name.startswith("tests/") for name in z.namelist())
    for row in z.read("SHA256SUMS").decode().splitlines():
        digest, name = row.split("  ", 1)
        assert sha(z.read(name)) == digest
    for name, data in runtime.items():
        assert z.read(name) == data
digest = sha(output.read_bytes())
Path(str(output) + ".sha256").write_text(digest + "  " + output.name + "\n", encoding="utf-8")
print(json.dumps(dict(path=str(output), sha256=digest, files=len(runtime),
                     size=output.stat().st_size, source_commit=head), indent=2))
