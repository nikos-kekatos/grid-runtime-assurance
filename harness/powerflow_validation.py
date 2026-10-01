#!/usr/bin/env python3
"""Validate the connectivity-based spatial monitor against real power flow.

Four gaps separated this work from the power-systems state of the art, and this
script addresses all four with one instrument:

  POWER FLOW      pandapower solves the network rather than approximating it, so
                  line loading and bus voltage are ground truth rather than a
                  proxy.
  REALISM         the test systems are pandapower's built-in cases, carrying the
                  published line impedances and load profiles, not our synthetic
                  ratings.
  PUBLISHED TOOL  pandapower (Thurner et al., IEEE Trans. Power Systems, 2018) is
                  a widely used, peer-reviewed tool. It is the baseline; we are
                  measured against it rather than against something we wrote.
  SCALE           several networks, thousands of scenarios, rather than one
                  33-bus feeder and 400 draws.

What is measured. Our spatial clause decides aggregate loading by summing
dispatch over the resources reachable through a segment. That is a connectivity
computation: it sees neither the underlying load, nor losses, nor voltage. We
quantify exactly how much that costs, in three parts:

  1. a GENERATION-ONLY proxy, which is what the framework currently computes;
  2. a NET proxy, still connectivity-only, but netting downstream load against
     downstream generation, which is the fairer version of the same idea;
  3. voltage, which no connectivity computation can see at all.

The honest expectation is that the net proxy tracks thermal loading well and
that voltage is invisible to both. Reporting the size of each gap is the point.
"""
import argparse
import warnings

warnings.filterwarnings("ignore")
import pandapower as pp                       # noqa: E402
import pandapower.networks as nw              # noqa: E402
import pandapower.topology as top             # noqa: E402
import networkx as nx                         # noqa: E402
import random                                 # noqa: E402
import numpy as np                            # noqa: E402
from pandapower.pypower.makePTDF import makePTDF   # noqa: E402


def ptdf_of(net):
    """Power transfer distribution factors: the sensitivity of each line's real
    flow to injection at each bus, computed once from topology and impedances.

    This is the generalisation the connectivity clause needs. On a radial,
    singly-fed feeder every entry is exactly 0 or 1 and the matrix IS the
    downstream indicator the spatial monitor already computes; where a network
    has several sources or parallel paths, flow divides and the entries become
    fractional. So the reachability formulation is not an approximation that
    PTDF replaces, it is the degenerate case that PTDF generalises."""
    ppc = net["_ppc"]
    lk = net["_pd2ppc_lookups"]
    bus_l = lk["bus"]
    ln = lk["branch"]["line"]
    rows = np.arange(ln[0], ln[1])
    cols = np.array([bus_l[b] for b in net.bus.index])

    # PTDF is undefined ACROSS islands: an injection in one island cannot change
    # a flow in another, so the matrix is block diagonal and building it globally
    # is singular. mv_oberrhein is two islands, each with its own source, so we
    # build one block per island and assemble. This is a structural property of
    # the quantity, not a defect of the tool.
    g = top.create_nxgraph(net, respect_switches=True)
    comps = list(nx.connected_components(g))
    H = np.zeros((len(rows), len(cols)))
    for comp in comps:
        cbuses = [b for b in net.bus.index if b in comp]
        if not cbuses:
            continue
        clines = [i for i in net.line.index
                  if int(net.line.at[i, "from_bus"]) in comp
                  and int(net.line.at[i, "to_bus"]) in comp]
        if not clines:
            continue
        srcs = [int(b) for b in net.ext_grid.bus if b in comp]
        if not srcs:
            continue
        try:
            Hc = makePTDF(ppc["baseMVA"], ppc["bus"], ppc["branch"],
                          slack=bus_l[srcs[0]])
        except Exception:
            continue
        li = {v: k for k, v in enumerate(net.line.index)}
        bi = {v: k for k, v in enumerate(net.bus.index)}
        for l in clines:
            for b in cbuses:
                H[li[l], bi[b]] = Hc[rows[li[l]], bus_l[b]]
    return H, list(net.line.index), list(net.bus.index)

V_MIN, V_MAX = 0.95, 1.05          # statutory band, per unit
THERMAL_PCT = 100.0                # line loading at which the thermal limit binds

