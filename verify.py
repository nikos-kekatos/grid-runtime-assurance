"""Compare a fresh run of the harnesses with the reference results.

    make results      # re-runs the three harnesses into results/
    python3 verify.py # exits non-zero on any mismatch

Every quantity must match the reference exactly, except wall-clock timings
(keys containing "cost"), which vary between machines and are reported only.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, "results")
REF = os.path.join(HERE, "checks", "reference", "results.json")

subprocess.run([sys.executable, os.path.join(HERE, "checks", "extract.py"), RES,
                os.path.join(RES, "results.json")], check=True, stdout=subprocess.DEVNULL)
new = json.load(open(os.path.join(RES, "results.json")))
ref = json.load(open(REF))

mismatches, timing = [], []


def walk(a, b, path):
    if isinstance(a, dict):
        for k in a:
            walk(a[k], b.get(k) if isinstance(b, dict) else None, f"{path}.{k}")
    elif "cost" in path:
        timing.append((path, a, b))
    elif a != b:
        mismatches.append((path, a, b))


walk(ref, new, "results")
for p, a, b in timing:
    print(f"timing (not compared): {p} reference={a} this run={b}")
if mismatches:
    for p, a, b in mismatches:
        print(f"MISMATCH {p}: reference={a} this run={b}")
    sys.exit(1)
print("all non-timing results match the reference")
