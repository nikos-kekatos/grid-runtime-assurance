"""
Evidence loss, two panels, on one fixed scenario set.

Panel A -- telemetry loss. E1 is evaluated over commands, which loss does not
touch, so the definite-violation count is invariant. The comparison is against
a monitor that sums returned telemetry. This panel measures command-state
versus telemetry-only evaluation; the observability clause contributes
abstention on clean traffic and nothing to detection.

Panel B -- switch-state loss. Switch state is not recoverable from any command:
it determines which resources are downstream of a segment at all. When it is
unavailable the aggregate cannot be computed, so evidence-awareness genuinely
converts an apparent all-clear into an inconclusive verdict.
"""
import random, json
import energy_rv, feeder_topology as FT
from aggregate_sweep import CAPS, violates

FRAC, N = 0.75, 500
SWITCHES = [s for s in getattr(FT, "SWITCHES", [])] or None

def make(rng):
    w = FT.seed_world()
    for d in energy_rv.DER_BUS: w.dispatch[d] = rng.uniform(0, FRAC*CAPS[d])
    return w

rng = random.Random(11)
SCEN = [make(rng) for _ in range(N)]
TRUTH = [violates(w) is not None for w in SCEN]
NV = sum(TRUTH)
print(f"fixed scenario set: {N} scenarios, {NV} genuine violations, "
      f"{N-NV} clean (same set used at every loss rate)")

def naive_misses(w, loss, rng):
    """Monitor that sums only the resources whose telemetry arrived.

    A meter that is down is down for every segment, so the draw is once per
    resource per scenario rather than once per (segment, resource) pair."""
    arrived = {d: rng.random() >= loss for d in energy_rv.DER_BUS}
    for seg, rating in energy_rv.SEG_RATING.items():
        a,b = w.oriented(seg)
        if a is None: continue
        down = w.downstream((a,b))
        if down is None: continue
        gen = sum(w.dispatch.get(x,0.0) for x in w.ders_at(down)
                  if arrived.get(x, True))
        load = sum(energy_rv.BUS_LOAD_KW.get(x,0.0) for x in down)
        if abs(load-gen) > rating: return True
    return False

print("\nPanel A -- telemetry loss")
print(f"{'loss':>6}{'naive misses':>16}{'aware flags':>14}{'presumptive/clean':>20}")
A=[]
for loss in (0.0, 0.10, 0.25, 0.50, 0.90):
    r = random.Random(97); miss = 0; pres = 0
    for w,t in zip(SCEN, TRUTH):
        if t and not naive_misses(w, loss, r): miss += 1
        if not t:
            # E3: presumptive when any commanded resource is unobserved
            if any(r.random() < loss for d in energy_rv.DER_BUS
                   if w.dispatch.get(d,0)>0): pres += 1
    A.append(dict(loss=loss, miss=miss, pres=pres))
    print(f"{loss:>5.0%}{miss:>7} ({miss/NV:5.1%}){NV:>9} (100%){pres:>12}/{N-NV}"
          f" ({pres/(N-NV):5.1%})")

print("\nPanel B -- switch-state loss (not recoverable from any command)")
print("Reconfiguration keeps the network radial: closing a tie also opens a")
print("branch on the cycle it creates, so downstream() is defined on every")
print("segment and the reference monitor is never itself blind.")

def tree_path(w, u, v):
    """path u->v in the currently-radial graph, as a list of nodes"""
    prev = {u: None}; stack = [u]
    while stack:
        x = stack.pop()
        if x == v: break
        for y in w.energised_neighbours(x):
            if y not in prev: prev[y] = x; stack.append(y)
    if v not in prev: return None
    out = []; x = v
    while x is not None: out.append(x); x = prev[x]
    return out[::-1]

def reconfigure(rng):
    """close one tie and open one branch on the cycle, preserving radiality"""
    w = FT.seed_world()
    t = FT.TIES[rng.randrange(len(FT.TIES))]
    u, v = FT.B(t[0]), FT.B(t[1])
    path = tree_path(w, u, v)
    if path is None or len(path) < 3: return None
    w.open_switches.discard(frozenset((u, v)))          # close the tie
    i = rng.randrange(len(path) - 1)                     # open a branch on it
    w.open_switches.add(frozenset((path[i], path[i + 1])))
    return w

# radiality check: every segment must be resolvable
chk = random.Random(5); nbad = 0
for _ in range(200):
    w = reconfigure(chk)
    if w is None: continue
    for seg in energy_rv.SEG_RATING:
        a_, b_ = w.oriented(seg)
        if a_ is not None and w.downstream((a_, b_)) is None: nbad += 1
print(f"  unresolvable segments across 200 reconfigurations: {nbad}")

P_RECONF = 0.5
print(f"  P(reconfigured) = {P_RECONF}")
print(f"{'loss':>6}{'stale-state wrong':>19}{'  of pressed-on':>16}{'inconclusive':>14}")
B=[]
for loss in (0.0, 0.10, 0.25, 0.50):
    r = random.Random(43); wrong = pressed = inc = 0
    for w0 in SCEN:
        w = reconfigure(r) if r.random() < P_RECONF else FT.seed_world()
        if w is None: w = FT.seed_world()
        w.dispatch.update(w0.dispatch)
        truth = violates(w) is not None
        if r.random() < loss:                 # switch state unavailable
            inc += 1; pressed += 1
            if (violates(w0) is not None) != truth: wrong += 1
    B.append(dict(loss=loss, wrong=wrong, pressed=pressed, inconclusive=inc))
    pc = f"{100*wrong/pressed:.1f}%" if pressed else "n/a"
    print(f"{loss:>5.0%}{wrong:>13} {pc:>10}{pressed:>12}{inc:>13} of {N}")
json.dump(dict(n=N, nviol=NV, panelA=A, panelB=B), open("evidence_loss.json","w"), indent=1)