NETWORKS = {
    "case33bw (IEEE 33-bus, Baran & Wu)": nw.case33bw,
    "CIGRE MV benchmark":                 lambda: nw.create_cigre_network_mv(with_der="all"),
    "mv_oberrhein (real German MV)":      nw.mv_oberrhein,
}


def prepare(net, der_frac=0.5, seed=0, headroom=1.6):
    """Site DERs at a sample of load buses, and size every line rating from the
    BASE-CASE flow rather than picking a number. A rating of `headroom` times the
    base-case current is how a feeder is actually dimensioned, and it makes
    `loading_percent` a meaningful quantity instead of an artefact of whatever
    default the test case happens to carry."""
    rng = random.Random(seed)
    pp.runpp(net, numba=False)
    base_i = net.res_line.i_ka.abs().fillna(0.0)
    floor = max(1e-3, float(base_i.max()) * 0.05)
    net.line["max_i_ka"] = (base_i * headroom).clip(lower=floor).values
    buses = list(net.load.bus.unique())
    rng.shuffle(buses)
    ders = buses[:max(1, int(len(buses) * der_frac))]
    for b in ders:
        pp.create_sgen(net, b, p_mw=0.0, name=f"der_{b}")
    return ders


def downstream_sets(net):
    """For each line, the buses on its far side, by graph reachability with that
    line removed. This is the same computation the spatial monitor performs."""
    g = top.create_nxgraph(net, respect_switches=True)
    # A network may be fed by SEVERAL sources: mv_oberrhein has two, feeding two
    # separate components. Taking a single slack makes "downstream" meaningless
    # for every line the chosen source does not feed, and silently inverts the
    # subtree for about half the network. The far side is the side containing NO
    # source, so all sources must be considered.
    slacks = set(int(b) for b in net.ext_grid.bus)
    for tab in ("gen", "sgen"):                 # a generator bus is not a source
        pass
    out = {}
    for idx, ln in net.line.iterrows():
        u, v = int(ln.from_bus), int(ln.to_bus)
        if not g.has_edge(u, v):
            continue
        data = dict(g[u][v])
        g.remove_edge(u, v)
        cu = nx.node_connected_component(g, u) if u in g else set()
        cv = nx.node_connected_component(g, v) if v in g else set()
        u_fed, v_fed = bool(cu & slacks), bool(cv & slacks)
        if v_fed and not u_fed:
            far = cu
        elif u_fed and not v_fed:
            far = cv
        else:
            far = set()          # both or neither side fed: not a radial cut
        for k, d in data.items():
            g.add_edge(u, v, key=k, **d)
        out[idx] = far
    return out


def scenario(net, ders, rng, level):
    for i, b in zip(net.sgen.index, net.sgen.bus):
        net.sgen.at[i, "p_mw"] = max(0.0, rng.gauss(level, level * 0.3))


