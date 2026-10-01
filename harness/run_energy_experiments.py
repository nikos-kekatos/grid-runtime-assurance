#!/usr/bin/env python3
"""Six experiments for the AIoT-RSE energy instantiation.

  1. Reachability vs a static feeder list, under reconfiguration
  2. k-anonymity as a computable phi_Sigma
  3. Evidence completeness under telemetry loss
  4. Composition necessity -- as an EMPIRICAL RATE, not a definitional claim
  5. Sensitivity to topology error
  6. Scaling and overhead

Seeded and reproducible. Pure stdlib, no network, no API.
"""
import argparse
import os
import random
import statistics
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "vendor"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent_rv_eval import Event
from energy_rv import (F, Fp, T, FeederShield, FeederSpatial, FeederSemantic,
                       FeederTemporal, K_ANON)
import energy_rv
import feeder_topology as FT

FULL = ("S", "T", "Sigma")


def ev(turn, tool, args, ts=0.0, actor="aggregator"):
    return Event(turn=turn, actor=actor, kind="tool_call", tool=tool,
                 args=args, ts=ts)


def wilson(k, n, z=1.959963985):
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return (100 * (c - h), 100 * (c + h))


def random_dispatch(w, rng, hi=140.0):
    """A dispatch in which EVERY DER is individually within nameplate."""
    for d in energy_rv.DER_BUS:
        w.dispatch[d] = round(rng.uniform(0, hi), 1)


# =============================================================================
# 1. Reachability vs a static feeder list, under reconfiguration
# =============================================================================
def exp1(trials=400, seed=1):
    rng = random.Random(seed)
    w0 = FT.seed_world()
    sp = FeederSpatial()
    # the static approach: downstream sets precomputed ONCE, in the base config
    static = {seg: w0.downstream(tuple(seg)) for seg in energy_rv.SEG_RATING}

    miss = false_alarm = agree = 0
    for _ in range(trials):
        w = FT.seed_world()
        # reconfigure: close some ties, open some trunk sectionalisers
        for a, b in FT.TIES:
            if rng.random() < 0.5:
                w.open_switches.discard(frozenset((FT.B(a), FT.B(b))))
        for a, b in rng.sample(FT.BRANCHES, k=rng.randint(0, 3)):
            w.open_switches.add(frozenset((FT.B(a), FT.B(b))))
        random_dispatch(w, rng)

        truth = sp.e1_aggregate_loading(w) == F           # reachability-based
        # static verdict: same sums, but over the STALE downstream sets
        stale = False
        for seg, rating in energy_rv.SEG_RATING.items():
            if seg not in static:
                continue
            gen = sum(w.dispatch.get(d, 0.0) for d in w.ders_at(static[seg]))
            load = sum(energy_rv.BUS_LOAD_KW.get(x, 0.0) for x in static[seg])
            if abs(load - gen) > rating:
                stale = True
                break
        if truth and not stale:
            miss += 1
        elif stale and not truth:
            false_alarm += 1
        else:
            agree += 1

    lo, hi = wilson(miss, trials)
    flo, fhi = wilson(false_alarm, trials)
    print("=" * 74)
    print("1. REACHABILITY vs STATIC FEEDER LIST, under reconfiguration")
    print("=" * 74)
    print(f"  trials with reconfiguration: {trials}")
    print(f"  static MISSES a real violation : {miss:>4}/{trials} = "
          f"{100*miss/trials:5.1f}%   Wilson95 [{lo:.1f}, {hi:.1f}]")
    print(f"  static FALSELY flags a legal one: {false_alarm:>4}/{trials} = "
          f"{100*false_alarm/trials:5.1f}%   Wilson95 [{flo:.1f}, {fhi:.1f}]")
    print(f"  agrees with reachability        : {agree:>4}/{trials} = "
          f"{100*agree/trials:5.1f}%")
    print("  A precomputed 'which DERs are on this feeder' list goes stale the")
    print("  moment a switch moves; the reachability query does not.")
    return dict(miss=miss, fa=false_alarm, n=trials)


