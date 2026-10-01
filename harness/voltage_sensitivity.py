#!/usr/bin/env python3
"""Close the voltage gap: can a linear model see what a real-power model cannot?

Our spatial clause reasons about real power, and 21.2% of scenarios on the IEEE
33-bus feeder breach the statutory voltage band with no thermal violation at all.
No PTDF, and no connectivity computation, can decide any of them. The question is
whether that requires a full AC solve or whether a linearisation suffices.

METHOD.  Voltage sensitivities come from the power-flow Jacobian at an operating
point. In polar form the Newton-Raphson Jacobian relates injections to state,

    [dP; dQ] = J [dtheta; d|V|] ,

so inverting it gives the sensitivity of every bus voltage magnitude to every bus
injection. Predicting a scenario is then one matrix product,

    |V| ~= |V_base| + S_VP (P - P_base) + S_VQ (Q - Q_base) ,

which is the same computational shape as the PTDF clause: a matrix built once per
topology, reused per evaluation, no solve in the loop.

WHAT IS MEASURED.  Whether the linearised prediction agrees with the AC solution
on the only question the monitor asks, namely whether any bus leaves the band,
and how that agreement decays as dispatch moves away from the point the Jacobian
was taken at. A monitor is allowed to be approximate; it is not allowed to be
approximate without knowing by how much.
"""
import argparse
import warnings

warnings.filterwarnings("ignore")
import numpy as np                            # noqa: E402
import pandapower as pp                       # noqa: E402
import pandapower.networks as nw              # noqa: E402
import random                                 # noqa: E402
from scipy.sparse.linalg import splu          # noqa: E402
from scipy.sparse import csc_matrix           # noqa: E402

V_MIN, V_MAX = 0.95, 1.05

NETWORKS = {
    "case33bw (IEEE 33-bus)": nw.case33bw,
    "CIGRE MV benchmark": lambda: nw.create_cigre_network_mv(with_der="all"),
}


def sensitivities(net):
    """S_VP and S_VQ at the current operating point, plus the base voltages.

    The Jacobian is ordered [pv, pq] for angle and [pq] for magnitude. We invert
    it once and take the block giving d|V| against dP and dQ at PQ buses, which
    is where load and DER injections act."""
    pp.runpp(net, numba=False)
    it = net["_ppc"]["internal"]
    J = csc_matrix(it["J"])
    pv, pq = np.asarray(it["pv"], int), np.asarray(it["pq"], int)
    npv, npq = len(pv), len(pq)
    lu = splu(J)
    # columns of J^-1 corresponding to dP at pq buses, and to dQ at pq buses
    n = J.shape[0]
    S_vp = np.zeros((npq, npq))
    S_vq = np.zeros((npq, npq))
    for k in range(npq):
        e = np.zeros(n); e[npv + k] = 1.0          # unit dP at pq bus k
        S_vp[:, k] = lu.solve(e)[npv + npq:]
        e = np.zeros(n); e[npv + npq + k] = 1.0    # unit dQ at pq bus k
        S_vq[:, k] = lu.solve(e)[npv + npq:]
    vbase = np.abs(it["V"])[pq]
    return S_vp, S_vq, vbase, pq


