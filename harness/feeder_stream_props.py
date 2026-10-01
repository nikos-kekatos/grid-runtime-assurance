#!/usr/bin/env python3
"""Two further stream properties on the real TeSSLa engine.

Both are accumulative quantitative constraints over a rolling window, which is
what a stream language is for and what neither of the other engines can express:
DejaVu's BDD encoding compares data for equality rather than magnitude, and an
unbounded accumulating aggregate is not what MFOTL's decision procedure is built
around.

  A. EQUIPMENT OPERATION BUDGET.  Tap changers, reclosers and switched capacitor
     banks wear per operation, and asset owners hold a maintenance allowance
     expressed as operations per day. The constraint is a rolling count against
     that allowance. Rather than filter a list by age, we keep the last
     BUDGET+1 operation timestamps and ask whether their SPAN fits inside the
     window: if it does, more than BUDGET operations occurred within one window.

  B. HUNTING AND OSCILLATION.  An aggregator's controller and the network's own
     voltage regulation can enter sustained disagreement, each reacting to the
     other. The symptom is repeated reversal of setpoint direction. We count
     direction changes over a sample window and compare against a tolerance.
     This is a control-stability property, not a limit violation: no individual
     setpoint is out of range at any point.

Each property is checked on the unmodified TeSSLa 2.1 interpreter and against an
independent Python reference.
"""
import argparse
import os
import random
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "tessla", "streamprops")
IMAGE = "hierarchical_rv-monitor:latest"

# --- A. equipment operation budget -------------------------------------------
BUDGET, WARN_OPS, WIN_S = 12, 9, 86400.0

SPEC_BUDGET = """in op : Events[Float]

def BUDGET : Int   = {budget}
def WARN   : Int   = {warn}
def WIN    : Float = {win}

def histB : Events[List[Float]] =
  fold(op, List.empty[Float], (acc: List[Float], x: Float) =>
    if List.size(acc) >= BUDGET + 1
    then List.append(List.tail(acc), x)
    else List.append(acc, x))

def histW : Events[List[Float]] =
  fold(op, List.empty[Float], (acc: List[Float], x: Float) =>
    if List.size(acc) >= WARN + 1
    then List.append(List.tail(acc), x)
    else List.append(acc, x))

def spanB : Events[Float] =
  slift1(histB, (h: List[Float]) =>
    if List.size(h) <= BUDGET then WIN *. 2.0
    else List.last(h) -. List.head(h))

def spanW : Events[Float] =
  slift1(histW, (h: List[Float]) =>
    if List.size(h) <= WARN then WIN *. 2.0
    else List.last(h) -. List.head(h))

def overBudget : Events[Bool] = slift1(spanB, (s: Float) => s <=. WIN)
def overWarn   : Events[Bool] = slift1(spanW, (s: Float) => s <=. WIN)

def verdict : Events[Int] =
  slift2(overBudget, overWarn, (b: Bool, w: Bool) =>
    if b then 3 else if w then 2 else 0)

out verdict
"""

# --- B. hunting / oscillation -------------------------------------------------
REV_WIN, REV_MAX, REV_WARN = 10, 6, 4

SPEC_HUNT = """in sp : Events[Float]

def WINN : Int = {win}
def MAXR : Int = {maxr}
def WARN : Int = {warnr}

def prevsp : Events[Float] = last(sp, sp)
def delta  : Events[Float] = slift2(sp, prevsp, (c: Float, p: Float) => c -. p)
def dir    : Events[Int] =
  slift1(delta, (d: Float) => if d >. 0.0 then 1 else if d <. 0.0 then -1 else 0)
def prevdir : Events[Int] = last(dir, dir)

def reversal : Events[Int] =
  slift2(dir, prevdir, (c: Int, p: Int) =>
    if c != 0 && p != 0 && c != p then 1 else 0)

def revhist : Events[List[Int]] =
  fold(reversal, List.empty[Int], (acc: List[Int], x: Int) =>
    if List.size(acc) >= WINN
    then List.append(List.tail(acc), x)
    else List.append(acc, x))

def revcount : Events[Int] =
  slift1(revhist, (h: List[Int]) => List.fold(h, 0, (a: Int, b: Int) => a + b))

def verdict : Events[Int] =
  slift1(revcount, (n: Int) => if n >= MAXR then 3 else if n >= WARN then 2 else 0)

out verdict
"""


def run_tessla(name, spec, stream):
    os.makedirs(OUT, exist_ok=True)
    sp = os.path.join(OUT, f"{name}.tessla")
    tr = os.path.join(OUT, f"{name}.trace")
    with open(sp, "w") as f:
        f.write(spec)
    inp = "op" if "op :" in spec else "sp"
    with open(tr, "w") as f:
        for t, v in stream:
            f.write(f"{int(t)}: {inp} = {v:.2f}\n")
    r = subprocess.run(["docker", "run", "--rm", "-v", f"{OUT}:/specs", IMAGE,
                        "tessla", "interpreter", f"/specs/{name}.tessla",
                        f"/specs/{name}.trace"],
                       capture_output=True, text=True, timeout=600)
    txt = r.stdout + r.stderr
    if "rror" in txt:
        return None, txt
    return [int(l.split("=")[1]) for l in r.stdout.splitlines()
            if "verdict =" in l], txt