def run_network(name, factory, n_scen, seed):
    net = factory()
    ders = prepare(net, seed=seed)
    down = downstream_sets(net)
    rng = random.Random(seed)

    load_by_bus = net.load.groupby("bus").p_mw.sum().to_dict()

    tp_g = fn_g = fp_g = tn_g = 0        # generation-only proxy vs thermal truth
    tp_n = fn_n = fp_n = tn_n = 0        # net proxy vs thermal truth
    tp_p = fn_p = fp_p = tn_p = 0        # PTDF-weighted vs thermal truth
    H, lidx, bidx = ptdf_of(net)
    cap_mw = {i: float(net.line.at[i, "max_i_ka"])
                 * float(net.bus.at[int(net.line.at[i, "from_bus"]), "vn_kv"])
                 * (3 ** 0.5) for i in net.line.index}
    volt_only = both_v = n_ok = 0        # voltage violations, seen/unseen
    fails = 0

    for s in range(n_scen):
        # sweep from well under to well over the point at which lines bind, so
        # that both violations and clean cases are represented
        base = float(net.load.p_mw.sum()) / max(1, len(net.sgen))
        # The lower end of this range decides how many CLEAN scenarios the
        # sweep produces. On CIGRE the old floor of 0.1x left only 3 of 119
        # clean, so a false-alarm rate there had a denominator of 3 and carried
        # no information. Starting near zero samples the compliant region too.
        scenario(net, ders, rng, level=rng.uniform(0.0, 3.0) * base)
        try:
            pp.runpp(net, numba=False)
        except Exception:
            fails += 1
            continue

        thermal_truth = bool((net.res_line.loading_percent > THERMAL_PCT).any())
        volt_truth = bool((net.res_bus.vm_pu < V_MIN).any()
                          or (net.res_bus.vm_pu > V_MAX).any())

        gen_by_bus = net.sgen.groupby("bus").p_mw.sum().to_dict()
        # the monitor's own rating proxy: the line's rated current at nominal
        # voltage, expressed in MW, so both proxies are compared on like terms
        proxy_g = proxy_n = False
        for idx, far in down.items():
            vn = float(net.bus.at[int(net.line.at[idx, "from_bus"]), "vn_kv"])
            cap = float(net.line.at[idx, "max_i_ka"]) * vn * (3 ** 0.5)
            g_sum = sum(gen_by_bus.get(b, 0.0) for b in far)
            l_sum = sum(load_by_bus.get(b, 0.0) for b in far)
            if g_sum > cap:
                proxy_g = True
            if abs(l_sum - g_sum) > cap:
                proxy_n = True

        # PTDF prediction: net injection per bus, flows by one matrix product
        inj = np.array([gen_by_bus.get(b, 0.0) - load_by_bus.get(b, 0.0)
                        for b in bidx])
        flows = H.dot(inj)
        proxy_p = any(abs(f) > cap_mw[i] for f, i in zip(flows, lidx))
        tp_p += thermal_truth and proxy_p; fn_p += thermal_truth and not proxy_p
        fp_p += (not thermal_truth) and proxy_p
        tn_p += (not thermal_truth) and (not proxy_p)

        for truth, proxy, acc in ((thermal_truth, proxy_g, "g"),
                                  (thermal_truth, proxy_n, "n")):
            if acc == "g":
                tp_g += truth and proxy; fn_g += truth and not proxy
                fp_g += (not truth) and proxy; tn_g += (not truth) and (not proxy)
            else:
                tp_n += truth and proxy; fn_n += truth and not proxy
                fp_n += (not truth) and proxy; tn_n += (not truth) and (not proxy)

        if volt_truth and not thermal_truth:
            volt_only += 1
        elif volt_truth:
            both_v += 1
        else:
            n_ok += 1

    n = n_scen - fails
    print(f"\n{name}   buses {len(net.bus)}  lines {len(net.line)}  "
          f"DERs {len(net.sgen)}  scenarios {n}")
    if n == 0:
        print("   no scenario converged"); return
    for label, (tp, fn, fp, tn) in (("generation-only proxy", (tp_g, fn_g, fp_g, tn_g)),
                                    ("net (load - gen) proxy", (tp_n, fn_n, fp_n, tn_n)),
                                    ("PTDF-weighted", (tp_p, fn_p, fp_p, tn_p))):
        pos, neg = tp + fn, fp + tn
        rec = 100 * tp / pos if pos else float("nan")
        far = 100 * fp / neg if neg else float("nan")
        print(f"   {label:<24} thermal recall {rec:5.1f}%   "
              f"false alarms {far:5.1f}%   (violations {pos}, clean {neg})")
    vt = volt_only + both_v
    print(f"   voltage violations       {vt:>4} of {n}"
          f"   invisible to any connectivity proxy: {volt_only}"
          f" ({100*volt_only/max(1,n):.1f}% of scenarios)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenarios", type=int, default=300)
    ap.add_argument("--seed", type=int, default=11)
    a = ap.parse_args()
    print("=" * 78)
    print("CONNECTIVITY PROXY vs REAL POWER FLOW (pandapower as the baseline)")
    print("=" * 78)
    print(f"thermal limit {THERMAL_PCT:.0f}% line loading; "
          f"voltage band [{V_MIN}, {V_MAX}] pu")
    for name, factory in NETWORKS.items():
        try:
            run_network(name, factory, a.scenarios, a.seed)
        except Exception as e:
            print(f"\n{name}: FAILED  {type(e).__name__}: {str(e)[:120]}")
    print("\nRecall is the fraction of real thermal violations the proxy catches;")
    print("false alarms are clean scenarios it refuses. Voltage is reported")
    print("separately because no connectivity computation can decide it.")


if __name__ == "__main__":
    main()
