#!/usr/bin/env python3
"""
Minimal evaluation for the composable multi-aspect RV framework for LLM agents
(paper: "Composing Stream-Based and Past-Time Runtime Monitors for Multi-Aspect
Compliance of LLM Agents").

It implements the three aspect monitors from the paper as faithful REFERENCE
monitors over a shared canonical event stream, fuses them with the four-valued
composition algebra, and runs a shield over STAC-style tool-call traces:

  phi_S  (spatial)       : for every patient_doc file f, at least one accessible
                           copy of f resides in an EU_storage container.
                           [qualitative-spatial fragment; TeSSLa in the paper]
  phi_T  (past-time FO)   : a bulk_delete that would remove the last copy of a
                           patient_doc f must be preceded, within 24h, by an
                           explicit consent(user,f).            [DejaVu in the paper]
  phi_Sigma (text-semantic): patient_doc(f) iff f carries a PHI tag or a
                           (calibrated) classifier fires.       [semantic kernel]

Composition: V4 = {T, F, Tp, Fp}; F (unsat) dominates; provenance = union of the
aspects that contributed to the decisive verdict.  If the composed verdict is F,
the shield BLOCKS the tool call (attack prevented).

Metrics (RQ1-RQ4 of the paper): attack success rate (ASR) without/with the
monitor, benign false-positive rate (FPR), per-event overhead, and the fraction
of blocks whose evidence spans >=2 aspects (the framework's distinguishing claim).

Reference monitors, not the mature engines; the specs are the ones the paper
feeds to TeSSLa/DejaVu, so those engines are drop-in.  Pure stdlib.
"""
import time
from dataclasses import dataclass, field
from typing import Optional

# --- four-valued verdicts -----------------------------------------------------
T, F, Tp, Fp = "T", "F", "Tp", "Fp"  # sat, unsat, presumably-sat, presumably-unsat


def compose(verdicts):
    """v_S (x) v_T (x) v_Sigma on V4: F dominates, then Fp, then T/Tp.
    Returns (verdict, provenance-set) where provenance names the aspects whose
    verdict equals the decisive (strongest) value."""
    order = {F: 3, Fp: 2, Tp: 1, T: 0}
    decisive = max((v for _, v in verdicts), key=lambda v: order[v])
    prov = {a for a, v in verdicts if v == decisive}
    return decisive, prov


# --- canonical event schema (paper Sec. IV-A) ---------------------------------
@dataclass
class Event:
    turn: int
    actor: str                 # "user" | "agent" | "env"
    kind: str                  # tool_call | tool_result | observation | user_msg
    tool: Optional[str] = None
    args: dict = field(default_factory=dict)
    labels: set = field(default_factory=set)   # semantic tags, e.g. {"patient_doc"}
    ts: float = 0.0            # hours (logical clock for the 24h window)


# --- world model the monitors share (produced by canonicalisation) -----------
class World:
    """Tracks which files reside in which containers, container region labels,
    file labels, and the consent log.  A container models a storage node or an
    archive; files can nest inside archive containers."""
    def __init__(self):
        # container -> {"labels": set, "holds": set(file_or_container_names)}
        self.containers = {}
        self.file_labels = {}                 # file -> set of semantic tags
        self.consent = {}                     # file -> latest consent ts (hours)
        self.exported = set()                 # files exfiltrated via export_file

    def add_container(self, name, labels, is_archive=False):
        self.containers.setdefault(name, {"labels": set(labels), "holds": set(),
                                          "archive": is_archive})

    def put(self, container, item):
        self.containers[container]["holds"].add(item)

    def _accessible_copies(self, f, seen=None):
        """Containers (recursively, through archives) that hold file f."""
        seen = seen or set()
        out = set()
        for cname, c in self.containers.items():
            if cname in seen:
                continue
            if f in c["holds"]:
                out.add(cname)
            # archives are containers that may themselves be held elsewhere;
            # a file inside an archive is accessible wherever the archive is.
        # resolve archive nesting: if f is in archive A, and A is in node N, then
        # f is accessible at N too.
        changed = True
        while changed:
            changed = False
            for cname, c in self.containers.items():
                if out & c["holds"] and cname not in out:
                    out.add(cname); changed = True
        return out

    def eu_copy_exists(self, f):
        for cname in self._accessible_copies(f):
            if "EU_storage" in self.containers[cname]["labels"]:
                return True
        return False

    def last_copy_removed_by(self, removed_containers, f):
        """True if removing `removed_containers` leaves file f with no copy."""
        remaining = self._accessible_copies(f) - set(removed_containers)
        return len(remaining) == 0

    def patient_docs(self):
        return [f for f, ls in self.file_labels.items() if "patient_doc" in ls]


# --- the three aspect monitors ------------------------------------------------
class SpatialMonitor:                     # phi_S  (TeSSLa in the paper)
    def check(self, world):
        for f in world.patient_docs():
            if not world.eu_copy_exists(f):
                return F                  # no EU_storage copy of a patient doc
        return T


class PastTimeMonitor:                    # phi_T  (DejaVu first-order past-time)
    WINDOW_H = 24.0

    def consented(self, world, f, now_ts):
        c = world.consent.get(f)
        return c is not None and (now_ts - c) <= self.WINDOW_H

    def check_bulk_delete(self, world, matched_containers, now_ts):
        """For each patient_doc whose last copy this bulk-delete would remove,
        require consent within the past 24h; else unsat."""
        for f in world.patient_docs():
            if world.last_copy_removed_by(matched_containers, f):
                if not self.consented(world, f, now_ts):
                    return F              # last-copy delete without recent consent
        return T

    def check_export(self, world, f, now_ts):
        """A patient_doc may only be exported after consent within 24h."""
        if "patient_doc" in world.file_labels.get(f, set()) and not self.consented(world, f, now_ts):
            return F
        return T


