#!/usr/bin/env python3
"""Energy instantiation of the multi-aspect RV framework (AIoT-RSE 2026).

Domain: an aggregator dispatching distributed energy resources (DERs) on a radial
distribution feeder, under constraints held by the network operator.

WHAT IS REUSED, WHAT IS NEW.  The two-sorted location graph, the four-valued
verdict algebra, the shadow-evaluate-then-commit shield and the clause structure
are inherited from the mission instantiation unchanged.  What is genuinely new is
that the spatial aspect here is *quantitative*: an aggregate-loading constraint
sums dispatched power over the DERs reachable through a segment, where the
mission clauses were qualitative reachability.  That is an extension of the
method, not a re-skin of it, and we say so rather than claiming portability we
did not get for free.

  phi_S  E1 aggregate loading   for each segment s, sum of dispatch over the DERs
                                DOWNSTREAM of s must not exceed its rating.
                                "Downstream" is a REACHABILITY query, which is
                                what keeps it correct under reconfiguration.
         E2 disclosure          granular telemetry must not reach a domain
                                outside the operator/market boundary
         E3 observability       a dispatched DER must have a live measurement
                                point upstream of it, else the constraint that
                                governs it cannot be evaluated

  phi_T  T1 authorisation       dispatch above a threshold needs a DSO consent
                                for THAT resource, fresh within a window
         T2 ramp                change per interval bounded per resource
         T3 window              activation inside the contracted window

  phi_Sigma  k-anonymity        an "aggregated" telemetry product discloses if
                                fewer than k premises contribute to it.  Unlike
                                the lexical cue matcher of the mission
                                instantiation, this is a COMPUTABLE property, so
                                the semantic aspect here is the most rigorous of
                                the three rather than the weakest.

Pure stdlib.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "vendor"))

from agent_rv_eval import T, F, Tp, Fp, compose, Event      # noqa: E402
from mission_rv import LocationGraph                        # noqa: E402

# release domains a granular telemetry product may legitimately occupy
ALLOWED_DOMAINS = frozenset({"DSO", "MARKET"})
K_ANON = 5                      # minimum premises per published aggregate


# =============================================================================
#  World
# =============================================================================
class FeederWorld:
    """State the three aspects read: the graph, DER dispatch, switch states,
    telemetry liveness, consents, and published data products."""

    def __init__(self, graph, substation="bus_1"):
        self.g = graph
        self.substation = substation
        self.dispatch = {}            # der -> kW currently commanded (signed)
        self.prev_dispatch = {}       # der -> kW at the previous interval
        self.open_switches = set()    # tie/sectionaliser switches currently OPEN
        self.dead_telemetry = set()   # buses whose measurement is not reporting
        self.consent = {}             # (der) -> (ts, actor)
        self.window = {}              # der -> (start, end)
        self.products = {}            # product -> {"premises": n, "granular": bool,
                                      #             "derived_from": str|None}
        self.holds = {}               # info vertex -> set(product)
        self.open_channels = set()
        self.published = set()

    # --- electrical helpers -------------------------------------------------
    def _edge_open(self, a, b):
        return frozenset((a, b)) in self.open_switches

    def energised_neighbours(self, v):
        """Neighbours of v over closed switches, WITHIN THE ELECTRICAL SORT.
        The two sorts are joined at each metered connection point, so an
        electrical walk that does not filter on sort escapes into the
        informational sort and returns most of the graph."""
        return [n for n in self.g.adj[v]
                if self.g.v[n]["sort"] == "phys" and not self._edge_open(v, n)]

    def oriented(self, seg):
        """Order a frozenset segment as (upstream, downstream) by hop distance
        from the substation. Returns (None, None) if the segment is open or
        either end is islanded."""
        a, b = sorted(seg)   # stable: frozenset order is hash-dependent
        if self._edge_open(a, b):
            return (None, None)
        ha, hb = self._hops(a), self._hops(b)
        if ha is None or hb is None:
            return (None, None)
        return (a, b) if ha <= hb else (b, a)

    def downstream(self, seg):
        """Buses reachable from segment `seg` moving AWAY from the substation,
        over closed switches only.  This is the reachability query that keeps
        aggregate constraints correct when the network is reconfigured; a static
        'which DERs are on this feeder' list goes stale the moment a switch
        moves."""
        a, b = seg
        if self._edge_open(a, b):
            return set()
        # whichever endpoint is further from the substation roots the subtree
        da, db = self._hops(a), self._hops(b)
        if da is None and db is None:
            return set()            # segment is de-energised: nothing downstream
        if da is None:
            root, parent = a, b     # only `a` is islanded, so it is the far side
        elif db is None:
            root, parent = b, a
        else:
            root, parent = (b, a) if db >= da else (a, b)
        # Blocking only `parent` is enough on a tree. On a meshed network the
        # walk returns through a closed tie and swallows the source, silently
        # reporting most of the feeder as "downstream" of a mid-trunk segment.
        # Downstream-of-a-segment is only well defined on a tree, so say so
        # rather than return a wrong set: the caller maps None to a presumptive
        # verdict, which is the honest answer.
        seen, stack = {root}, [root]
        while stack:
            v = stack.pop()
            for n in self.energised_neighbours(v):
                if n != parent and n not in seen:
                    seen.add(n); stack.append(n)
        if self.substation in seen:
            return None             # meshed: the walk reached the source
        return seen

    def _hops(self, v):
        """Hop distance from the substation over closed switches (None if the
        vertex is islanded)."""
        seen, frontier, d = {self.substation}, [self.substation], 0
        while frontier:
            nxt = []
            for u in frontier:
                if u == v:
                    return d
                for n in self.energised_neighbours(u):
                    if n not in seen:
                        seen.add(n); nxt.append(n)
            frontier, d = nxt, d + 1
        return None

    def ders_at(self, buses):
        return [d for d, bus in DER_BUS.items() if bus in buses]

    def upstream_live(self, der):
        """Is the measurement that GOVERNS this DER reporting?  That is the bus
        one hop toward the source: it is the point at which the aggregate through
        the DER's segment is observed.  An earlier version accepted any live bus
        anywhere, which made every DER trivially observable."""
        bus = DER_BUS[der]
        d = self._hops(bus)
        if d is None:
            return False                      # islanded: nothing observes it
        if d == 0:
            return bus not in self.dead_telemetry
        for n in self.energised_neighbours(bus):
            dn = self._hops(n)
            if dn is not None and dn < d:     # the parent bus
                return n not in self.dead_telemetry
        return False


DER_BUS = {}        # populated by the scenario module
SEG_RATING = {}     # frozenset({a,b}) -> kW rating
BUS_LOAD_KW = {}    # bus label -> kW of load behind that bus


# =============================================================================
#  phi_S : quantitative spatial monitor
# =============================================================================
class FeederSpatial:
    def __init__(self, k=K_ANON):
        self.k = k
        self.last_props = {}

    def e1_aggregate_loading(self, w):
        """For every segment: the NET power crossing it must not exceed its
        rating. Net, not generation alone: what loads a conductor is downstream
        load minus downstream generation, and summing generation only is a clause
        correct in form and wrong in content -- on this feeder added generation
        REDUCES the flow on the trunk rather than increasing it. The magnitude is
        taken because a segment is equally overloaded by import and by export.
        Each DER may still be individually within nameplate."""
        undecidable = False
        for seg, rating in SEG_RATING.items():
            # SEG_RATING is keyed by frozenset, so tuple(seg) has arbitrary
            # order. downstream() walks AWAY from the substation and needs the
            # upstream end first: taking the order from the frozenset made it
            # walk the wrong way and return most of the feeder.
            a, b = w.oriented(seg)
            if a is None:
                continue                      # segment is open or islanded
            down = w.downstream((a, b))
            if down is None:
                # the segment has no well-defined downstream set on this
                # topology; the clause is not decidable, not satisfied
                undecidable = True
                continue
            gen = sum(w.dispatch.get(d, 0.0) for d in w.ders_at(down))
            load = sum(BUS_LOAD_KW.get(b_, 0.0) for b_ in down)
            if abs(load - gen) > rating:
                return F
        return Fp if undecidable else T

    def e2_disclosure(self, w):
        """Granular telemetry must not reach a domain outside the operator or
        market boundary -- the same `reach` formulation as the mission clause."""
        disallowed = lambda l: (w.g.v[l]["sort"] == "info"
                                and w.g.domain(l) not in ALLOWED_DOMAINS)
        trav = lambda l: (w.g.has_label(l, "home")
                          or any(d == l for _s, d in w.open_channels))
        for prod, meta in w.products.items():
            if not meta.get("granular"):
                continue
            for l in [v for v, ps in w.holds.items() if prod in ps]:
                if disallowed(l):
                    return F
                if w.g.reach(l, trav, lambda x: disallowed(x) and trav(x)):
                    return F
        return T

    def e3_observability(self, w):
        """A dispatched DER whose governing constraint cannot be evaluated for
        want of telemetry yields a PRESUMPTIVE verdict, never a silent pass."""
        for d, kw in w.dispatch.items():
            if kw != 0.0 and not w.upstream_live(d):
                return Fp
        return T

    def check(self, w):
        vals = (self.e1_aggregate_loading(w), self.e2_disclosure(w),
                self.e3_observability(w))
        self.last_props = dict(zip(("E1", "E2", "E3"), vals))
        if F in vals:
            return F
        return Fp if Fp in vals else T


# =============================================================================
#  phi_T : first-order past-time obligations
# =============================================================================
class FeederTemporal:
    W_CONSENT = 4.0
    AUTHORITIES = frozenset({"dso", "operator", "market"})
    RAMP_LIMIT = 50.0            # kW per interval, per resource
    CONSENT_THRESHOLD = 100.0    # kW above which a DSO consent is required

    def t1_authorisation(self, w, der, kw, now):
        if abs(kw) <= self.CONSENT_THRESHOLD:
            return T
        rec = w.consent.get(der)
        if rec is None:
            return F
        ts, actor = rec
        if actor not in self.AUTHORITIES:      # the aggregator cannot self-consent
            return F
        return T if 0.0 <= now - ts <= self.W_CONSENT else F

    def t2_ramp(self, w, der, kw):
        prev = w.prev_dispatch.get(der, 0.0)
        return T if abs(kw - prev) <= self.RAMP_LIMIT else F

    def t3_window(self, w, der, now):
        win = w.window.get(der)
        if win is None:
            return T
        return T if win[0] <= now <= win[1] else F


# =============================================================================
#  phi_Sigma : k-anonymity over published aggregates
# =============================================================================
class FeederSemantic:
    """A computable disclosure test, in contrast to the lexical cue matcher the
    mission instantiation had to settle for."""

    def __init__(self, k=K_ANON):
        self.k = k

    def granular(self, meta):
        """A product is granular if it is explicitly per-premises, or if it is an
        aggregate over fewer than k premises -- an aggregate of one is a
        measurement."""
        if meta.get("per_premises"):
            return True
        return meta.get("premises", 0) < self.k

    def laundered(self, w, prod):
        """Derived from a granular source, unmarked, and still resolving fewer
        than k premises."""
        meta = w.products.get(prod, {})
        src = meta.get("derived_from")
        if src is None:
            return False
        smeta = w.products.get(src, {})
        return (self.granular(smeta) or smeta.get("granular")) \
            and not meta.get("marked") and self.granular(meta)


# =============================================================================
#  Shield
# =============================================================================
class FeederShield:
    def __init__(self, block_on=(F,)):
        self.spatial = FeederSpatial()
        self.past = FeederTemporal()
        self.sem = FeederSemantic()
        self.block_on = frozenset(block_on)
        self.blocks = []
        self.last = {}

    def step(self, w, ev, enabled=("S", "T", "Sigma")):
        enabled = set(enabled)
        vs = vt = vsig = T
        commit = lambda: None
        tool, a = ev.tool, ev.args

        if tool == "set_dispatch":
            der, kw = a["der"], a["kw"]
            prev = w.dispatch.get(der, 0.0)
            w.dispatch[der] = kw
            vs = self.spatial.check(w)
            w.dispatch[der] = prev
            if "T" in enabled:
                for v in (self.past.t1_authorisation(w, der, kw, ev.ts),
                          self.past.t2_ramp(w, der, kw),
                          self.past.t3_window(w, der, ev.ts)):
                    if v == F:
                        vt = F
            def commit(der=der, kw=kw):
                w.prev_dispatch[der] = w.dispatch.get(der, 0.0)
                w.dispatch[der] = kw

        elif tool == "grant_consent":
            def commit():
                w.consent[a["der"]] = (ev.ts, ev.actor)

        elif tool == "open_switch":
            seg = frozenset((a["a"], a["b"]))
            w.open_switches.add(seg)
            vs = self.spatial.check(w)
            w.open_switches.discard(seg)
            def commit(seg=seg):
                w.open_switches.add(seg)

        elif tool == "close_switch":
            seg = frozenset((a["a"], a["b"]))
            had = seg in w.open_switches
            w.open_switches.discard(seg)
            vs = self.spatial.check(w)
            if had:
                w.open_switches.add(seg)
            def commit(seg=seg):
                w.open_switches.discard(seg)

        elif tool == "publish_product":
            prod = a["product"]
            meta = {k: v for k, v in a.items() if k != "product"}
            meta["granular"] = self.sem.granular(meta) if "Sigma" in enabled else False
            saved = w.products.get(prod)
            w.products[prod] = meta
            w.holds.setdefault(a.get("dest", "dso_scada"), set()).add(prod)
            vs = self.spatial.check(w)
            if "Sigma" in enabled and self.sem.laundered(w, prod):
                vsig = F
            w.holds[a.get("dest", "dso_scada")].discard(prod)
            if saved is None:
                w.products.pop(prod, None)
            else:
                w.products[prod] = saved
            def commit(prod=prod, meta=meta, dest=a.get("dest", "dso_scada")):
                w.products[prod] = meta
                w.holds.setdefault(dest, set()).add(prod)
                w.published.add(prod)

        elif tool == "open_feed":
            src, dst = a["src"], a["dst"]
            w.open_channels.add((src, dst))
            vs = self.spatial.check(w)
            w.open_channels.discard((src, dst))
            def commit(src=src, dst=dst):
                w.open_channels.add((src, dst))

        elif tool == "telemetry_loss":
            def commit():
                w.dead_telemetry.add(a["bus"])

        verdicts = [(k, v) for k, v in (("S", vs), ("T", vt), ("Sigma", vsig))
                    if k in enabled]
        verdict, prov = compose(verdicts) if verdicts else (T, set())
        blocked = verdict in self.block_on
        if not blocked:
            commit()
        self.last = {"per_aspect": dict(verdicts), "composed": verdict,
                     "prov": sorted(prov), "tool": tool}
        if blocked:
            self.blocks.append((ev.turn, verdict, prov))
        return blocked, prov, verdict