# --- references ---------------------------------------------------------------
def ref_budget(stream):
    """`fold` carries an initial value, so histB is a SIGNAL defined from time 0
    and `slift` emits a verdict there, before any operation. Zero operations is
    within budget, so that leading verdict is correct and the reference must
    produce it too."""
    out, hist = [0], []
    for _t, v in stream:
        hist.append(v)
        if len(hist) > BUDGET + 1:
            hist.pop(0)
        hw = hist[-(WARN_OPS + 1):]
        over_b = len(hist) > BUDGET and (hist[-1] - hist[0]) <= WIN_S
        over_w = len(hw) > WARN_OPS and (hw[-1] - hw[0]) <= WIN_S
        out.append(3 if over_b else (2 if over_w else 0))
    return out


def ref_hunt(stream):
    """`last(sp, sp)` has no value until the second sample, so `delta` and every
    stream derived from it are undefined at the first. The engine therefore
    emits one verdict fewer than there are samples, and the reference skips the
    first sample rather than inventing a direction for it."""
    out, revs, pdir = [], [], 0
    prev = None
    first = True
    for _t, v in stream:
        if first:
            prev, first = v, False
            continue
        d = 1 if v > prev else (-1 if v < prev else 0)
        rev = 1 if (d != 0 and pdir != 0 and d != pdir) else 0
        revs.append(rev)
        if len(revs) > REV_WIN:
            revs.pop(0)
        n = sum(revs)
        out.append(3 if n >= REV_MAX else (2 if n >= REV_WARN else 0))
        if d != 0:
            pdir = d
        prev = v
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=5)
    a = ap.parse_args()
    rng = random.Random(a.seed)

    print("=" * 74)
    print("TWO FURTHER STREAM PROPERTIES ON TeSSLa 2.1")
    print("=" * 74)

    # A. a recloser with a quiet spell, then a storm burst inside one day
    ops, t = [], 0.0
    for _ in range(6):                       # sparse, well inside allowance
        t += rng.uniform(30000, 50000); ops.append((t, t))
    for _ in range(14):                      # storm: many operations in hours
        t += rng.uniform(900, 2400); ops.append((t, t))
    verd, txt = run_tessla("budget", SPEC_BUDGET.format(
        budget=BUDGET, warn=WARN_OPS, win=f"{WIN_S:.1f}"), ops)
    ref = ref_budget(ops)
    print(f"\nA. EQUIPMENT OPERATION BUDGET "
          f"({BUDGET}/day allowance, warn at {WARN_OPS})")
    if verd is None:
        print("   ENGINE ERROR:", txt.strip().splitlines()[0][:90])
    else:
        print(f"   operations : {len(ops)}")
        print(f"   TeSSLa     : {verd}")
        print(f"   reference  : {ref}")
        print(f"   agree      : {verd == ref}")
        first = next((i for i, v in enumerate(verd) if v == 3), None)
        if first is not None:
            print(f"   budget first exceeded at operation {first + 1}, "
                  f"{(ops[first][0] - ops[first - BUDGET][0]) / 3600:.1f} h "
                  f"after the {BUDGET} preceding it")

    # B. a setpoint that tracks smoothly, then hunts against voltage regulation
    sps, v = [], 40.0
    for i in range(12):                      # smooth ramp
        v += 3.0; sps.append((i * 60.0, v))
    for i in range(12, 30):                  # hunting: alternating corrections
        v += (8.0 if i % 2 == 0 else -8.0) + rng.uniform(-0.5, 0.5)
        sps.append((i * 60.0, v))
    verd2, txt2 = run_tessla("hunting", SPEC_HUNT.format(
        win=REV_WIN, maxr=REV_MAX, warnr=REV_WARN), sps)
    ref2 = ref_hunt(sps)
    print(f"\nB. HUNTING / OSCILLATION "
          f"(>= {REV_MAX} reversals in {REV_WIN} samples)")
    if verd2 is None:
        print("   ENGINE ERROR:", txt2.strip().splitlines()[0][:90])
    else:
        print(f"   samples   : {len(sps)}")
        print(f"   TeSSLa    : {verd2}")
        print(f"   reference : {ref2}")
        print(f"   agree     : {verd2 == ref2}")
        first = next((i for i, x in enumerate(verd2) if x == 3), None)
        if first is not None:
            print(f"   hunting detected at sample {first + 1}; every setpoint "
                  f"remained within range throughout")


if __name__ == "__main__":
    main()
