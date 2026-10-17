"""Deterministic rule engine: CV timeline proposes moments, probes answer narrow yes/no cues, rules
turn (timeline, probe answers) into typed events.

Binding design (plan Task 3/4): every probe is ONE narrow cue on 1-3 keyframes; door open = CV cue
only; no holistic VLM classification; п.42 is never a trigger. Each rule has
`propose(ctx) -> [ProbeRequest]` and `decide(ctx, [(request, result)]) -> [Event]`.
Module-level `propose()` / `decide()` run all rules (the fixed Task 3/4 interface); `plan()` gives the
per-rule request groups so the analyzer can run them stage-wise (deadline, streaming events).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace

from forte_detector.probes.base import ProbeRequest, ProbeResult
from forte_detector.rules import taxonomy as tx
from forte_detector.schema import Event

# Cues answered by cheap CV (not counted against the VLM budget).
CV_CUES = {"door_open", "camera_blocked"}
# COCO object classes (the runner's fallback when the VLM is down) are noisy on these overhead views
# (desk phone -> "cell phone", jackets -> "handbag"): alone they stay below the verdict threshold.
COCO_ONLY_WEIGHT = 0.25
# Recall-first policy (organizer clarification, docs/decisions.md): a cue that is present but weak
# (partial corroboration, single frame, weak-cue-only) still EMITS the violation with this fact_conf
# (x the probe's own confidence); the verdict threshold sits below it.
WEAK_FACT = 0.45


@dataclass(frozen=True)
class RulesConfig:
    verdict_threshold: float = float(os.environ.get("FORTE_VERDICT_THR", "0.25"))
    max_vlm_requests: int = int(os.environ.get("FORTE_MAX_VLM_REQ", "50"))  # first-round plan
    max_vlm_total: int = 60  # hard cap per video incl. follow-ups (CV cues do not count)
    max_events: int = 3
    merge_gap_s: float = 5.0
    min_probe_conf: float = 0.5  # a True answer below this confidence is ignored
    departure_window_s: float = 14.0  # seat-leave this long before the exit belongs to the departure
    client_min_dwell_s: float = 5.0
    visit_min_dwell_s: float = 3.0
    short_visit_max_s: float = 15.0
    client_change_max_gap_s: float = 30.0
    max_client_changes: int = 3
    max_short_visits: int = 2
    max_departures: int = 3  # candidate departures (+ the end of the clip) confirmed by person count
    enable_service: bool = True  # ServiceRule (T10 cash at the body + T15 client ID check)
    t15_standalone: bool = True  # T15 without a T10 cue (team decision: missing ID is a violation)


# ----------------------------------------------------------------------------- context

@dataclass
class Visit:
    track_id: int
    start: float
    end: float
    enters: bool  # born inside the video (not at the very start)
    exits: bool  # dies inside the video
    at_desk_t: float | None = None

    @property
    def dwell(self) -> float:
        return self.end - self.start


@dataclass
class Departure:
    """Candidate "cashier gone" moment; confirmed by a VLM person count == 0 at t_probe."""

    t_exit: float  # cashier crosses out / room becomes empty (best estimate)
    t_leave: float | None  # cashier leaves the seat (start of the interval), if seen
    source: str  # "seat" (leave episode) | "exit" | "empty" | "end" (end of clip)
    t_probe: float = 0.0

    @property
    def timecode(self) -> float:
        """Middle of the departure interval [leaves the workplace, crosses the door]. Labels mark
        either end of it (stands up with the item left / crosses the door); the middle keeps both
        conventions within a few seconds."""
        if self.t_leave is None or self.t_leave >= self.t_exit:
            return self.t_exit
        return round((self.t_leave + self.t_exit) / 2, 2)


TRUNCATION_CUT_S = 3.0  # exits / room-empty starts this close to a truncation cut are not departures


class Ctx:
    """Derived facts over the perception timeline (+ calibration)."""

    def __init__(self, timeline: dict, calibration: dict | None = None, cfg: RulesConfig | None = None,
                 staged: bool = False):
        self.staged = staged  # True: rules may ask second-round requests via Rule.followup()
        self.tl = timeline
        self.cal = calibration or {}
        self.cfg = cfg or RulesConfig()
        self.duration = float(timeline.get("duration") or (timeline.get("series", {}).get("t") or [0])[-1])
        # perception stopped early (deadline / cancel): `duration` is the cut, not the end of the video
        self.truncated = bool((timeline.get("meta") or {}).get("truncated"))
        self.cps = timeline.get("checkpoints", [])
        self.cashier_ids = set(timeline.get("roles", {}).get("cashier_tracks", []))
        self.visits = self._visits()
        self.departures = self._departures()

    # -- helpers
    def clip(self, t: float) -> float:
        return round(min(max(0.0, t), max(0.0, self.duration - 0.25)), 2)

    def cp(self, kind: str) -> list[dict]:
        return [c for c in self.cps if c["type"] == kind]

    def frac(self, f: float) -> float:
        return self.clip(f * self.duration)

    def seat_arrivals(self) -> list[float]:
        return sorted(c["t"] for c in self.cps if c["type"] in ("cashier_arrives_seat", "cashier_returns_seat"))

    def _visits(self) -> list[Visit]:
        enters = {i for c in self.cp("person_enters") for i in c["track_ids"]}
        exits = {i for c in self.cp("person_exits") for i in c["track_ids"]}
        desk = {}
        for c in self.cp("visitor_arrives_at_desk"):
            for i in c["track_ids"]:
                desk.setdefault(i, c["t"])
        out = []
        for tr in self.tl.get("tracks", []):
            if tr["id"] in self.cashier_ids or not tr.get("t"):
                continue
            out.append(Visit(tr["id"], float(tr["t"][0]), float(tr["t"][-1]), tr["id"] in enters,
                             tr["id"] in exits, desk.get(tr["id"])))
        return sorted(out, key=lambda v: v.start)

    def _departures(self) -> list[Departure]:
        """Candidates: seat-leave episodes that do not end soon (probe up to 6 s later, before any
        return), perception exits / room-empty starts (+1 s), and the end of the clip. Perception
        exits fire late or never (people behind the glass stay tracked), so every candidate is
        confirmed by one VLM person count; candidates with probes < 4 s apart are merged."""
        cfg = self.cfg
        leaves = sorted(c["t"] for c in self.cp("cashier_leaves_seat"))
        rets = sorted(c["t"] for c in self.cps if c["type"] in ("cashier_returns_seat", "cashier_arrives_seat"))
        cands: list[Departure] = []
        for tl_ in leaves:
            nxt = [r for r in rets if r > tl_]
            if nxt and nxt[0] - tl_ < 2.0:
                continue  # stood up / turned to the safe and sat down again
            if self.truncated and not nxt and tl_ + 6.0 > self.duration - 0.5:
                continue  # left just before a truncation cut: whether she came back is unknown
            tp = min(tl_ + 6.0, (nxt[0] - 0.5) if nxt else self.duration)
            # door-crossing estimate: the farthest-from-seat point of this episode, else leave + 3 s
            far = [c["t"] for c in self.cp("cashier_farthest_from_seat") if tl_ < c["t"] <= tp]
            cands.append(Departure(far[-1] if far else min(tl_ + 3.0, tp), tl_, "seat", self.clip(tp)))
        for kind, src in (("cashier_exits_room", "exit"), ("room_empty_start", "empty")):
            for c in self.cp(kind):
                if c["t"] <= 1.0:
                    continue
                if self.truncated and c["t"] >= self.duration - TRUNCATION_CUT_S:
                    continue  # at the cut: nothing after it was seen, so it is not an absence
                w = [x for x in leaves if c["t"] - cfg.departure_window_s <= x <= c["t"] + 0.5]
                cands.append(Departure(c["t"], w[-1] if w else None, src, self.clip(c["t"] + 1.0)))
        cands.sort(key=lambda d: d.t_probe)
        merged: list[Departure] = []
        for d in cands:
            if merged and d.t_probe - merged[-1].t_probe < 4.0:
                m = merged[-1]
                leave = max([x for x in (m.t_leave, d.t_leave) if x is not None], default=None)
                merged[-1] = Departure(max(m.t_exit, d.t_exit), leave, m.source if m.source != "seat" else d.source,
                                       max(m.t_probe, d.t_probe))
            else:
                merged.append(d)
        merged = merged[-cfg.max_departures:]
        t_end = self.clip(self.duration - 0.5)
        cashier_seen = bool(self.cashier_ids) or any(self.tl.get("series", {}).get("cashier_present") or [])
        if self.truncated:  # the cut is not the end of the clip: no end-of-clip absence candidate
            return merged
        if cashier_seen and (not merged or t_end - merged[-1].t_probe >= 4.0):
            merged.append(Departure(t_end, None, "end", t_end))
        return merged


def expand(box, k: float):
    x0, y0, x1, y1 = box
    cx, cy, w, h = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) * k, (y1 - y0) * k
    return (max(0.0, cx - w / 2), max(0.0, cy - h / 2), min(1.0, cx + w / 2), min(1.0, cy + h / 2))


# ----------------------------------------------------------------------------- event helpers

def make_event(type_id: str, t: float, fact_conf: float, obs: str, *, start: float | None = None,
               end: float | None = None, evidence: list[float] | None = None, source: str = "",
               extra: dict | None = None) -> Event:
    legal = tx.legal_conf(type_id)
    fact = round(max(0.0, min(1.0, fact_conf)), 3)
    tc = round(float(t), 2)
    others = sorted({round(float(x), 2) for x in (evidence or []) if x is not None} - {tc})
    ev = [tc] + others[:2]  # evidence_ts[0] is always the timecode frame
    start_v = min(tc, float(start)) if start is not None else tc
    end_v = max(tc, float(end)) if end is not None else tc
    return Event(
        type=type_id, timecode_sec=tc, confidence=round(fact * tx.LEGAL_WEIGHT[legal], 3),
        description_ru=tx.describe(type_id, t, obs), evidence_ts=ev, source=source, extra=dict(extra or {}),
        title_ru=tx.title(type_id), start_sec=round(start_v, 2), end_sec=round(end_v, 2),
        fact_conf=fact, legal_conf=legal, obs=obs,
    )


def is_true(res: ProbeResult | None, min_conf: float = 0.5) -> bool:
    return res is not None and res.value is True and (res.conf or 0.0) >= min_conf


def count_of(res: ProbeResult | None) -> int | None:
    if res is None or res.value is None:
        return None
    c = (res.raw or {}).get("count")
    if isinstance(c, (int, float)):
        return int(c)
    return None


def req(rule: str, cue: str, t: float, key: str = "", roi=None, extra_ts=None, **context) -> ProbeRequest:
    return ProbeRequest(cue=cue, t=round(float(t), 2), roi=tuple(roi) if roi else None,
                        extra_ts=[round(float(x), 2) for x in (extra_ts or [])],
                        context={"rule": rule, "key": key, **context})


def by_key(pairs) -> dict[str, tuple[ProbeRequest, ProbeResult]]:
    return {p[0].context.get("key", ""): p for p in pairs}


def mm(t: float) -> str:
    return tx.mmss(t)


def people(n: int) -> str:
    """'1 человек', '2 человека', '5 человек' (Russian plural)."""
    n = int(n)
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{n} человека"
    return f"{n} человек"


# ----------------------------------------------------------------------------- rules

class Rule:
    name = "rule"
    types: tuple[str, ...] = ()

    def propose(self, ctx: Ctx) -> list[ProbeRequest]:
        return []

    def decide(self, ctx: Ctx, pairs: list[tuple[ProbeRequest, ProbeResult]]) -> list[Event]:
        return []

    def followup(self, ctx: Ctx, pairs: list[tuple[ProbeRequest, ProbeResult]]) -> list[ProbeRequest]:
        """Second-round requests that depend on first-round answers (staged mode only)."""
        return []


DEPARTURE_CUES = (  # cue, type, obs, ROI zone names
    ("cash_on_desk", "T1", "видны банкноты, помещение пустое с {since}", ("desk",)),
    ("bag_on_desk", "T1", "видна сумка, помещение пустое с {since}", ("desk",)),
    ("stamps_or_journal_on_desk", "T1b", "помещение пустое с {since}", ("desk",)),
    ("screen_on_unlocked", "T4", "экран не заблокирован, помещение пустое с {since}",
     ("laptop", "screen", "monitor")),
    ("key_in_safe", "T3", "ключ виден в замке, помещение пустое с {since}", ("safe",)),
    ("safe_open", "T3", "сейф не закрыт, помещение пустое с {since}", ("safe",)),
)


class DepartureRule(Rule):
    """Cashier gone -> what state is left behind (T1, T1b, T3, T4).

    Each candidate departure (see Ctx._departures) gets one VLM person count; only when the room
    is empty (count == 0) are the six desk/safe/screen cues asked at that moment (staged mode:
    follow-up round; one-shot mode: asked up front). For the end-of-clip candidate, 3 earlier
    counts find since when the room is empty (the event time).
    """

    name = "departure"
    types = ("T1", "T1b", "T3", "T4")
    BACKTRACK = (2.5, 5.0, 7.5)

    DESK_CUES = ("cash_on_desk", "bag_on_desk", "stamps_or_journal_on_desk")

    def _cues(self, ctx: Ctx, i: int, d: Departure) -> list[ProbeRequest]:
        out = [req(self.name, cue, d.t_probe, f"d{i}:{cue}") for cue, _, _, _ in DEPARTURE_CUES]
        # "desk not cleared": the desk cues get a second look a little later (small items, glare)
        t2 = ctx.clip(d.t_probe + 2.5)
        if t2 - d.t_probe >= 1.0:
            out += [req(self.name, cue, t2, f"d{i}:{cue}:2") for cue in self.DESK_CUES]
        if d.source == "end":
            out += [req(self.name, "person_count", d.t_probe - b, f"d{i}:count-{b}")
                    for b in self.BACKTRACK if d.t_probe - b > 0.5]
        return out

    def propose(self, ctx: Ctx) -> list[ProbeRequest]:
        out = []
        for i, d in enumerate(ctx.departures):
            out.append(req(self.name, "person_count", d.t_probe, f"d{i}:count"))
            if not ctx.staged:
                out += self._cues(ctx, i, d)
        return out

    def followup(self, ctx: Ctx, pairs) -> list[ProbeRequest]:
        if not ctx.staged:
            return []
        k = by_key(pairs)
        out = []
        for i, d in enumerate(ctx.departures):
            p = k.get(f"d{i}:count")
            if p and count_of(p[1]) == 0:
                out += self._cues(ctx, i, d)
        return out

    def decide(self, ctx: Ctx, pairs) -> list[Event]:
        k = by_key(pairs)
        events = []
        base_by_src = {"exit": 0.9, "empty": 0.88, "seat": 0.85, "end": 0.8}
        for i, d in enumerate(ctx.departures):
            p = k.get(f"d{i}:count")
            if not p or count_of(p[1]) != 0:
                continue
            if d.source == "end":
                since = d.t_probe
                for b in self.BACKTRACK:
                    q = k.get(f"d{i}:count-{b}")
                    if q is None or count_of(q[1]) != 0:
                        break
                    since = q[0].t
                leaves = [c["t"] for c in ctx.cp("cashier_leaves_seat") if since - 8 <= c["t"] <= since + 0.5]
                d = Departure(since, leaves[-1] if leaves else None, "end", d.t_probe)
            base = base_by_src.get(d.source, 0.8) * min(1.0, p[1].conf + 0.1)
            for cue, type_id, obs, _ in DEPARTURE_CUES:
                looks = [k.get(f"d{i}:{cue}"), k.get(f"d{i}:{cue}:2")]
                looks = [x for x in looks if x is not None and is_true(x[1], ctx.cfg.min_probe_conf)]
                if not looks:
                    continue
                q = looks[0]
                t = d.timecode
                events.append(make_event(
                    type_id, t, base * q[1].conf, obs.format(since=mm(d.t_exit)), start=d.t_leave if d.t_leave is not None else t, end=q[0].t,
                    evidence=[d.timecode, d.t_exit, q[0].t], source=f"rule:departure/{d.source}:{cue}",
                    extra={"cue": cue, "probe_t": q[0].t, "departure": d.source},
                ))
        return events


class PersistentStateRule(Rule):
    """A wrong state that persists over the second half of the clip: 3 tail probes of one cue
    (at 50/75/~100 % of the clip by default); fires when >= need of them are True."""

    def __init__(self, name, cue, type_id, obs, zones=(), fracs=(0.5, 0.75, 1.0), need=3, base=0.85,
                 require_cashier=False, min_conf: float | None = None, weak_need: int | None = None,
                 weak_check=None):
        self.min_conf = min_conf
        self.weak_check = weak_check  # optional (ctx, request) -> bool that every weak hit must pass
        self.weak_need = weak_need  # fewer hits than `need` but >= weak_need -> weak event (WEAK_FACT)
        self.name, self.cue, self.type_id, self.obs = name, cue, type_id, obs
        self.zones, self.fracs, self.need, self.base = zones, fracs, need, base
        self.types = (type_id,)
        self.require_cashier = require_cashier

    def times(self, ctx: Ctx) -> list[float]:
        return sorted({ctx.clip(min(f * ctx.duration, ctx.duration - 0.5)) for f in self.fracs})

    def propose(self, ctx):
        return [req(self.name, self.cue, t, f"{self.name}:{i}") for i, t in enumerate(self.times(ctx))]

    def decide(self, ctx, pairs):
        hits = [(q, r) for q, r in pairs if is_true(r, self.min_conf or ctx.cfg.min_probe_conf)]
        planned = len(self.times(ctx))
        need = min(self.need, planned)  # missing (capped) probes never lower the bar
        if not hits:
            return []
        if len(hits) >= need:
            conf = self.base * min(r.conf for _, r in hits) * (len(hits) / max(1, planned)) ** 0.5
            strength = "strong"
        elif (self.weak_need is not None and len(hits) >= self.weak_need
              and (self.weak_check is None or all(self.weak_check(ctx, q) for q, _ in hits))):
            conf = WEAK_FACT * min(r.conf for _, r in hits)
            strength = "weak"
        else:
            return []
        t0 = hits[0][0].t
        if all(r.source == "coco" for _, r in hits):  # COCO-only fallback (VLM down): weak evidence
            conf *= COCO_ONLY_WEIGHT
        seen = ", ".join(mm(q.t) for q, _ in hits)
        return [make_event(self.type_id, t0, conf, self.obs.format(times=seen, n=len(hits)), start=t0,
                           end=hits[-1][0].t,
                           evidence=[q.t for q, _ in hits], source=f"rule:{self.name}:{self.cue}",
                           extra={"cue": self.cue, "hits": len(hits), "probes": len(pairs),
                                  "strength": strength})]


class DoorRule(Rule):
    """T5: door stays open after the cashier has settled (CV cue only), T6: someone then enters.

    Door probes: first seat arrival + 3 s, last arrival + 2 s, end of clip (or 30/60/100 % without
    arrivals). T5 needs >= 2 True (a single True is the cashier still passing through the door)."""

    name = "door"
    types = ("T5", "T6")

    def times(self, ctx: Ctx) -> list[float]:
        arr = [a for a in ctx.seat_arrivals() if a < 0.7 * ctx.duration]
        if arr:
            t1 = ctx.clip(arr[0] + 3.0)
            ts = [t1, ctx.clip(max(t1 + 3.0, arr[-1] + 2.0)), ctx.clip(ctx.duration - 0.5)]
        else:
            ts = [ctx.frac(0.3), ctx.frac(0.6), ctx.clip(ctx.duration - 0.5)]
        # A served client (long visit) sits/stands near the door in the client-service layout and
        # the door-diff CV cannot tell him from an open door; T5 is about the cashier leaving the
        # door open behind her, so probe only while no long-staying visitor is present.
        clients = [v for v in ctx.visits if v.dwell >= 8.0]
        ts = [t for t in ts if not any(v.start - 1.0 <= t <= v.end + 1.0 for v in clients)]
        return sorted(set(ts))

    def _t5(self, ctx: Ctx, hits) -> float:
        """The cashier settles at the desk with the door left open: last seat arrival before the
        first 'open' probe (else that probe's time)."""
        first = hits[0][0].t
        arr = [a for a in ctx.seat_arrivals() if a <= first]
        return arr[-1] if arr else first

    def _entry(self, ctx: Ctx, t5: float) -> Visit | None:
        """First real-looking entry after the door was left open (reflections give short tracks)."""
        ent = [v for v in ctx.visits if v.enters and v.start > t5 and v.dwell >= 2.0]
        return ent[0] if ent else None

    def _count_req(self, ctx: Ctx, v: Visit) -> ProbeRequest:
        return req(self.name, "person_count", ctx.clip(v.start + 1.5), "entry:count")

    def propose(self, ctx):
        out = [req(self.name, "door_open", t, f"door:{i}") for i, t in enumerate(self.times(ctx))]
        if not ctx.staged:
            arr = [a for a in ctx.seat_arrivals() if a < 0.7 * ctx.duration]
            v = self._entry(ctx, ctx.clip(arr[0] + 2.0) if arr else 0.0)
            if v is not None:
                out.append(self._count_req(ctx, v))
        return out

    def followup(self, ctx, pairs):
        if not ctx.staged:
            return []
        hits = [(q, r) for q, r in pairs if q.cue == "door_open" and is_true(r, ctx.cfg.min_probe_conf)]
        if len(hits) < 2:
            return []
        v = self._entry(ctx, self._t5(ctx, hits))
        return [self._count_req(ctx, v)] if v is not None else []

    def decide(self, ctx, pairs):
        door = [(q, r) for q, r in pairs if q.cue == "door_open"]
        hits = [(q, r) for q, r in door if is_true(r, ctx.cfg.min_probe_conf)]
        if not hits:
            return []
        if len(hits) < 2:
            # weak: a single 'open' probe that is not the first one (the first can be the cashier
            # still passing through the doorway)
            if door and hits[0][0] is door[0][0]:
                return []
            q, r = hits[0]
            t5 = self._t5(ctx, hits)
            return [make_event("T5", t5, WEAK_FACT * r.conf, f"кассир у рабочего места с {mm(t5)}, дверь открыта на "
                               f"{mm(q.t)} (одна проверка)", start=t5, end=q.t, evidence=[t5, q.t],
                               source="rule:door:door_open/weak", extra={"hits": 1, "probes": len(door),
                                                                          "strength": "weak"})]
        t5 = self._t5(ctx, hits)
        conf = 0.8 * min(r.conf for _, r in hits) * (len(hits) / len(door)) ** 0.5
        events = [make_event("T5", t5, conf, f"кассир у рабочего места с {mm(t5)}, дверь открыта на "
                             f"{', '.join(mm(q.t) for q, _ in hits)}",
                             start=t5, end=hits[-1][0].t, evidence=[t5] + [q.t for q, _ in hits[:2]],
                             source="rule:door:door_open", extra={"hits": len(hits), "probes": len(door)})]
        v = self._entry(ctx, t5)
        cnt = by_key(pairs).get("entry:count")
        n = count_of(cnt[1]) if cnt else None
        if v is not None and n is not None and (n >= 3 or (n >= 2 and v.at_desk_t is None)):
            events.append(make_event("T6", v.start, 0.75 * conf / 0.8 * cnt[1].conf, "вход через незакрытую дверь, "
                                     f"в помещении {people(n)} на {mm(cnt[0].t)}", start=v.start, end=v.end,
                                     evidence=[hits[0][0].t, v.start, cnt[0].t],
                                     source="rule:door:entry_after_open", extra={"track_id": v.track_id, "count": n}))
        return events


class ClientChangeRule(Rule):
    """Client change with the previous operation unfinished.

    Sequential (previous visitor left, next one enters): cash on the desk (2 looks) or banknotes in
    the counting machine when the next visitor arrives -> T2.
    Parallel (next visitor enters while the previous one is still there): VLM person count (2 looks,
    max) >= 3 = two non-staff people at once -> T6; plus cash/machine still in use -> T2.
    """

    name = "client_change"
    types = ("T2", "T6")

    def pairs_(self, ctx: Ctx):
        cfg = ctx.cfg
        clients = [v for v in ctx.visits if v.dwell >= cfg.client_min_dwell_s]
        out = []
        for b in ctx.visits:
            if not b.enters or b.dwell < cfg.visit_min_dwell_s:
                continue
            prev = [a for a in clients if a.track_id != b.track_id and a.start < b.start - 2.0]
            if not prev:
                continue
            overlapping = [a for a in prev if a.end > b.start + 2.0]
            if overlapping:
                out.append(("par", b, overlapping[0]))
            else:
                a = max(prev, key=lambda v: v.end)
                if 0 <= b.start - a.end + 2.0 <= cfg.client_change_max_gap_s:
                    out.append(("seq", b, a))
        out.sort(key=lambda x: (x[0] != "seq", -x[1].dwell))  # sequential first, then longest
        return out[: cfg.max_client_changes]

    def propose(self, ctx):
        out = []
        for i, (kind, b, a) in enumerate(self.pairs_(ctx)):
            t1 = ctx.clip(b.start + (1.0 if kind == "seq" else 2.0))
            t2 = ctx.clip(b.start + (3.5 if kind == "seq" else 4.5))
            t3 = ctx.clip(b.start + 6.0)
            out += [req(self.name, "cash_on_desk", t1, f"{i}:cash"),
                    req(self.name, "counting_machine_used", t1, f"{i}:machine")]
            if kind == "seq":
                # persistent evidence across the change: before (previous client leaving) and after
                out += [req(self.name, "cash_on_desk", t2, f"{i}:cash:2"),
                        req(self.name, "cash_on_desk", t3, f"{i}:cash:3"),
                        req(self.name, "counting_machine_used", t2, f"{i}:machine:2"),
                        req(self.name, "cash_on_desk", ctx.clip(a.end - 1.0), f"{i}:cash:pre")]
            else:
                out += [req(self.name, "person_count", t1, f"{i}:count"),
                        req(self.name, "person_count", t2, f"{i}:count:2")]
        return out

    def decide(self, ctx, pairs):
        k = by_key(pairs)
        thr = ctx.cfg.min_probe_conf
        events = []
        for i, (kind, b, a) in enumerate(self.pairs_(ctx)):
            cash = [k.get(f"{i}:cash"), k.get(f"{i}:cash:2"), k.get(f"{i}:cash:3")]
            cash = [p for p in cash if p is not None and is_true(p[1], thr)]
            machs = [p for p in (k.get(f"{i}:machine"), k.get(f"{i}:machine:2")) if p is not None and is_true(p[1], thr)]
            mach = machs[0] if machs else None
            left = cash[0] if cash else mach
            what = "банкноты видны на столе" if cash else "банкноты загружены в счётчик"
            pre = k.get(f"{i}:cash:pre")
            n_post = len(cash) + len(machs)
            spans = pre is not None and is_true(pre[1], thr) and n_post >= 1
            if kind == "seq":
                if left:
                    # >= 2 agreeing probes (or before + after the change) = persistent; 1 = weak
                    fact = (0.85 if (n_post >= 2 or spans) else WEAK_FACT) * left[1].conf
                    events.append(make_event(
                        "T2", ctx.clip(b.start + 1.0), fact,
                        f"следующий посетитель вошёл в {mm(b.start)}, {what}",
                        start=a.end, end=left[0].t, evidence=[a.end, b.start, left[0].t],
                        source=f"rule:client_change/seq:{left[0].cue}",
                        extra={"prev": a.track_id, "next": b.track_id, "agreeing_probes": n_post,
                               "spans_change": spans, "strength": "strong" if (n_post >= 2 or spans) else "weak"}))
                continue
            counts = [p for p in (k.get(f"{i}:count"), k.get(f"{i}:count:2")) if p and count_of(p[1]) is not None]
            if not counts:
                continue
            best = max(counts, key=lambda p: count_of(p[1]))
            n = count_of(best[1])
            if n < 3:
                continue
            t = best[0].t
            events.append(make_event(
                "T6", t, 0.7 * best[1].conf, f"в помещении одновременно {people(n)}, второй посетитель вошёл в "
                f"{mm(b.start)}, пока первый ещё у стола", start=b.start, end=min(a.end, b.end), evidence=[b.start, t],
                source="rule:client_change/par:person_count",
                extra={"prev": a.track_id, "next": b.track_id, "count": n}))
            if left:
                events.append(make_event(
                    "T2", t, 0.75 * min(best[1].conf, left[1].conf), f"второй посетитель у стола с {mm(b.start)}, "
                    f"{what}", start=b.start, end=min(a.end, b.end), evidence=[b.start, t, left[0].t],
                    source=f"rule:client_change/par:{left[0].cue}", extra={"prev": a.track_id, "next": b.track_id}))
        return events


class VisitExitRule(Rule):
    """A visitor (real: VLM count >= 2 shortly before leaving) leaves and the cashier is then alone
    (count <= 1): counting machine still in use -> T7 (count not in the client's presence); for a
    short visit (<= short_visit_max_s) a bag left on the desk -> T9 (collector's bag accepted
    without checks). Visits need not start inside the clip (the client may be there at t0)."""

    name = "visit_exit"
    types = ("T7", "T9")

    def visits(self, ctx: Ctx) -> list[Visit]:
        cfg = ctx.cfg
        vs = [v for v in ctx.visits if v.exits and v.dwell >= cfg.visit_min_dwell_s]
        groups: list[Visit] = []  # overlapping fragments of the same visitor -> one visit
        for v in vs:
            if groups and v.start <= groups[-1].end and abs(v.end - groups[-1].end) < 4.0:
                g = groups[-1]
                groups[-1] = replace(g, start=min(g.start, v.start), end=max(g.end, v.end),
                                     at_desk_t=g.at_desk_t or v.at_desk_t)
            else:
                groups.append(replace(v))
        return sorted(groups, key=lambda g: -g.dwell)[: cfg.max_short_visits]

    def propose(self, ctx):
        out = []
        for i, v in enumerate(self.visits(ctx)):
            ta = ctx.clip(v.end + 1.5)
            # "was the visitor real": mid-visit for short visits, shortly before leaving for long ones
            t_mid = (v.start + v.end) / 2 if v.dwell <= ctx.cfg.short_visit_max_s else v.end - 3.0
            out += [req(self.name, "person_count", ctx.clip(t_mid), f"{i}:mid"),
                    req(self.name, "person_count", ta, f"{i}:after"),
                    req(self.name, "counting_machine_used", ta, f"{i}:machine",
                        extra_ts=[ctx.clip(ta + 1.0), ctx.clip(ta + 2.0)]),
                    req(self.name, "counting_machine_used", ctx.clip(v.end - 2.0), f"{i}:machine:leaving",
                        extra_ts=[ctx.clip(v.end - 1.0), ctx.clip(v.end)])]
            if v.dwell <= ctx.cfg.short_visit_max_s:
                out.append(req(self.name, "bag_on_desk", ta, f"{i}:bag"))
        return out

    def decide(self, ctx, pairs):
        k = by_key(pairs)
        events = []
        for i, v in enumerate(self.visits(ctx)):
            mid, after = k.get(f"{i}:mid"), k.get(f"{i}:after")
            n_mid = count_of(mid[1]) if mid else None
            n_after = count_of(after[1]) if after else None
            if n_mid is None or n_mid < 2 or n_after is None or n_after > 1:
                continue
            base = 0.75 * min(mid[1].conf, after[1].conf)
            mach, bag = k.get(f"{i}:machine"), k.get(f"{i}:bag")
            leaving = k.get(f"{i}:machine:leaving")
            if not (mach and is_true(mach[1], ctx.cfg.min_probe_conf)) and leaving and is_true(leaving[1], 0.5):
                # the count runs while the client walks out (perception's exit can lag the door)
                base = WEAK_FACT / max(leaving[1].conf, 1e-6)  # -> fact = WEAK_FACT (one conf factor)
                mach = leaving
            if mach and is_true(mach[1], ctx.cfg.min_probe_conf):
                events.append(make_event(
                    "T7", v.end, base * mach[1].conf, (f"клиент уходит, счётная машина работает на {mm(mach[0].t)}" if mach is leaving else f"после ухода клиента счётная машина работает на {mm(mach[0].t)}"), start=v.start, end=mach[0].t, evidence=[mid[0].t, v.end, mach[0].t],
                    source="rule:visit_exit:counting_machine_used", extra={"dwell": round(v.dwell, 1), "strength": "weak" if mach is leaving else "strong"}))
            elif bag and is_true(bag[1], ctx.cfg.min_probe_conf):
                t = round((v.start + v.end) / 2, 2)
                events.append(make_event(
                    "T9", t, base * bag[1].conf, f"посетитель пробыл около {v.dwell:.0f} с и ушёл в {mm(v.end)}", start=v.start, end=v.end, evidence=[mid[0].t, v.end, bag[0].t],
                    source="rule:visit_exit:bag_on_desk", extra={"dwell": round(v.dwell, 1)}))
        return events


ShortVisitRule = VisitExitRule  # backward-compatible name


class ServiceRule(Rule):
    """Client service visits: procedure-aware ID check (T15) and cash-at-the-body cue (T10).

    Procedure (human review round 2, team decision): a real visitor (VLM count >= 2 mid-visit) who
    leaves a bag on the desk is a collector handover (T9's domain, never T15). Otherwise it is a client
    deposit, and an ID document must be shown between the client's arrival and the cash acceptance
    (~ arrival at the desk): 2 id_document_shown probes x 3 frames ("any") over that window. No ID
    in both -> T15 (п.19 пп.1; low-medium fact_conf, a digital ID is allowed and invisible). For a
    client already there at the clip start the ID may have been shown before the recording: T15 is
    still emitted, at a lower fact_conf.
    T10 (longest visit only): the hand cue (conf >= 0.75) corroborated by the missing ID or a second
    hand window -> 0.6; a single hand cue -> weak."""

    name = "service"
    types = ("T10", "T15")
    MAX_VISITS = 2

    def visits(self, ctx: Ctx) -> list[Visit]:
        vs = [v for v in ctx.visits if v.dwell >= 8.0 and (v.at_desk_t is not None or v.dwell >= 12.0)]
        vs = sorted(vs, key=lambda v: -v.dwell)
        out: list[Visit] = []
        for v in vs:  # distinct visitors: skip fragments overlapping a chosen visit by > 50 %
            if any(min(v.end, o.end) - max(v.start, o.start) > 0.5 * min(v.dwell, o.dwell) for o in out):
                continue
            out.append(v)
        return sorted(out[: self.MAX_VISITS], key=lambda v: v.start)

    def visit(self, ctx: Ctx) -> Visit | None:
        vs = self.visits(ctx)
        return max(vs, key=lambda v: v.dwell) if vs else None

    @staticmethod
    def id_window(v: Visit) -> tuple[float, float]:
        accept = v.at_desk_t if v.at_desk_t is not None and v.at_desk_t > v.start + 2.0 else v.start + 0.35 * v.dwell
        return v.start + 0.5, min(v.end, accept + 1.5)

    def propose(self, ctx):
        if not ctx.cfg.enable_service:
            return []
        out = []
        longest = self.visit(ctx)
        for i, v in enumerate(self.visits(ctx)):
            s, d = v.start, v.dwell
            a, b = self.id_window(v)
            w = (b - a) / 6
            out += [req(self.name, "person_count", ctx.clip(s + 0.5 * d), f"{i}:count"),
                    req(self.name, "bag_on_desk", ctx.clip(s + 0.35 * d), f"{i}:bag"),
                    req(self.name, "bag_on_desk", ctx.clip(s + 0.7 * d), f"{i}:bag:2"),
                    # an "ID" answer while banknotes are being handed over is the banknotes
                    req(self.name, "cash_on_desk", ctx.clip(a + 4 * w), f"{i}:cash_at_id"),
                    req(self.name, "cash_on_desk", ctx.clip(b), f"{i}:cash_at_id:2"),
                    req(self.name, "id_document_shown", ctx.clip(a + w), f"{i}:id:0",
                        extra_ts=[ctx.clip(a + 2 * w), ctx.clip(a + 3 * w)]),
                    req(self.name, "id_document_shown", ctx.clip(a + 4 * w), f"{i}:id:1",
                        extra_ts=[ctx.clip(a + 5 * w), ctx.clip(b)])]
            if v is longest:
                out += [req(self.name, "cash_in_hand_to_body", ctx.clip(s + 0.6 * d), f"{i}:hand",
                            extra_ts=[ctx.clip(s + 0.75 * d), ctx.clip(s + 0.9 * d)]),
                        req(self.name, "cash_in_hand_to_body", ctx.clip(s + 0.3 * d), f"{i}:hand:2",
                            extra_ts=[ctx.clip(s + 0.4 * d), ctx.clip(s + 0.5 * d)])]
        return out

    def decide(self, ctx, pairs):
        k = by_key(pairs)
        events = []
        longest = self.visit(ctx)
        for i, v in enumerate(self.visits(ctx)):
            cnt = k.get(f"{i}:count")
            if cnt is None or (count_of(cnt[1]) or 0) < 2:
                continue  # no real client in the room
            bags = [k.get(f"{i}:bag"), k.get(f"{i}:bag:2")]
            collector = any(p is not None and is_true(p[1], ctx.cfg.min_probe_conf) for p in bags)
            ids = [k.get(f"{i}:id:0"), k.get(f"{i}:id:1")]

            def said_no(p):
                return p is not None and p[1].value is False and p[1].conf >= 0.7

            cash_at_id = any(p is not None and is_true(p[1], ctx.cfg.min_probe_conf)
                             for p in (k.get(f"{i}:cash_at_id"), k.get(f"{i}:cash_at_id:2")))
            id_was_cash = (ids[1] is not None and ids[1][1].value is True and cash_at_id)
            no_id = said_no(ids[0]) and (said_no(ids[1]) or id_was_cash)
            hands = [k[x] for x in (f"{i}:hand", f"{i}:hand:2") if x in k and is_true(k[x][1], 0.75)]
            weak_hands = [k[x] for x in (f"{i}:hand", f"{i}:hand:2") if x in k and is_true(k[x][1], 0.5)]
            if v is longest and not collector:
                if hands and (no_id or len(hands) == 2):
                    q, r = hands[0]
                    events.append(make_event(
                        "T10", q.t, 0.6 * r.conf, "кассир удерживает часть наличных у корпуса во время обслуживания "
                        "клиента", start=max(v.start, q.t - 2.0), end=max(q.extra_ts or [q.t]) + 0.5,
                        evidence=[q.t] + list(q.extra_ts), source="rule:service:cash_in_hand_to_body",
                        extra={"corroboration": "no_id" if no_id else "two_windows"}))
                elif weak_hands:
                    q, r = weak_hands[0]
                    events.append(make_event(
                        "T10", q.t, WEAK_FACT * r.conf, "кассир удерживает часть наличных у корпуса во время "
                        "обслуживания клиента (слабый признак)", start=max(v.start, q.t - 2.0),
                        end=max(q.extra_ts or [q.t]) + 0.5, evidence=[q.t] + list(q.extra_ts),
                        source="rule:service:cash_in_hand_to_body/weak", extra={"strength": "weak"}))
            if collector or not no_id or not ctx.cfg.t15_standalone:
                continue
            a, b = self.id_window(v)
            t15 = v.at_desk_t if v.at_desk_t is not None and v.at_desk_t > v.start + 2.0 else round(b, 2)
            # a client already present at the clip start may have shown the ID before the recording:
            # that lowers fact_conf, it does not drop the event (recall-first)
            at_start = v.start <= 0.5
            level = 0.55 if (said_no(ids[1]) and not at_start) else WEAK_FACT
            conf = level * min(p[1].conf for p in ids)
            events.append(make_event(
                "T15", t15, conf, f"посетитель у стола с {mm(v.start)}, на кадрах {mm(a)}–{mm(b)} карточка "
                "или паспорт не видны", start=v.start, end=b, evidence=[t15, ids[0][0].t, ids[1][0].t],
                source="rule:service:id_document_shown",
                extra={"procedure": "client_deposit", "id_window": [round(a, 2), round(b, 2)], "visit": i,
                       "client_at_clip_start": at_start, "id_answer_was_banknotes": id_was_cash}))
        return events


class UnexplainedAccessRule(Rule):
    """T99 (binding policy: no free VLM pass): a concrete CV anomaly no typed rule explains — a
    non-cashier track reaches the cashier's desk while the cashier is absent. Low fact_conf by
    design (below the verdict threshold): it is shown for review but never flips a verdict alone."""

    name = "unexplained_access"
    types = ("T99",)
    FACT_CONF = 0.2  # combined ~0.18 < verdict threshold 0.25: never flips a verdict alone

    def decide(self, ctx, pairs):
        s = ctx.tl.get("series", {})
        ts, present = s.get("t", []), s.get("cashier_present", [])
        if not ts or len(present) != len(ts) or not ctx.cashier_ids:
            return []
        for c in ctx.cp("visitor_arrives_at_desk"):
            near = [present[i] for i, t in enumerate(ts) if abs(t - c["t"]) <= 1.0]
            if near and all(x is False for x in near):  # None = unknown, not absent
                return [make_event("T99", c["t"], self.FACT_CONF, "посетитель подошёл к рабочему месту кассира, "
                                   "когда кассира в помещении не было", start=c["t"], end=c["t"] + 2.0,
                                   evidence=[c["t"]], source="rule:unexplained_access:cv",
                                   extra={"track_ids": c["track_ids"]})]
        return []


class CameraRule(Rule):
    """T14: camera blocked / dark on >= 2 consecutive CV probes (5 evenly spaced)."""

    name = "camera"
    types = ("T14",)

    def propose(self, ctx):
        return [req(self.name, "camera_blocked", ctx.frac(f), f"cam:{i}")
                for i, f in enumerate((0.1, 0.3, 0.5, 0.7, 0.9))]

    def decide(self, ctx, pairs):
        flags = [is_true(r, 0.6) for _, r in pairs]
        for i in range(len(flags) - 1):
            if flags[i] and flags[i + 1]:
                q, r = pairs[i]
                return [make_event("T14", q.t, 0.8 * r.conf, f"на {mm(q.t)} и {mm(pairs[i + 1][0].t)} подряд, "
                                   "по проверке яркости и деталей кадра", start=q.t, end=pairs[i + 1][0].t,
                                   evidence=[q.t, pairs[i + 1][0].t], source="rule:camera:camera_blocked")]
        return []


def phone_in_hand(ctx: Ctx, q: ProbeRequest, window: float = 1.0, min_score: float = 0.3) -> bool:
    """CV corroboration for a weak phone answer: a COCO 'cell phone' box (+-window s) whose centre lies
    inside a person box of the same sample, i.e. held by someone, not a phone lying on the desk."""
    s = ctx.tl.get("series", {})
    ts, objs, persons = s.get("t") or [], s.get("objects") or [], s.get("persons") or []
    for i, t in enumerate(ts):
        if abs(t - q.t) > window or i >= len(objs):
            continue
        boxes = [p[2] for p in (persons[i] if i < len(persons) else [])]
        for o in objs[i]:
            if o[0] != "cell phone" or o[5] < min_score:
                continue
            cx, cy = (o[1] + o[3]) / 2, (o[2] + o[4]) / 2
            eps = 1e-6  # the box edge counts as inside (float centres)
            if any(b[0] - eps <= cx <= b[2] + eps and b[1] - eps <= cy <= b[3] + eps for b in boxes):
                return True
    return False


def default_rules() -> list[Rule]:
    """Rules in priority order (the VLM budget is spent in this order)."""
    return [
        DepartureRule(),
        DoorRule(),
        PersistentStateRule("safe_tail", "safe_open", "T3", "кассир за столом, сейф не закрыт на {times} "
                            "(до конца записи)",
                            zones=("safe",), need=3, base=0.85, weak_need=2),
        ClientChangeRule(),
        VisitExitRule(),
        PersistentStateRule("belongings", "handbag_or_coat_on_desk", "T11", "видны на столе на {times}",
                            zones=("desk",), fracs=(0.5, 0.8, 1.0), need=2, base=0.85, weak_need=1),
        PersistentStateRule("food", "cup_or_food", "T12", "видны на {times}",
                            zones=("desk",), fracs=(0.45, 0.7, 0.95), need=2, base=0.85, weak_need=1),
        CameraRule(),
        PersistentStateRule("phone", "phone_visible", "T13", "виден на {times}", fracs=(0.4, 0.7, 0.95),
                            need=3, base=0.75, min_conf=0.8, weak_need=2, weak_check=phone_in_hand),
        ServiceRule(),
        UnexplainedAccessRule(),
    ]


# ----------------------------------------------------------------------------- module API

@dataclass
class PlanItem:
    rule: Rule
    requests: list[ProbeRequest] = field(default_factory=list)


def plan(ctx: Ctx, rules: list[Rule] | None = None) -> list[PlanItem]:
    """Per-rule request groups after the VLM budget (lower-priority groups lose requests first;
    a rule that loses any VLM request is dropped entirely, CV-only rules always run)."""
    items, used = [], 0
    for rule in rules or default_rules():
        reqs = rule.propose(ctx)
        n_vlm = sum(r.cue not in CV_CUES for r in reqs)
        if n_vlm and used + n_vlm > ctx.cfg.max_vlm_requests:
            continue
        used += n_vlm
        items.append(PlanItem(rule, reqs))
    return items


def propose(timeline: dict, calibration: dict | None = None, cfg: RulesConfig | None = None) -> list[ProbeRequest]:
    ctx = Ctx(timeline, calibration, cfg)
    return [r for item in plan(ctx) for r in item.requests]


def decide(timeline: dict, calibration: dict | None, probe_results: list[ProbeResult],
           cfg: RulesConfig | None = None) -> list[Event]:
    """Events from the results of `propose(timeline, calibration)` (same order, one per request)."""
    ctx = Ctx(timeline, calibration, cfg)
    items = plan(ctx)
    flat = [(item, r) for item in items for r in item.requests]
    if len(flat) != len(probe_results):
        raise ValueError(f"expected {len(flat)} probe results, got {len(probe_results)}")
    events: list[Event] = []
    for item in items:
        pairs = [(r, res) for (it, r), res in zip(flat, probe_results) if it is item]
        events += item.rule.decide(ctx, pairs)
    return finalize(events, ctx.cfg)


def merge_events(events: list[Event], gap: float = 5.0) -> list[Event]:
    """Same-type events closer than `gap` s merge into the stronger one (interval widened)."""
    import copy

    out: list[Event] = []
    for e in sorted(events, key=lambda e: (e.type, e.timecode_sec)):
        prev = out[-1] if out else None
        if prev is not None and prev.type == e.type and abs(e.timecode_sec - prev.timecode_sec) < gap:
            keep, other = (prev, e) if prev.confidence >= e.confidence else (e, prev)
            keep = copy.copy(keep)  # never mutate the inputs (the analyzer re-finalizes as it streams)
            keep.extra = {**keep.extra, "merged": list(keep.extra.get("merged", []))}
            keep.start_sec = min(keep.start_sec, other.start_sec)
            keep.end_sec = max(keep.end_sec, other.end_sec)
            keep.extra.setdefault("merged", []).append(other.source)
            out[-1] = keep
        else:
            out.append(copy.copy(e))
    return out


def finalize(events: list[Event], cfg: RulesConfig | None = None) -> list[Event]:
    cfg = cfg or RulesConfig()
    merged = merge_events(events, cfg.merge_gap_s)
    top = sorted(merged, key=lambda e: -e.confidence)[: cfg.max_events]
    return sorted(top, key=lambda e: e.timecode_sec)


def verdict(events: list[Event], cfg: RulesConfig | None = None) -> bool:
    cfg = cfg or RulesConfig()
    return any(e.confidence >= cfg.verdict_threshold for e in events)