def ppc_bus_of(net):
    lk = net["_pd2ppc_lookups"]["bus"]
    return {b: lk[b] for b in net.bus.index}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenarios", type=int, default=300)
    ap.add_argument("--seed", type=int, default=13)
    a = ap.parse_args()
    print("=" * 78)
    print("VOLTAGE: LINEARISED SENSITIVITIES vs AC SOLUTION")
    print("=" * 78)
    print(f"band [{V_MIN}, {V_MAX}] pu; sensitivities taken once at the base point")

    for name, factory in NETWORKS.items():
        net = factory()
        rng = random.Random(a.seed)
        # site DERs
        buses = list(net.load.bus.unique()); rng.shuffle(buses)
        for b in buses[: max(1, len(buses) // 2)]:
            pp.create_sgen(net, b, p_mw=0.0, name=f"der_{b}")

        S_vp, S_vq, vbase, pq = sensitivities(net)
        b2p = ppc_bus_of(net)
        pq_pos = {int(p): i for i, p in enumerate(pq)}
        base_p = {b: 0.0 for b in net.bus.index}
        for _i, r in net.load.iterrows():
            base_p[r.bus] = base_p.get(r.bus, 0.0) - float(r.p_mw)
        baseMVA = float(net["_ppc"]["baseMVA"])

        tp = fn = fp = tn = 0
        by_stress = {}
        total = float(net.load.p_mw.sum())
        for _s in range(a.scenarios):
            stress = rng.uniform(0.1, 3.0)
            for i, b in zip(net.sgen.index, net.sgen.bus):
                net.sgen.at[i, "p_mw"] = max(
                    0.0, rng.gauss(stress * total / max(1, len(net.sgen)), 0.2))
            try:
                pp.runpp(net, numba=False)
            except Exception:
                continue
            truth = bool((net.res_bus.vm_pu < V_MIN).any()
                         or (net.res_bus.vm_pu > V_MAX).any())

            # linear prediction from the base-point sensitivities
            dP = np.zeros(len(pq))
            gen = net.sgen.groupby("bus").p_mw.sum().to_dict()
            for b in net.bus.index:
                pos = pq_pos.get(b2p[b])
                if pos is None:
                    continue
                p_now = gen.get(b, 0.0) - float(
                    net.load[net.load.bus == b].p_mw.sum())
                dP[pos] = (p_now - base_p.get(b, 0.0)) / baseMVA
            vpred = vbase + S_vp.dot(dP)
            pred = bool((vpred < V_MIN).any() or (vpred > V_MAX).any())

            tp += truth and pred; fn += truth and not pred
            fp += (not truth) and pred; tn += (not truth) and (not pred)
            # Stratify by whether a violation EXISTS. An unstratified accuracy at
            # high stress merely reports the base rate: when almost every
            # scenario violates, predicting "violation" is trivially right.
            band = "near" if stress <= 1.0 else ("mid" if stress <= 2.0 else "far")
            d = by_stress.setdefault(band, {"viol": [0, 0], "clean": [0, 0]})
            key = "viol" if truth else "clean"
            d[key][0] += 1
            d[key][1] += (truth == pred)

        pos_, neg_ = tp + fn, fp + tn
        print(f"\n{name}   buses {len(net.bus)}  DERs {len(net.sgen)}")
        print(f"   voltage violations {pos_} of {pos_ + neg_}")
        if pos_:
            print(f"   recall      {100*tp/pos_:5.1f}%   "
                  f"(catches {tp} of {pos_} real band violations)")
        if neg_:
            print(f"   false alarm {100*fp/neg_:5.1f}%   "
                  f"({fp} of {neg_} clean scenarios refused)")
        print("   stratified agreement (violating / clean scenarios separately):")
        for band in ("near", "mid", "far"):
            if band not in by_stress:
                continue
            d = by_stress[band]
            lab = "<=1x" if band == "near" else ("<=2x" if band == "mid" else ">2x")
            parts = []
            for key in ("viol", "clean"):
                n_, ok = d[key]
                parts.append(f"{key} {100*ok/n_:5.1f}% (n={n_})" if n_
                             else f"{key}   n/a (n=0)")
            print(f"      {band:<5} {lab:<5}  " + "   ".join(parts))

        # --- the linear model as a FILTER on an expensive check ----------------
        # A presumptive verdict is only useful if something can adjudicate it.
        # Here the AC solve is the adjudicator, and the question is how often it
        # has to be paid for: the linear model refuses nothing on its own, it
        # decides when the solve is needed.
        solves = pos_ + fp          # every predicted violation triggers one
        missed = fn                 # violations the filter never escalates
        print(f"   as a filter on an AC solve: "
              f"{solves} solves for {pos_ + neg_} scenarios "
              f"({100*solves/max(1,pos_+neg_):.0f}%), "
              f"{missed} violation(s) never escalated")

    print("\nThe sensitivity matrix is built once per topology and reused, so this")
    print("is the same computational shape as the PTDF clause: a matrix product")
    print("per evaluation, no solve in the loop.")


if __name__ == "__main__":
    main()
