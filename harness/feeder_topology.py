#!/usr/bin/env python3
"""IEEE 33-bus radial distribution feeder, plus the informational sort.

TOPOLOGY PROVENANCE.  The connectivity below is the standard IEEE 33-bus test
system of Baran and Wu: a main feeder 1..18 with three laterals rooted at buses
2, 3 and 6, and five normally-open tie switches.  Only the CONNECTIVITY matters
for the experiments here, because every spatial clause is a reachability query
rather than a power-flow computation.  **Line impedances and the published load
profile are NOT reproduced here and must be taken from the original source
before any claim that depends on them.**  Segment ratings below are illustrative
and chosen so that the aggregate constraint binds at a realistic DER penetration;
they are not from a published data set.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "vendor"))
from mission_rv import LocationGraph            # noqa: E402
import energy_rv                                # noqa: E402

# --- IEEE 33-bus connectivity (Baran & Wu) -----------------------------------
MAIN = [(i, i + 1) for i in range(1, 18)]                      # 1-2 ... 17-18
LAT_A = [(2, 19), (19, 20), (20, 21), (21, 22)]
LAT_B = [(3, 23), (23, 24), (24, 25)]
LAT_C = [(6, 26), (26, 27), (27, 28), (28, 29), (29, 30),
         (30, 31), (31, 32), (32, 33)]
BRANCHES = MAIN + LAT_A + LAT_B + LAT_C
TIES = [(8, 21), (9, 15), (12, 22), (18, 33), (25, 29)]        # normally open

B = lambda i: f"bus_{i}"

# --- DER placement (illustrative) --------------------------------------------
DER_BUSES = [7, 10, 14, 16, 18, 24, 25, 30, 31, 32, 33, 21, 22]
DERS = {f"der_{b}": B(b) for b in DER_BUSES}

# --- segment ratings, kW (illustrative; see provenance note) ------------------
# Published Baran--Wu load profile (kW per bus, 1-indexed) and the base-case
# branch flows a power-flow solution gives on it. Ratings are derived from the
# flows rather than assigned by hand: a rating below the base-case flow makes
# the feeder violate its own limits with no DER dispatched at all.
BUS_LOAD_KW = {2: 100.0, 3: 90.0, 4: 120.0, 5: 60.0, 6: 60.0, 7: 200.0, 8: 200.0, 9: 60.0, 10: 60.0, 11: 45.0, 12: 60.0, 13: 60.0, 14: 120.0, 15: 60.0, 16: 60.0, 17: 60.0, 18: 90.0, 19: 90.0, 20: 90.0, 21: 90.0, 22: 90.0, 23: 90.0, 24: 420.0, 25: 420.0, 26: 60.0, 27: 60.0, 28: 60.0, 29: 120.0, 30: 200.0, 31: 150.0, 32: 210.0, 33: 60.0}

BASE_FLOW_KW = {'1-2': 3917.7, '2-3': 3444.3, '3-4': 2362.9, '4-5': 2223.0, '5-6': 2144.3, '6-7': 1095.3, '7-8': 893.4, '8-9': 688.5, '9-10': 624.3, '10-11': 560.8, '11-12': 515.2, '12-13': 454.3, '13-14': 391.7, '14-15': 270.9, '15-16': 210.6, '16-17': 150.3, '17-18': 90.1, '2-19': 361.1, '19-20': 271.0, '20-21': 180.1, '21-22': 90.0, '3-23': 939.6, '23-24': 846.4, '24-25': 421.3, '6-26': 950.8, '26-27': 888.2, '27-28': 824.8, '28-29': 753.5, '29-30': 625.7, '30-31': 421.8, '31-32': 270.2, '32-33': 60.0, '8-21': 0.0, '9-15': 0.0, '12-22': 0.0, '18-33': 0.0, '25-29': 0.0}

HEADROOM = 1.6            # rating = HEADROOM x base-case flow

def _ratings():
    """Segment ratings at HEADROOM times the base-case flow, as
    powerflow_validation.prepare() does it. The earlier hand-assigned values
    (900/600/300 kW by depth) were below the base-case flow on 22 of 37
    segments, so the feeder violated them before any DER dispatched."""
    r = {}
    for a, b in BRANCHES + TIES:
        key = f"{min(a, b)}-{max(a, b)}"
        base = BASE_FLOW_KW.get(key)
        if base is None or base <= 0.0:
            base = 300.0 / HEADROOM          # ties carry no base flow when open
        r[frozenset((B(a), B(b)))] = round(HEADROOM * base, 1)
    return r


def build_graph():
    g = LocationGraph()
    # --- electrical sort ------------------------------------------------------
    for i in range(1, 34):
        labels = {"substation"} if i == 1 else set()
        if B(i) in DERS.values():
            labels.add("der_point")
        g.add(B(i), "phys", labels=labels)
    for a, b in BRANCHES:
        g.link(B(a), B(b), weight=1.0)
    for a, b in TIES:
        g.link(B(a), B(b), weight=1.0)

    # --- informational sort ---------------------------------------------------
    g.add("dso_scada",  "info", labels={"home"},  domain="DSO")
    g.add("dso_dms",    "info", labels={},        domain="DSO")
    g.add("market",     "info", labels={},        domain="MARKET")
    g.add("aggregator", "info", labels={},        domain="AGGREGATOR")
    g.add("third_party", "info", labels={},       domain="THIRD_PARTY")
    g.link("dso_scada", "dso_dms")                 # w = 0, within DSO
    g.link("dso_scada", "market")                  # w = 1, allowed
    g.link("dso_dms", "aggregator")                # w = 1, DISALLOWED
    g.link("aggregator", "third_party")            # w = 1, DISALLOWED

    # --- the join between the two sorts ---------------------------------------
    # Without this the "two-sorted graph" is two disconnected components and
    # nothing is typed into both. Each metered connection point is joined to the
    # informational vertex that first receives its telemetry, at zero weight:
    # the measurement does not cross a release boundary by being measured.
    for bus in sorted(set(DERS.values())):
        g.link(bus, "dso_scada", weight=0.0)
    return g


def seed_world(open_ties=True):
    energy_rv.DER_BUS = dict(DERS)
    energy_rv.SEG_RATING = _ratings()
    energy_rv.BUS_LOAD_KW = {B(i): kw for i, kw in BUS_LOAD_KW.items()}
    w = energy_rv.FeederWorld(build_graph(), substation=B(1))
    if open_ties:                                  # ties normally open
        for a, b in TIES:
            w.open_switches.add(frozenset((B(a), B(b))))
    w.open_channel = lambda s, d: w.open_channels.add((s, d))
    w.open_channels.add(("dso_scada", "dso_dms"))
    return w
