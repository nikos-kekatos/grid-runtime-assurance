"""
Aggregate-only penetration sweep.

Every resource is capped below the limit at which it could breach any segment
it sits behind acting alone. Under that cap a single setpoint is always
network-admissible, so every violation the monitor reports requires two or
more resources acting together. This removes the single-resource/tail-rating
confound in the earlier uncapped sweep.
"""
import random, json
N = 500
import energy_rv, feeder_topology as FT

def solo_admissible_caps(w):
    """Bounds non-negative injection only; see the assertion below."""
    """cap(d) = min over segments s behind which d sits of (load(s) + rating(s)).

    A lone resource at value v behind s gives |load(s) - v| > rating(s) only
    when v > load(s) + rating(s); below that bound no single setpoint can
    breach s. Taking the minimum over all such s makes d admissible alone
    everywhere."""
    cap = {d: float("inf") for d in energy_rv.DER_BUS}
    for seg, rating in energy_rv.SEG_RATING.items():
        a, b = w.oriented(seg)
        if a is None: continue
        down = w.downstream((a, b))
        if down is None: continue
        load = sum(energy_rv.BUS_LOAD_KW.get(x, 0.0) for x in down)
        for d in w.ders_at(down):
            cap[d] = min(cap[d], load + rating)
    # 1% margin so isolation-admissibility is strict, not boundary-exact
    return {d: 0.99 * c for d, c in cap.items()}

def violates(w):
    for seg, rating in energy_rv.SEG_RATING.items():
        a, b = w.oriented(seg)
        if a is None: continue
        down = w.downstream((a, b))
        if down is None: continue
        gen  = sum(w.dispatch.get(x, 0.0) for x in w.ders_at(down))
        load = sum(energy_rv.BUS_LOAD_KW.get(x, 0.0) for x in down)
        if abs(load - gen) > rating:
            return seg, len(list(w.ders_at(down)))
    return None

TOTAL_LOAD = sum(FT.BUS_LOAD_KW.values())
base = FT.seed_world()
CAPS = solo_admissible_caps(base)
print("per-resource solo-admissible caps (kW):")
print("  min %.0f  median %.0f  max %.0f" % (
    min(CAPS.values()), sorted(CAPS.values())[len(CAPS)//2], max(CAPS.values())))

def main():
    out = []
    for frac in (0.25, 0.50, 0.75, 0.90, 1.00):
        rng = random.Random(11); nviol = 0; solo = 0; sizes = []
        for _ in range(N):
            w = FT.seed_world()
            for d in energy_rv.DER_BUS:
                w.dispatch[d] = rng.uniform(0, frac * CAPS[d])
            # assert admissibility in isolation
            v = violates(w)
            if v:
                nviol += 1; sizes.append(v[1])
                if v[1] < 2: solo += 1
        pen = 100 * sum(frac * CAPS[d] for d in energy_rv.DER_BUS) / TOTAL_LOAD
        med = sorted(sizes)[len(sizes)//2] if sizes else 0
        out.append(dict(frac=frac, pen=pen, nviol=nviol, solo=solo, med_ders=med))
        print(f"  cap fraction {frac:.2f}  penetration {pen:5.0f}%  "
              f"violations {nviol:3d}/500  single-resource {solo}  "
              f"median resources behind binding segment {med}")

    # Every resource, acting alone anywhere in [0, cap], must be admissible on
    # every segment. The caps bound non-negative injection; a negative setpoint is
    # a load and is outside the class of actions this sweep draws from.
    for d in energy_rv.DER_BUS:
        for k in range(0, 201):
            solo = FT.seed_world()
            for x in energy_rv.DER_BUS: solo.dispatch[x] = 0.0
            solo.dispatch[d] = CAPS[d] * k / 200.0
            assert violates(solo) is None, f"{d} breaches alone at {k/200:.3f} of cap"
    print(f"verified: each of {len(CAPS)} resources is admissible alone across "
          f"[0, cap], 201 points each")

    # report the SMALLEST cohort on any violated segment, which does not depend on
    # scan order the way "first violated segment" does
    import statistics
    def min_cohort(w):
        best = None
        for seg, rating in energy_rv.SEG_RATING.items():
            a, b = w.oriented(seg)
            if a is None: continue
            down = w.downstream((a, b))
            if down is None: continue
            gen  = sum(w.dispatch.get(x, 0.0) for x in w.ders_at(down))
            load = sum(energy_rv.BUS_LOAD_KW.get(x, 0.0) for x in down)
            if abs(load - gen) > rating:
                n = len([x for x in w.ders_at(down) if w.dispatch.get(x, 0) > 0])
                best = n if best is None else min(best, n)
        return best
    for r in out:
        rng2 = random.Random(11); cs = []
        for _ in range(N):
            w = FT.seed_world()
            for d in energy_rv.DER_BUS: w.dispatch[d] = rng2.uniform(0, r["frac"]*CAPS[d])
            c = min_cohort(w)
            if c is not None: cs.append(c)
        r["min_cohort_min"] = min(cs) if cs else None
        r["min_cohort_med"] = statistics.median(cs) if cs else None
        print(f"  frac {r['frac']:.2f}: smallest cohort on any violated segment, "
              f"min {r['min_cohort_min']}, median {r['min_cohort_med']}")
    json.dump(out, open("aggregate_sweep.json","w"), indent=1)


if __name__ == "__main__":
    main()
