#!/usr/bin/env python3
"""Wire the time-integrated thermal clause to the real feeder, and discharge it
on the real TeSSLa engine.

The other energy experiments evaluate a dispatch snapshot. Thermal loading is not
a snapshot property: a conductor may carry its short-term rating briefly and only
its continuous rating sustained, so the constraint is a moving-window integral
over a dispatch *schedule*. This script

  1. generates a dispatch schedule over the IEEE 33-bus feeder in which an
     aggregator ramps a fleet up across a market window,
  2. computes per-segment loading at each interval using the same reachability
     traversal the spatial monitor uses,
  3. exports one TeSSLa trace per segment and runs the unmodified TeSSLa 2.1
     interpreter on each,
  4. evaluates the same property with an independent Python reference, and
  5. reports agreement.

Division of labour, stated plainly: TeSSLa discharges the temporal-quantitative
part. The reachability traversal that decides WHICH resources load a segment runs
outside it. The claim is that TeSSLa monitors the time-integrated loading of a
segment, not that the spatial aspect runs on TeSSLa.

Requires Docker and the local `hierarchical_rv-monitor` image (TeSSLa 2.1).
"""
import argparse
import os
import random
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import energy_rv
import feeder_topology as FT

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "tessla", "feeder")
IMAGE = "hierarchical_rv-monitor:latest"

WIN = 5                 # samples in the thermal window
INTERVAL_S = 60         # seconds between dispatch intervals
SHORT_FACTOR = 1.5      # short-term rating as a multiple of the continuous one

SPEC = """# Time-integrated thermal loading, segment {seg}
# continuous rating {cont} kW, short-term rating {short} kW, window {win} samples
in load : Events[Float]

def WIN   : Int   = {win}
def CONT  : Float = {cont}
def SHORT : Float = {short}

def hist : Events[List[Float]] =
  fold(load, List.empty[Float], (acc: List[Float], x: Float) =>
    if List.size(acc) >= WIN
    then List.append(List.tail(acc), x)
    else List.append(acc, x))

def win_sum : Events[Float] =
  slift1(hist, (h: List[Float]) =>
    List.fold(h, 0.0, (a: Float, b: Float) => a +. b))
def win_n : Events[Int] = slift1(hist, (h: List[Float]) => List.size(h))
def win_mean : Events[Float] =
  slift2(win_sum, win_n, (s: Float, n: Int) => s /. intToFloat(n))

def inst_ok  : Events[Bool] = slift1(load,     (p: Float) => p <=. SHORT)
def integ_ok : Events[Bool] = slift1(win_mean, (m: Float) => m <=. CONT)

# 0 SAT, 2 PUNSAT (sustained overload), 3 UNSAT (instantaneous breach)
def verdict : Events[Int] =
  slift2(inst_ok, integ_ok, (i: Bool, g: Bool) =>
    if !i then 3 else if !g then 2 else 0)

out verdict
"""


def schedule(n_intervals, rng):
    """An aggregator ramps its fleet across a market window, holds, then spikes.
    Every setpoint stays inside each device's own rating throughout."""
    ders = sorted(energy_rv.DER_BUS)
    sched = []
    for i in range(n_intervals):
        if i < 4:
            level = 20.0 + 10.0 * i            # ramp
        elif i < 12:
            level = 70.0 + rng.uniform(-4, 4)  # sustained hold
        elif i == 12:
            level = 140.0                      # a single high interval
        else:
            level = 45.0 + rng.uniform(-4, 4)  # back down
        sched.append({d: round(min(level, 140.0), 1) for d in ders})
    return sched


def segment_loads(sched):
    """Per-segment loading at each interval, via the reachability traversal."""
    w = FT.seed_world()
    series = {}
    for t, dispatch in enumerate(sched):
        w.dispatch = dict(dispatch)
        for seg, rating in energy_rv.SEG_RATING.items():
            a, b = tuple(seg)
            total = sum(w.dispatch.get(d, 0.0) for d in w.ders_at(w.downstream((a, b))))
            series.setdefault((a, b, rating), []).append(total)
    return series


