"""Pull the numbers the chapter quotes out of the three harness transcripts."""
import re, json, sys, os

H = os.path.dirname(os.path.abspath(__file__))
IN = sys.argv[1] if len(sys.argv) > 1 else H
txt = {n: open(os.path.join(IN, n + ".out")).read()
       for n in ("energy", "powerflow", "voltage")}

R = {}

# ---- energy: composition necessity, ablation, topology error, scaling ------
# penetration sweep replaces the single composition-necessity figure
R["sweep"] = [(int(a), int(b), float(c))
              for a, b, c in re.findall(
                  r"^\s+(\d+)\s+\d+\s+\d+%\s+(\d+)/\d+\s+([\d.]+)%",
                  txt["energy"], re.M)]

for key, label in [("abl_S", "phi_S only"), ("abl_T", "phi_T only"),
                   ("abl_Sig", "phi_Sigma only"), ("abl_ST", "S x T"),
                   ("abl_SSig", "S x Sigma"), ("abl_TSig", "T x Sigma"),
                   ("abl_all", "all three")]:
    m = re.search(re.escape(label) + r"\s+(\d+)/(\d+)\s+(\d+)/(\d+)", txt["energy"])
    R[key] = int(m[1]); R[key + "_den"] = int(m[2]); R[key + "_fp"] = int(m[3])

R["topo"] = [float(x) for x in
             re.findall(r"^\s+0\.\d\d\s+([\d.]+)%", txt["energy"], re.M)]
R["cost_us"] = [float(m[1]) for m in
                re.finditer(r"^\s+\d+\s+\d+\s+([\d.]+)\s+[\d.]+\s*$",
                            txt["energy"], re.M)]

# ---- powerflow: per-network false alarms / recall / voltage-only ----------
R["pf"] = {}
for blk in re.split(r"\n(?=\S.*buses\s+\d+)", txt["powerflow"]):
    nm = re.match(r"(\S[^ ]*)", blk)
    if not nm or "buses" not in blk:
        continue
    name = nm[1]
    d = {}
    for tag, pat in [("gen_fa", r"generation-only proxy.*?false alarms\s+([\d.]+)%"),
                     ("net_fa", r"net \(load - gen\) proxy.*?false alarms\s+([\d.]+)%"),
                     ("net_rec", r"net \(load - gen\) proxy\s+thermal recall\s+([\d.]+)%")]:
        mm = re.search(pat, blk, re.S)
        if mm:
            d[tag] = float(mm[1])
    mm = re.search(r"invisible to any connectivity proxy: (\d+) \(([\d.]+)%", blk)
    if mm:
        d["volt_only_n"], d["volt_only_pct"] = int(mm[1]), float(mm[2])
    R["pf"][name] = d

# ---- voltage: linearisation recall / over-refusal / stratification --------
R["v"] = {}
for blk in re.split(r"\n(?=\S.*buses\s+\d+)", txt["voltage"]):
    nm = re.match(r"(\S[^ ]*)", blk)
    if not nm or "recall" not in blk:
        continue
    d = {}
    mm = re.search(r"recall\s+([\d.]+)%\s+\(catches (\d+) of (\d+)", blk)
    d["recall"], d["caught"], d["real"] = float(mm[1]), int(mm[2]), int(mm[3])
    mm = re.search(r"false alarm\s+([\d.]+)%\s+\((\d+) of (\d+)", blk)
    d["fa"], d["refused"], d["clean"] = float(mm[1]), int(mm[2]), int(mm[3])
    st = re.findall(r"(near|mid|far)\s+<?=?[^ ]*\s+viol\s+([\d.]+)%.*?clean\s+([\d.n/a]+)", blk)
    d["strat"] = {a: (float(b), (float(c) if c not in ("n/a",) else None)) for a, b, c in st}
    R["v"][nm[1]] = d

OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(IN, "results.json")
json.dump(R, open(OUT, "w"), indent=2)
print(json.dumps(R, indent=2))