def _is_radial(w):
    """True if the energised network is a connected tree spanning every bus.

    exp1's protocol permits meshed and islanded configurations, which is what
    makes its figure an upper bound: the reachability/flow equivalence holds
    only on a radial network fed from one source. This restricts to
    configurations an operator could actually switch to."""
    import networkx as nx
    G = nx.Graph()
    buses = [x for x in w.g.v if w.g.v[x]["sort"] == "phys"]
    G.add_nodes_from(buses)
    for u, ns in w.g.adj.items():
        if w.g.v[u]["sort"] != "phys":
            continue
        for v in ns:
            if w.g.v[v]["sort"] == "phys" and frozenset((u, v)) not in w.open_switches:
                G.add_edge(u, v)
    import networkx as nx2
    return nx.is_connected(G) and G.number_of_edges() == len(buses) - 1


def exp1b(trials=400, seed=11):
    rng = random.Random(seed)
    w0 = FT.seed_world()
    sp = FeederSpatial()
    static = {}
    for seg in energy_rv.SEG_RATING:
        a, b = w0.oriented(seg)
        if a is not None:
            static[seg] = w0.downstream((a, b))

    miss = false_alarm = agree = 0
    tried = kept = 0
    while kept < trials and tried < trials * 500:
        tried += 1
        w = FT.seed_world()
        for a, b in FT.TIES:
            if rng.random() < 0.5:
                w.open_switches.discard(frozenset((FT.B(a), FT.B(b))))
        for a, b in rng.sample(FT.BRANCHES, k=rng.randint(0, 3)):
            w.open_switches.add(frozenset((FT.B(a), FT.B(b))))
        if not _is_radial(w):
            continue
        kept += 1
        random_dispatch(w, rng)

        truth = sp.e1_aggregate_loading(w) == F
        stale = False
        for seg, rating in energy_rv.SEG_RATING.items():
            if seg not in static:
                continue
            down = static[seg]
            gen = sum(w.dispatch.get(d, 0.0) for d in w.ders_at(down))
            load = sum(energy_rv.BUS_LOAD_KW.get(x, 0.0) for x in down)
            if abs(load - gen) > rating:
                stale = True
                break
        if truth and not stale:
            miss += 1
        elif stale and not truth:
            false_alarm += 1
        else:
            agree += 1

    lo, hi = wilson(miss, kept)
    flo, fhi = wilson(false_alarm, kept)
    print("\n" + "=" * 74)
    print("1b. REACHABILITY vs STATIC LIST, RADIAL CONFIGURATIONS ONLY")
    print("=" * 74)
    print(f"  radial configurations kept: {kept} of {tried} sampled")
    print(f"  static MISSES a violation       : {miss:>4}/{kept} = "
          f"{100*miss/max(1,kept):5.1f}%   Wilson95 [{lo:.1f}, {hi:.1f}]")
    print(f"  static FALSELY flags a compliant : {false_alarm:>4}/{kept} = "
          f"{100*false_alarm/max(1,kept):5.1f}%   Wilson95 [{flo:.1f}, {fhi:.1f}]")
    print(f"  agrees with reachability         : {agree:>4}/{kept} = "
          f"{100*agree/max(1,kept):5.1f}%")
    return dict(miss=miss, fa=false_alarm, n=kept, tried=tried)


# =============================================================================
# 2. k-anonymity as a computable phi_Sigma
# =============================================================================
def exp2(trials=400, seed=2, k=K_ANON):
    rng = random.Random(seed)
    sem = FeederSemantic(k=k)
    tp = fp = tn = fn = 0
    for _ in range(trials):
        w = FT.seed_world()
        disclosing = rng.random() < 0.5
        n_prem = rng.randint(1, k - 1) if disclosing else rng.randint(k, 60)
        w.products["src"] = {"per_premises": True, "granular": True}
        meta = {"premises": n_prem, "derived_from": "src", "marked": False}
        w.products["agg"] = meta
        flagged = sem.laundered(w, "agg")
        if disclosing and flagged:      tp += 1
        elif disclosing and not flagged: fn += 1
        elif flagged:                    fp += 1
        else:                            tn += 1
    print("\n" + "=" * 74)
    print(f"2. k-ANONYMITY AS phi_Sigma  (k = {k})")
    print("=" * 74)
    print(f"  detection of disclosing aggregates : {tp}/{tp+fn} = "
          f"{100*tp/max(1,tp+fn):5.1f}%")
    print(f"  false positives on legal aggregates: {fp}/{fp+tn} = "
          f"{100*fp/max(1,fp+tn):5.1f}%")
    print("  Unlike the lexical cue matcher the mission instantiation used, this")
    print("  is a computable property: exact by construction, not a heuristic.")
    return dict(tp=tp, fn=fn, fp=fp, tn=tn)


