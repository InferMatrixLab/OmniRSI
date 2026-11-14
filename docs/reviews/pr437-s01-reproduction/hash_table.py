import hashlib
import json
import sys
from pathlib import Path

# Markdown table of per-trial result.json hashes before and after the split.
sys.stdout.reconfigure(encoding="utf-8", newline="\n")
before_root, after_root = Path(sys.argv[1]), Path(sys.argv[2])
before = json.loads((before_root / "manifest.json").read_text(encoding="utf-8"))
after = json.loads((after_root / "manifest.json").read_text(encoding="utf-8"))


def records(manifest):
    return {name: digest for name, digest in manifest["files"].items()
            if name.endswith("/result.json") and "/verification/" not in name
            and not name.startswith("pure/")}


def tree_digest(files):
    lines = "".join(f"{digest}  {name}\n" for name, digest in sorted(files.items()))
    return hashlib.sha256(lines.encode()).hexdigest()


old, new = records(before), records(after)
print(f"trial records: before={len(old)} after={len(new)}")
print(f"all retained files: before={len(before['files'])} after={len(after['files'])}")
print(f"tree digest over all retained files: before={tree_digest(before['files'])}")
print(f"tree digest over all retained files: after ={tree_digest(after['files'])}")
print(f"tree digest over trial result.json:  before={tree_digest(old)}")
print(f"tree digest over trial result.json:  after ={tree_digest(new)}")
print()
print("| Trial | Verdict | evaluation_error | result.json SHA-256 before | after | Same |")
print("| --- | --- | --- | --- | --- | --- |")
for name in sorted(old.keys() | new.keys()):
    record = json.loads((after_root / name).read_text(encoding="utf-8"))
    print(f"| `{name.removesuffix('/result.json')}` | {record['verdict']} "
          f"| {record['evaluation_error'] or 'none'} | `{old.get(name)}` | `{new.get(name)}` "
          f"| {'yes' if old.get(name) == new.get(name) else 'NO'} |")
print()
print("| Pure judgment output | SHA-256 before | after | Same |")
print("| --- | --- | --- | --- |")
for name in sorted(n for n in before["files"] if n.startswith("pure/")):
    a, b = before["files"][name], after["files"].get(name)
    print(f"| `{name}` | `{a}` | `{b}` | {'yes' if a == b else 'NO'} |")