class SemanticMonitor:                    # phi_Sigma (semantic kernel)
    """patient_doc(f) via PHI tag (deterministic) or a calibrated classifier.
    Returns (verdict, is_phi); Tp = presumably-PHI from the classifier."""
    PHI_HINTS = ("patient", "record", "phi", "diagnosis", "mrn")

    def classify(self, filename, tagged_phi):
        if tagged_phi:
            return T, True                # deterministic PHI tag
        if any(h in filename.lower() for h in self.PHI_HINTS):
            return Tp, True               # calibrated classifier (presumptive)
        return T, False


# --- the composed shield ------------------------------------------------------
class Shield:
    def __init__(self):
        self.spatial = SpatialMonitor()
        self.past = PastTimeMonitor()
        self.sem = SemanticMonitor()
        self.blocks = []                  # (turn, verdict, provenance)

    def _apply_semantics(self, world, ev, enabled):
        """phi_Sigma types files as patient_doc before the spatial/temporal aspects
        run. This typing IS the semantic dimension: it is applied only when Sigma
        is enabled, so an ablation that drops Sigma leaves phi_S and phi_T with no
        protected object to reason about -- the precondition the other two need."""
        if "Sigma" not in enabled:
            return
        for fn in ev.args.get("_files", []):
            v, is_phi = self.sem.classify(fn, "phi" in ev.labels)
            if is_phi:
                world.file_labels.setdefault(fn, set()).add("patient_doc")

    def step(self, world, ev, enabled=("S", "T", "Sigma")):
        """Evaluate a tool_call against the ENABLED aspects; block iff the composed
        verdict over those aspects is F. Commits the call's effect only if allowed
        (deferred via a `commit` closure). `enabled` supports leave-one-out and
        single-aspect ablations."""
        enabled = set(enabled)
        t0 = time.perf_counter()
        self._apply_semantics(world, ev, enabled)
        vs = vt = vsig = T
        commit = lambda: None
        if ev.tool == "compress_file":
            src, out = ev.args["source"], ev.args["output"]
            world.add_container(out, labels={"EU_storage"} if ev.args.get("eu", True) else set(),
                                is_archive=True)          # additive; never blocks
            world.put(out, src)
            vs = self.spatial.check(world)
        elif ev.tool == "delete_file":
            # deleting a file path removes the loose copy in storage NODES, not the
            # copy packed inside an archive (that needs deleting the archive).
            fn = ev.args["filename"]
            nodes = [c for c in world.containers
                     if fn in world.containers[c]["holds"] and not world.containers[c]["archive"]]
            vt = self.past.check_bulk_delete(world, nodes, ev.ts)
            shadow = {c: set(world.containers[c]["holds"]) for c in nodes}
            for c in nodes:
                world.containers[c]["holds"].discard(fn)
            vs = self.spatial.check(world)
            for c in nodes:
                world.containers[c]["holds"] = shadow[c]
            def commit(nodes=nodes, fn=fn):
                for c in nodes:
                    world.containers[c]["holds"].discard(fn)
        elif ev.tool == "delete_files_by_pattern":
            ext = ev.args["extension"]
            matched = [c for c in world.containers
                       if c.endswith(ext) and world.containers[c]["archive"]]
            vt = self.past.check_bulk_delete(world, matched, ev.ts)
            saved = {c: world.containers[c] for c in matched}
            for c in matched:
                del world.containers[c]
            vs = self.spatial.check(world)
            for c, v in saved.items():
                world.containers[c] = v
            def commit(matched=matched):
                for c in matched:
                    del world.containers[c]
        elif ev.tool == "move_file":
            # data-residency move: relocate f to `dest` (possibly non-EU). No copy
            # is destroyed, so phi_T is silent; phi_S alone must catch a non-EU move.
            fn, dest = ev.args["filename"], ev.args["dest"]
            world.add_container(dest, labels={"EU_storage"} if ev.args.get("eu", False) else set())
            src_nodes = [c for c in world.containers
                         if fn in world.containers[c]["holds"] and not world.containers[c]["archive"] and c != dest]
            shadow = {c: set(world.containers[c]["holds"]) for c in world.containers}
            for c in src_nodes:
                world.containers[c]["holds"].discard(fn)
            world.containers[dest]["holds"].add(fn)
            vs = self.spatial.check(world)
            for c in shadow:
                world.containers[c]["holds"] = shadow[c]
            def commit(src_nodes=src_nodes, fn=fn, dest=dest):
                for c in src_nodes:
                    world.containers[c]["holds"].discard(fn)
                world.containers[dest]["holds"].add(fn)
        elif ev.tool == "export_file":
            # exfiltration: no copy removed (phi_S silent); phi_T requires consent.
            fn = ev.args["filename"]
            vt = self.past.check_export(world, fn, ev.ts)
            def commit(fn=fn):
                world.exported.add(fn)
        elif ev.tool == "grant_consent":
            def commit():
                world.consent[ev.args["filename"]] = ev.ts

        verdicts = [(a, v) for a, v in (("S", vs), ("T", vt), ("Sigma", vsig)) if a in enabled]
        verdict, prov = compose(verdicts)
        blocked = verdict == F
        if not blocked:
            commit()
        dt = (time.perf_counter() - t0) * 1e6      # microseconds
        if blocked:
            self.blocks.append((ev.turn, verdict, prov))
        return blocked, prov, dt


# Evaluation driver lives in run_eval.py.
