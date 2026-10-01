# Runtime assurance for IoT-instrumented distribution grids

Code and harnesses for the book chapter

> N. Kekatos, S. Basagiannis, P. Katsaros, A. Lekidis, S. Petridou.
> *From Measurement to Enforcement in IoT-Instrumented Distribution Grids.*
> AIoT-RSE 2026, Track 6 (book chapter 8).

Every number quoted in the chapter is produced by the scripts in `harness/`.
`make results` re-runs them, and `make verify` compares the fresh run with the
reference transcripts in `checks/reference/`, failing on any mismatch.

## Quick start

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
make results     # about 25 minutes
make verify      # "all non-timing results match the reference"
```

Wall-clock timings (the runtime-cost figures) vary between machines; `verify.py`
reports them but does not compare them.

## What reproduces what

| Chapter result | Script | Transcript |
|---|---|---|
| Aggregate violations versus DER penetration (0/500 at 92%, 56.6% at 276%, …) | `harness/run_energy_experiments.py` (uses `aggregate_sweep.py`) | `energy.out` |
| Evidence loss: telemetry and switch-state panels | `harness/run_energy_experiments.py` (uses `evidence_loss.py`) | `energy.out` |
| Informational reachability (204/204) and cohort rule (211/211) | `harness/run_energy_experiments.py` | `energy.out` |
| Model sensitivity and runtime cost | `harness/run_energy_experiments.py` | `energy.out` |
| Validation against power flow (IEEE 33-bus, CIGRE MV, mv_oberrhein) | `harness/powerflow_validation.py` | `powerflow.out` |
| Voltage linearisation (surrogate verdicts) | `harness/voltage_sensitivity.py` | `voltage.out` |
| Nine clauses against five monitoring engines | `harness/sota_comparison.py` | `harness/sota/RESULTS.txt` |

The feeder model, the two-sorted location graph and the verdict algebra are in
`harness/energy_rv.py` and `harness/feeder_topology.py`; the shared verdict
algebra and location graph live in `vendor/`.

## The five-engine comparison (`make engines`)

This part needs external tools that are not bundled:

| Engine | How it is run | Setting |
|---|---|---|
| MonPoly | Docker image `infsec/monpoly:latest` | `MONPOLY_IMAGE` |
| TeSSLa | a Docker image with the TeSSLa interpreter | `TESSLA_IMAGE` |
| DejaVu | `dejavu.jar` (download from the DejaVu project) | `DEJAVU_JAR` (default `tools/dejavu.jar`) |
| MoonLight | Python package `moonlight` (needs Java 21) | `JAVA_HOME_21`, `JAVA21_BIN` |
| RTAMT | Python package `rtamt` | – |

The specifications for every engine are in `harness/sota/` (`*.mfotl`,
`*.tessla`, `*.qtl`, `*.mls`) together with the transcripts of the reference run.

## Layout

```
harness/            experiment scripts and engine specifications
vendor/             verdict algebra and location graph shared with related work
checks/extract.py   pulls the quoted numbers out of the transcripts
checks/reference/   transcripts and extracted numbers of the reference run
verify.py           compares a fresh run with the reference
```

`checks/check.py` and `checks/retracted.py` are the authors' consistency checks
against the chapter source; they need the chapter text (`CHAPTER_TEX=path`), which
is not distributed here.

## Scope of the experiments

Segment ratings and dispatch profiles are synthetic and deliberately
stress-inducing; the proportions describe that distribution, not operational
frequencies. All experiments are seeded.

## Licence

MIT; see `LICENSE`. If you use this code, please cite the chapter (`CITATION.cff`).