# =============================================================================
# 2b. Disclosure reachability on the informational sort
# =============================================================================
def exp2b(trials=400, seed=22):
    """The spatial clause E2 asks where a data product can TRAVEL, not what it
    contains. It is the same reach query as E1, run on the informational sort of
    the same graph. This exercises it: a granular product is held somewhere, a
    channel is opened or not, and the question is whether the product can reach a
    domain outside the operator and market boundary."""
    rng = random.Random(seed)
    sp = FeederSpatial()
    tp = fp = tn = fn = 0
    for _ in range(trials):
        w = FT.seed_world()
        w.products["telemetry"] = {"granular": True, "per_premises": True}
        # the product sits in the DSO's data-management system
        w.holds.setdefault("dso_dms", set()).add("telemetry")
        # with probability one half a feed to the aggregator is open
        leaky = rng.random() < 0.5
        if leaky:
            w.open_channels.add(("dso_dms", "aggregator"))
        truth = leaky                      # aggregator is outside ALLOWED_DOMAINS
        flagged = sp.e2_disclosure(w) == F
        if truth and flagged:       tp += 1
        elif truth:                 fn += 1
        elif flagged:               fp += 1
        else:                       tn += 1
    print("\n" + "=" * 74)
    print("2b. DISCLOSURE REACHABILITY ON THE INFORMATIONAL SORT (phi_S, E2)")
    print("=" * 74)
    print(f"  granular product reaches a disallowed domain: {tp}/{tp+fn} detected")
    print(f"  legal holdings falsely flagged              : {fp}/{fp+tn}")
    print("  The same reach operator that decides which resources sit behind a")
    print("  feeder segment decides which domains a data product can travel to.")
    return dict(tp=tp, fn=fn, fp=fp, tn=tn)


# =============================================================================
# 3. Evidence completeness under telemetry loss
# =============================================================================
def exp3(trials=300, seed=3):
    """Missing telemetry makes the aggregate UNDER-COUNT, so a real violation can
    become invisible. We measure how often that happens, and whether an
    evidence-aware monitor surfaces it instead of reporting a clean verdict."""
    rng = random.Random(seed)
    sp = FeederSpatial()
    print("\n" + "=" * 74)
    print("3. EVIDENCE COMPLETENESS UNDER TELEMETRY LOSS")
    print("=" * 74)
    print(f"  {'loss':>6}{'real violations':>17}{'naive misses':>15}{'':>9}"
          f"{'aware flags':>14}{'':>9}{'of which Fp':>10}"
          f"{'clean':>9}{'incon.':>10}{'':>9}")
    for p in (0.0, 0.1, 0.25, 0.5):
        real = naive_miss = aware = 0
        clean = inconclusive = aware_presumptive = 0
        for _ in range(trials):
            w = FT.seed_world()
            # 280 kW = parity between fleet nameplate and feeder load, the
            # lowest penetration at which aggregate violations exist at all
            random_dispatch(w, rng, hi=280.0)
            truth = sp.e1_aggregate_loading(w) == F      # full information
            for b in range(2, 34):
                if rng.random() < p:
                    w.dead_telemetry.add(FT.B(b))
            # naive monitor: sums only the DERs whose governing measurement is up
            visible = {d: kw for d, kw in w.dispatch.items() if w.upstream_live(d)}
            hidden = dict(w.dispatch)
            # The naive monitor sums only what it can measure. The evidence-aware
            # monitor also knows which setpoints it COMMANDED, which is not
            # privileged information, and E3 fires when a commanded resource is
            # unobservable. Deleting the unobservable resources from `dispatch`
            # instead would make E3 vacuously true and measure nothing.
            w.dispatch = visible
            naive = sp.e1_aggregate_loading(w) == F
            w.dispatch = hidden
            aware_v = sp.check(w)
            if truth:
                real += 1
                if not naive:
                    naive_miss += 1                      # the silent all-clear
                if aware_v in (F, Fp):
                    aware += 1
                if aware_v == Fp:
                    aware_presumptive += 1               # how many were INCOMPLETE
            else:
                clean += 1
                if aware_v == Fp:
                    inconclusive += 1                    # cost on clean traffic
        pct = lambda x: f"{100*x/max(1,real):5.1f}%"
        cpct = (lambda k: f"{100*k/max(1,clean):5.1f}%")
        print(f"  {p:>6.2f}{real:>17}{naive_miss:>15} {pct(naive_miss):>8}"
              f"{aware:>14} {pct(aware):>8}"
              f"{aware_presumptive:>10}"
              f"{clean:>9}{inconclusive:>10} {cpct(inconclusive):>8}")
    print("  A monitor that sums only what reported cannot distinguish a small")
    print("  aggregate from a large one it half observed. Flagging the gap is the")
    print("  difference between 'no violation' and 'no evidence'.")


