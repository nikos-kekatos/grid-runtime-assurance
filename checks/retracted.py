"""
List every occurrence of a retracted quantity in chapter.tex.

check.py cannot catch these: a retracted figure is absent from the harness
transcripts, so it is simply never checked. chapter.tex is normalised (math
delimiters and thin spaces stripped) before searching, because 103/291 was
typeset as "$103$ ... of the $291$" and a literal grep missed it entirely.

retracted.txt format: one entry per line, "<pattern>  # <why>", where the
pattern may contain spaces.
"""
import os, re, sys

H = os.path.dirname(os.path.abspath(__file__))
raw = open(os.environ.get("CHAPTER_TEX", os.path.join(H, "..", "chapter.tex"))).read()
norm = raw.replace("$", "").replace("\\,", " ")
norm = re.sub(r"\s+", " ", norm)

hits = 0
for line in open(os.path.join(H, "retracted.txt")):
    line = line.strip()
    if not line or line.startswith("#"):
        continue
    pat, _, why = line.partition("#")
    pat = pat.strip()
    if not pat:
        continue
    n = norm.count(pat)
    if n:
        hits += 1
        print(f"  {n:>3} hit(s)  {pat!r:<28} {why.strip()}")
print("  (review each hit: legitimate only inside the passage that retracts it)"
      if hits else "  no retracted quantity appears in chapter.tex")