def reference(loads, cont, short):
    """The same property, evaluated independently in Python."""
    out, hist = [], []
    for p in loads:
        hist.append(p)
        if len(hist) > WIN:
            hist.pop(0)
        mean = sum(hist) / len(hist)
        out.append(3 if p > short else (2 if mean > cont else 0))
    return out


def run_tessla(name, spec, loads):
    os.makedirs(OUT, exist_ok=True)
    sp = os.path.join(OUT, f"{name}.tessla")
    tr = os.path.join(OUT, f"{name}.trace")
    with open(sp, "w") as f:
        f.write(spec)
    with open(tr, "w") as f:
        for i, p in enumerate(loads):
            f.write(f"{i * INTERVAL_S}: load = {p:.1f}\n")
    r = subprocess.run(["docker", "run", "--rm", "-v", f"{OUT}:/specs", IMAGE,
                        "tessla", "interpreter", f"/specs/{name}.tessla",
                        f"/specs/{name}.trace"],
                       capture_output=True, text=True, timeout=600)
    txt = r.stdout + r.stderr
    if "Error" in txt or "error" in txt.lower():
        return None, txt
    verd = []
    for line in r.stdout.splitlines():
        if "verdict =" in line:
            verd.append(int(line.split("=")[1].strip()))
    return verd, txt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--intervals", type=int, default=20)
    ap.add_argument("--seed", type=int, default=3)
    ap.add_argument("--top", type=int, default=4, help="most-loaded segments to run")
    a = ap.parse_args()

    FT.seed_world()                       # populates DER_BUS / SEG_RATING
    rng = random.Random(a.seed)
    sched = schedule(a.intervals, rng)
    series = segment_loads(sched)

    # run the segments that actually carry load
    ranked = sorted(series.items(), key=lambda kv: -max(kv[1]))[:a.top]

    print("=" * 78)
    print(f"TIME-INTEGRATED THERMAL LOADING on the IEEE 33-bus feeder")
    print(f"{a.intervals} dispatch intervals, {len(energy_rv.DER_BUS)} DERs, "
          f"window {WIN} samples, TeSSLa 2.1 via {IMAGE}")
    print("=" * 78)
    print(f"{'segment':<26}{'cont/short kW':>15}{'peak kW':>10}"
          f"{'TeSSLa == reference':>22}")
    agree_all = True
    for (u, v, rating), loads in ranked:
        cont, short = rating, rating * SHORT_FACTOR
        name = f"{u}_{v}".replace(".", "_")
        spec = SPEC.format(seg=f"{u}-{v}", cont=f"{cont:.1f}",
                           short=f"{short:.1f}", win=WIN)
        verd, txt = run_tessla(name, spec, loads)
        ref = reference(loads, cont, short)
        if verd is None:
            print(f"{u}-{v:<20} ENGINE ERROR: {txt.strip().splitlines()[0][:60]}")
            agree_all = False
            continue
        ok = verd == ref
        agree_all &= ok
        print(f"{u+'-'+v:<26}{f'{cont:.0f}/{short:.0f}':>15}"
              f"{max(loads):>10.0f}{('yes' if ok else 'NO'):>22}")
        if not ok:
            print(f"    tessla    {verd}")
            print(f"    reference {ref}")

    # show one segment's trajectory in full, as the interpretable result
    (u, v, rating), loads = ranked[0]
    cont, short = rating, rating * SHORT_FACTOR
    ref = reference(loads, cont, short)
    print(f"\ntrajectory for {u}-{v} (continuous {cont:.0f} kW, "
          f"short-term {short:.0f} kW):")
    print(f"  {'t':>4}{'load':>9}{'window mean':>14}{'verdict':>10}")
    hist = []
    for i, p in enumerate(loads):
        hist.append(p)
        if len(hist) > WIN:
            hist.pop(0)
        tag = {0: "SAT", 2: "PUNSAT", 3: "UNSAT"}[ref[i]]
        print(f"  {i:>4}{p:>9.0f}{sum(hist)/len(hist):>14.1f}{tag:>10}")

    print(f"\nTeSSLa agrees with the reference on every segment: {agree_all}")
    print("Scope: TeSSLa discharges the time-integrated part. The reachability")
    print("traversal deciding WHICH resources load a segment runs outside it.")


if __name__ == "__main__":
    main()
