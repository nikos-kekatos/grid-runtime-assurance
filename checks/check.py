"""
Assert that every number chapter.tex quotes still matches the harnesses.

    python3 checks/extract.py     # after re-running the harnesses
    python3 checks/check.py       # fails loudly on drift

Timing figures are wall-clock and vary run to run; they are checked with a
tolerance and reported separately. Everything else must match exactly.
"""
import json, os, re, sys

H = os.path.dirname(os.path.abspath(__file__))
R = json.load(open(os.path.join(H, "results.json")))
TEX = open(os.environ.get("CHAPTER_TEX", os.path.join(H, "..", "chapter.tex"))).read()

fails, soft, checks = [], [], []


def want(label, value, *, pat=None, tol=None):
    """Assert the chapter states `value`. pat overrides how it is written."""
    if pat is None:
        # word-boundary match: a bare re.escape lets "154" match inside
        # \cite{ieee1547} and certify a retracted quantity by accident
        pat = r"(?<![\d.])" + re.escape(f"{value}") + r"(?![\d.])"
    hit = re.search(pat, TEX) is not None
    checks.append((label, value, hit))
    if not hit:
        (soft if tol else fails).append(f"{label}: harness={value} not found in chapter.tex")


# ---- composition necessity -------------------------------------------------
# the single headline figure was replaced by a penetration sweep; check the
# two endpoints the chapter quotes
want("no violation at half penetration", "0/500")
want("aggregate sweep: none at 92% penetration", "0/500")
want("aggregate sweep: 276% penetration", "56.6")
want("aggregate sweep: 331% penetration", "85.4")
want("aggregate sweep: 368% penetration", "94.2")

# ---- ablation table: deleted from the chapter (its rows are forced by
#      the category split, so it carried one datum, not seven)

# ---- topology-error sensitivity -------------------------------------------
for v in R["topo"]:
    # 100.0 is written "100\%" in prose; accept either spelling
    want("verdict agreement at a switch-error rate",
         f"{v}", pat=re.escape(f"{v}") + "|" + re.escape(f"{v:.0f}") + r"\\%")

# ---- reachability vs static list, and telemetry loss --------------------
# the reconfiguration comparison is retracted: one arm cannot fire and the
# reference detects switching rather than list staleness. Only the zero-dispatch
# control is quoted.
want("reconfiguration control: violations at zero dispatch", "343")
want("evidence: genuine violations, loss-invariant", "283")

for v in ("15.2", "39.9", "74.6", "99.3", "70.5", "95.4"):
    want(f"telemetry panel (a) {v}", v)
for v in ("31", "233", "118", "13.3", "11.1", "11.0"):
    want(f"switch-state panel (b) {v}", v)

# ---- power-flow validation -------------------------------------------------
pf = R["pf"]
want("33-bus generation-only false alarms", pf["case33bw"]["gen_fa"])
want("33-bus net-proxy recall", pf["case33bw"]["net_rec"])
want("33-bus voltage-only share", pf["case33bw"]["volt_only_pct"])
want("mv_oberrhein residual false alarms", pf["mv_oberrhein"]["net_fa"])
for nm in pf:
    if pf[nm].get("net_fa") == 0.0:
        want(f"{nm} net-proxy false alarms", "0.0")

# ---- voltage linearisation -------------------------------------------------
v33, vcig = R["v"]["case33bw"], R["v"]["CIGRE"]
want("33-bus band violations caught", f"{v33['caught']}$ of ${v33['real']}",
     pat=re.escape(f"${v33['caught']}$ of ${v33['real']}$"))
want("CIGRE band violations caught", f"{vcig['caught']} of {vcig['real']}",
     pat=re.escape(f"${vcig['caught']}$ of ${vcig['real']}$"))
want("33-bus over-refusal", v33["fa"])
want("CIGRE over-refusal", vcig["fa"])
want("clean agreement near the base point", v33["strat"]["near"][1])
want("clean agreement at twice the distance", v33["strat"]["mid"][1])
want("observed false negatives", v33["real"] - v33["caught"])

# ---- timings: wall-clock, so compare numerically within 10% ---------------
quoted_ms = [float(x) for x in re.findall(r"\$?([\d.]+)\$?\\,ms", TEX)]
for us in (R["cost_us"][0], R["cost_us"][-1]):
    ms = us / 1000.0
    near = [q for q in quoted_ms if abs(q - ms) <= 0.10 * max(ms, 0.1)]
    checks.append((f"evaluation cost ~{ms:.1f} ms", f"{ms:.1f}", bool(near)))
    if not near:
        soft.append(f"evaluation cost {ms:.2f} ms has no figure within 10% in chapter.tex "
                    f"(quoted: {sorted(quoted_ms)})")

# ---- report ----------------------------------------------------------------
w = max(len(c[0]) for c in checks)
for lab, val, ok in checks:
    print(f"{lab:<{w}}  {str(val):>12}  {'ok' if ok else 'MISSING'}")

print()
if soft:
    print(f"{len(soft)} timing figure(s) drifted (wall-clock, not fatal):")
    for f in soft:
        print("  " + f)
    print()
if fails:
    print(f"FAIL: {len(fails)} quoted value(s) no longer match the harness:")
    for f in fails:
        print("  " + f)
    sys.exit(1)
print(f"all {len(checks) - len(soft)} non-timing values in chapter.tex reproduce from the harnesses")