# =============================================================================
# 4. Composition necessity -- as an empirical rate
# =============================================================================
def exp4(trials=500, seed=4):
    rng = random.Random(seed)
    print("\n" + "=" * 74)
    print("4. COMPOSITION NECESSITY (empirical, not definitional)")
    print("=" * 74)
    # (a) how often is a per-device-LEGAL dispatch aggregate-violating, and at
    #     what PV penetration does the question even have a non-zero answer?
    #     Net segment flow is |load - generation|, so a fleet much smaller than
    #     the feeder load cannot reverse-load a trunk segment at all: the
    #     constraint only starts to bind as nameplate approaches total load.
    sp = FeederSpatial()
    total_load = sum(FT.BUS_LOAD_KW.values())
    print("  per-device-legal dispatches violating an aggregate limit,")
    print("  as a function of fleet nameplate:")
    print(f"    {'DER kW':>8}{'fleet kW':>10}{'penetration':>13}"
          f"{'violating':>12}{'Wilson95':>18}")
    sweep = []
    for cap in (140, 280, 420, 560, 700):
        r2 = random.Random(seed); viol = 0
        for _ in range(trials):
            w = FT.seed_world()
            for d in energy_rv.DER_BUS:
                w.dispatch[d] = r2.uniform(0, cap)
            if sp.e1_aggregate_loading(w) == F:
                viol += 1
        fleet = len(FT.DERS) * cap
        lo, hi = wilson(viol, trials)
        pen = 100.0 * fleet / total_load
        sweep.append((cap, fleet, pen, viol, lo, hi))
        print(f"    {cap:>8}{fleet:>10.0f}{pen:>12.0f}%"
              f"{viol:>7}/{trials} {100*viol/trials:>5.1f}%"
              f"   [{lo:.1f}, {hi:.1f}]")
    viol = sweep[0][3]
    lo, hi = sweep[0][4], sweep[0][5]
    print(f"  at the modelled 140 kW rating the aggregate constraint does not "
          f"bind: {viol}/{trials}")
    print("    (this is the rate a per-device control would pass unchallenged --")
    print("     an empirical quantity, not the definitional observation that a")
    print("     per-device check cannot see an aggregate)")

    # (b) ablation over the three aspects on a mixed violation suite
    print(f"\n  {'configuration':<22}{'missed':>10}{'false pos':>12}")
    cases = []
    for _ in range(trials):
        kind = rng.choice(["aggregate", "authority", "disclosure", "legal"])
        cases.append(kind)
    for name, en in (("phi_S only", ("S",)), ("phi_T only", ("T",)),
                     ("phi_Sigma only", ("Sigma",)), ("S x T", ("S", "T")),
                     ("S x Sigma", ("S", "Sigma")), ("T x Sigma", ("T", "Sigma")),
                     ("all three", FULL)):
        missed = falsepos = nviol = nlegal = 0
        r2 = random.Random(seed + 99)
        for kind in cases:
            w = FT.seed_world()
            sh = FeederShield(block_on=(F, Fp))
            ts = 1.0
            blocked = False
            if kind == "aggregate":
                # ONLY phi_S may catch this: every DER is under the consent
                # threshold (100 kW) and the step from the previous interval is
                # under the ramp limit (50 kW), so no temporal clause applies.
                for d in energy_rv.DER_BUS:
                    w.dispatch[d] = 90.0
                    w.prev_dispatch[d] = 90.0
                b, _p, _v = sh.step(w, ev(1, "set_dispatch",
                                          {"der": "der_7", "kw": 95.0}, ts), enabled=en)
                blocked = b
            elif kind == "authority":
                # ONLY phi_T: above the consent threshold with no DSO consent,
                # but ramped gently and small enough not to load any segment.
                for d in energy_rv.DER_BUS:
                    w.dispatch[d] = 0.0
                w.prev_dispatch["der_7"] = 120.0
                b, _p, _v = sh.step(w, ev(1, "set_dispatch",
                                          {"der": "der_7", "kw": 150.0}, ts), enabled=en)
                blocked = b
            elif kind == "disclosure":
                w.products["src"] = {"per_premises": True, "granular": True}
                b, _p, _v = sh.step(w, ev(1, "publish_product",
                    {"product": "agg", "premises": 2, "derived_from": "src",
                     "marked": False, "dest": "dso_scada"}, ts), enabled=en)
                blocked = b
            else:   # legal
                sh.step(w, ev(1, "grant_consent", {"der": "der_7"}, 0.5,
                              actor="dso"), enabled=en)
                b, _p, _v = sh.step(w, ev(2, "set_dispatch",
                                          {"der": "der_7", "kw": 40.0}, 1.0), enabled=en)
                blocked = b
            if kind == "legal":
                nlegal += 1
                falsepos += bool(blocked)
            else:
                nviol += 1
                missed += (not blocked)
        print(f"  {name:<22}{missed:>6}/{nviol:<4}{falsepos:>7}/{nlegal:<4}")


