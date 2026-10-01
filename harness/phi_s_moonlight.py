#!/usr/bin/env python3
"""Discharge phi_S on the REAL MoonLight engine.

phi_S was the one aspect of the framework with no external engine behind it.
The paper claimed its clauses were "written for TeSSLa" and that the engine was
drop-in; having used TeSSLa, that claim is wrong. TeSSLa is a stream language and
phi_S is reachability over a weighted graph. The tool that actually monitors this
logic is MoonLight, which implements STREL over exactly the structure we use: a
weighted graph whose nodes and edges carry attributes evolving in time.

This script encodes the containment clause in MoonLight's script language, builds
the mission location graph and its per-node signals, runs the unmodified engine,
and compares its verdict against our reference monitor on every trace of the
MEDEVAC suite.

The clause. A marked record is contained iff, from the location holding it, there
is no path along traversable vertices that escapes to cross-domain distance >= 1.
In STREL that is `escape`, which is the operator the paper names and until now
only our own code evaluated.

Requires Java 21 (MoonLight ships class-file version 65) and the `moonlight`
package; run with the venv interpreter in this directory.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "vendor"))

from moonlight import ScriptLoader                       # noqa: E402
from mission_rv import F, MissionShield                  # noqa: E402
import mission_scenarios as med                          # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
MLS = os.path.join(HERE, "moonlight", "containment.mls")

# The clause, in prose, because MoonLight's script language has no comments:
# a marked record is contained iff, from the location holding it, there is no
# path along traversable vertices that REACHES a disallowed, traversable
# vertex, and the holding location is not itself in a disallowed domain.
#
# Note on the operator. Our first rendering used `escape`, which asks whether a
# path gets FAR AWAY (cross-domain distance >= 1). That is not the clause: a
# record may legitimately travel one domain hop to an allowed partner. The
# clause asks whether it can get to a DISALLOWED place, which is `reach`. In
# MoonLight `reach` is infix: phi1 reach(w)[a,b] phi2.
SCRIPT = """signal { bool marked; bool trav; bool disallowed; }
space { edges { real hop; } }
domain boolean;
formula containment =
  ! ( marked & ( disallowed | ( trav reach(hop)[0.0, inf] ( disallowed & trav ) ) ) );
"""

def graph_and_signal(w, sh):
    """Build MoonLight's inputs from the world: the informational sub-graph as a
    weighted edge list, and one boolean triple per node.

    Only the informational sort is passed. The physical clauses (standoff,
    corridor) are separate formulas over the physical sort; encoding them is the
    same exercise and we report the containment clause here."""
    nodes = [v for v in w.g.v if w.g.v[v]["sort"] == "info"]
    idx = {n: i for i, n in enumerate(nodes)}
    mark = sh.spatial.MARK
    allowed = sh.spatial.ALLOWED

    held = set()
    for r in w.created:
        if w.marked(r, mark):
            held |= set(w.locations(r))

    sig = []
    for n in nodes:
        sig.append([n in held,
                    bool(w.traversable(n)),
                    w.g.domain(n) not in allowed])
    edges = []
    for a in nodes:
        for b, wt in w.g.adj[a].items():
            if b in idx:
                edges.append([idx[a], idx[b], float(wt)])
    return nodes, idx, sig, edges


def main():
    os.makedirs(os.path.dirname(MLS), exist_ok=True)
    with open(MLS, "w") as f:
        f.write(SCRIPT)
    script = ScriptLoader.loadFromFile(MLS)
    mon = script.getMonitor("containment")

    traces = [("A%d" % (i + 1), t, s)
              for i, (t, s) in enumerate(med.attack_traces())]
    traces += [("B%d" % (i + 1), t, None)
               for i, t in enumerate(med.benign_traces())]

    print("=" * 74)
    print("phi_S CONTAINMENT ON THE REAL MOONLIGHT ENGINE (STREL)")
    print("=" * 74)
    print(f"{'trace':<7}{'step':>6}{'reference S1':>15}{'MoonLight':>12}{'agree':>8}")
    agree = disagree = checked = 0

    for tid, trace, _succ in traces:
        w = med.seed_world()
        sh = MissionShield(block_on=())          # observe, never suppress
        for k, ev in enumerate(trace, 1):
            sh.step(w, ev, enabled=("S", "T", "Sigma"))
            nodes, idx, sig, edges = graph_and_signal(w, sh)
            if not any(s[0] for s in sig):
                continue                          # no marked record placed yet
            ref_ok = sh.spatial.s1_pii_containment(w) != F
            # MoonLight: one time point, signal per node, static graph
            # signature: (locationTimeArray, graph, signalTimeArray,
            # signalValues). The graph is static here, so one location time
            # point carrying one adjacency list.
            res = mon.monitor([0.0], [edges], [0.0], [[s] for s in sig])
            # result is per-node; containment holds globally iff it holds at
            # every node that carries the record
            # MoonLight's boolean domain encodes false as -1.0 and true as
            # +1.0. Reading it with bool() makes every violation look like a
            # satisfaction, because bool(-1.0) is True.
            ml_ok = all(res[i][0][1] > 0 for i, n in enumerate(nodes) if sig[i][0])
            ok = (ref_ok == ml_ok)
            checked += 1
            agree += ok
            disagree += (not ok)
            if not ok:
                print(f"{tid:<7}{k:>6}{str(ref_ok):>15}{str(ml_ok):>12}{'NO':>8}")
            elif not ref_ok:
                print(f"{tid:<7}{k:>6}{'VIOLATED':>15}{'VIOLATED':>12}{'yes':>8}")

    print(f"\nchecked {checked} states; agree {agree}, disagree {disagree}")
    print("MoonLight is the engine of record for phi_S; the reference monitor is")
    print("an independent implementation of the same clause, so a disagreement")
    print("localises a fault in one of the two renderings rather than in either.")


if __name__ == "__main__":
    main()