# =============================================================================
# 5. Sensitivity to topology error
# =============================================================================
def exp5(trials=300, seed=5):
    rng = random.Random(seed)
    sp = FeederSpatial()
    print("\n" + "=" * 74)
    print("5. SENSITIVITY TO TOPOLOGY ERROR")
    print("=" * 74)
    print(f"  {'switch-state error rate':>26}{'verdict agreement':>20}")
    for p in (0.0, 0.05, 0.1, 0.2):
        agree = 0
        for _ in range(trials):
            w = FT.seed_world()
            for a, b in FT.TIES:
                if rng.random() < 0.5:
                    w.open_switches.discard(frozenset((FT.B(a), FT.B(b))))
            random_dispatch(w, rng)
            truth = sp.e1_aggregate_loading(w)
            # the monitor's belief about switch state is wrong at rate p
            w2 = FT.seed_world()
            w2.dispatch = dict(w.dispatch)
            w2.open_switches = set(w.open_switches)
            for a, b in FT.BRANCHES + FT.TIES:
                if rng.random() < p:
                    seg = frozenset((FT.B(a), FT.B(b)))
                    if seg in w2.open_switches:
                        w2.open_switches.discard(seg)
                    else:
                        w2.open_switches.add(seg)
            agree += (sp.e1_aggregate_loading(w2) == truth)
        print(f"  {p:>26.2f}{100*agree/trials:>19.1f}%")
    print("  Reachability verdicts are only as good as the connectivity model;")
    print("  this quantifies how good it has to be.")


# =============================================================================
# 6. Scaling and overhead
# =============================================================================
def exp6(seed=6):
    rng = random.Random(seed)
    print("\n" + "=" * 74)
    print("6. SCALING AND OVERHEAD")
    print("=" * 74)
    print(f"  {'DERs':>8}{'buses':>8}{'mean us/event':>16}{'p95 us':>10}")
    base = dict(FT.DERS)
    for n_der in (13, 50, 200, 1000):
        # replicate DERs across the same 33 buses to isolate population effect
        ders = {}
        buses = list(base.values())
        for i in range(n_der):
            ders[f"der_{i}"] = buses[i % len(buses)]
        energy_rv.DER_BUS = ders
        w = FT.seed_world()
        energy_rv.DER_BUS = ders          # seed_world resets it
        for d in ders:
            w.dispatch[d] = rng.uniform(0, 20.0)
        sp = FeederSpatial()
        samples = []
        for _ in range(200):
            t0 = time.perf_counter()
            sp.check(w)
            samples.append((time.perf_counter() - t0) * 1e6)
        samples.sort()
        print(f"  {n_der:>8}{33:>8}{statistics.mean(samples):>16.1f}"
              f"{samples[int(0.95*len(samples))]:>10.1f}")
    energy_rv.DER_BUS = base
    print("  NOTE: unlike the mission instantiation, cost here is dominated by the")
    print("  per-segment reachability traversal, which is independent of the DER")
    print("  count -- 13 DERs already cost most of what 200 do. The DER population")
    print("  adds a sub-linear term on top. The obvious optimisation is to cache")
    print("  downstream sets per topology and invalidate them on a switch change,")
    print("  which we have NOT done: these are unoptimised figures.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    a = ap.parse_args()
    n = 100 if a.quick else 400
    exp1(trials=n); exp2(trials=n); exp3(trials=max(60, n // 2))
    exp4(trials=max(500, n)); exp5(trials=max(60, n // 2)); exp6()
