# 01 — Synthesized findings & plan (orchestrator)

Sources: `videos/batch_A..D.md`, `regulations.md`, `tech_options.md`. S1 = НБРК № 231 от 29.11.2019 (кассовые операции и инкассация); S2 = НБРК № 14 от 24.02.2020 (охрана и устройство помещений).
Orchestrator spot-verified against frames: X-17 (bag handover), X-44 (detector skipped: clean X-43/X-45 run notes through small detector at ~14–16 s, X-44 hand-counts only), X-1 (stack split, part moves toward body).

## Core insight
The dataset is built as **matched pairs**: same actor/scene, one clean, one with **a single step skipped or a wrong state left behind**. Actions themselves (leaving, safe opening, visitor entering, bag handover) occur in clean videos too.
→ Detect **states at checkpoints** ("when the cashier leaves the room / when a client changes / after a handover — is everything as the correct procedure requires?"), not raw actions. Action-only detectors = false positives.
Labeled second ≈ moment the wrong state becomes final (cashier crosses door, next client enters, sits down with safe open).

Camera set-ups seen: (A) behind cashier's shoulder serving a client, (B) facing cashier alone, (C) rear-side with visitor chair. Final test = new angle → no pixel-hardcoded zones.

## Taxonomy (22/22 training positives covered)

| ID | Type (RU for UI) | Train videos @label | Observable cue / checkpoint | Rule | Difficulty |
|---|---|---|---|---|---|
| T1 | Наличные/ценности оставлены без присмотра | X-15@15, X-24@8, X-26@9 | room empty (no person) + cash/bag/tray on desk; checkpoint = cashier exits | S1 п.20 ч.2; п.22 | easy–med |
| T2 | Наличные предыдущего клиента не убраны / смешение | X-4@57, X-47@68, X-38@33 | new client enters while bundle still on desk; no safe-open between clients | S1 п.20 ч.2 | med |
| T3 | Сейф открыт / ключ оставлен в замке | X-20@5, X-22@14 | safe door open while cashier ≠ at safe; key/tag in keyhole when room empty (tiny!) | S1 п.20 ч.2, п.80 | med / hard (key) |
| T4 | Рабочая станция не заблокирована / оставлена включённой | X-28@7, X-11@18 (X-13 secondary) | screen bright/content when cashier leaves (clean: lock screen / lid closed) | АРРФР № 53 от 03.04.2026 (Требования к ИБ банков) п.71 пп.9, п.11 + S1 п.22 — medium; НБРК №48 repealed, don't cite | med |
| T5 | Дверь кассового помещения оставлена открытой | X-32@5, X-33@6 (X-30 too) | door open >N s after cashier passes and nobody in door zone | S2 п.8, п.16 пп.1 | med |
| T6 | Посторонний / второе лицо в кассе во время операции | X-30@13, X-18@20, X-33@10–14 | ≥2 non-cashier persons, or non-collector without bag entering | S1 п.75; S2 п.21 | easy–med |
| T7 | Пересчёт не в присутствии клиента | X-34@13 | client leaves before/during machine count | S1 п.20 ч.1 | med |
| T8 | Не проведена проверка подлинности / пересчёт | X-44@16, X-13@47 | counting machine/detector idle while notes handled (contrast with clean routine) | S1 п.19 пп.2, п.41 | hard |
| T9 | Приём сумки от инкассатора без проверки документов/сумки | X-17@7 | collector stays <~10 s, no card/document exchange, bag opened after he leaves | S1 п.24 пп.3–4 | med |
| T10 | Сокрытие/неполная выдача наличных | X-1@22, X-2@10 | stack split; hand moves cash to lap/chest/pocket | S1 п.31 пп.4 (recount before client) — weak, describe as "подозрительное действие" | hard |
| T11 | Личные вещи на рабочем месте кассира | X-40@17 | handbag/coat on desk | S1 п.12 | easy |
| T12 | Еда/напитки на рабочем месте кассира | X-46@11 | cup/food container on desk, hand-to-mouth | S1 п.12 (allowed-items list excludes food) — interpretation, low–med; no explicit RK ban | easy |
| T13* | Мобильный телефон в кассе | — (not in train, likely in test) | phone in hand / on desk | S1 п.12 | easy |
| T14* | Перекрытие камеры | — | frame blocked/dark | S2 п.16 пп.6 | easy |
| T99 | Иное подозрительное действие (VLM catch-all) | — | VLM free check, low confidence | no citation unless matched | — |

`*` = derived from regulations for unseen test types (FAQ #9).

## Label notes
- X-4 ≡ X-47 (identical): 57 s = cash left as next client enters (T2), 68 s = new client's cash placed on top (mixing). Emit **both** events.
- Several violation videos contain 2 events (X-13: T8 + T4; X-33: T5 + T6; X-30: T5 + T6). Multi-event output helps.

## Detector design (proposal — pending VLM spike `spike_vlm.md`)
1. **Sample** 2–4 fps. **People track** (Apache-licensed detector + ByteTrack): count persons, entries/exits, who stays at the cashier seat → timeline of *checkpoints*: cashier-exit, client-enter, client-exit, collector visit, safe interaction.
2. **Scene objects per checkpoint** via open-vocab detector / VLM questions: cash on desk, bag on desk, safe door open, laptop screen on/locked, door open, cup/food, handbag, phone, counterfeit detector used.
3. **Rule engine** (deterministic, per taxonomy row) on checkpoint states → typed events with timecode = checkpoint time; evidence frames = checkpoint frame + "before" frame.
4. **VLM pass** (Qwen3-VL-4B Q4, local) over short clips at checkpoints: confirm/classify, write RU description from a template with observed facts; plus a catch-all pass for T99.
5. **Citations only from `rules.json`.** Verdict = violation if any event ≥ threshold; thresholds tuned on the 42 clips for 100% recall first, then minimize FPs.
6. Eval harness on the 42 clips: verdict accuracy, |Δt| to label, per-type confusion.

## Open items
- VLM feasibility & latency on 6 GB → `forte-vlm-spike`.
- T4/T12 rules resolved (see regulations.md V19–V21); T12 basis is weak → description must say «по смыслу п.12», no overclaim.
- Human: also open adilet V2600038336 (АРРФР №53).
- ~~Human: open adilet links~~ — done by orchestrator via Chrome (see bottom).

## Revisions after teammate comparison (02_teammate_comparison.md) — orchestrator-verified
- **X-24**: objects left on desk are a black journal + two teal self-inking stamps (verified frames 3/27), not a tray → T1 variant "штампы/журнал оставлены без присмотра", cite S1 **п.80** (именные штампы), not п.20.
- **X-18**: second man hands an ID card (next client), served while first client's cash still in the counting machine → closer to T2; cite S1 п.20 ч.2.
- **X-1**: our split-stack cue stands; type unresolved — alt: suspicious note returned to client (S1 п.59/п.61).
- **X-2**: emit both cues (no ID shown + closed fist at lap).
- **Citation fixes**: п.75/п.76 apply to vault/safe room — NOT cash room/desk safe (T6 needs other basis: S2 п.8 isolation, S2 п.21); п.31 пп.4 is payouts only (T10); п.41 is a bank-equipment requirement (T8 → use п.19 пп.2 only). п.42 (controller present) must never be a trigger — clean videos show solo counting.
- Adopt: event start–end interval; separate confidence for "fact observed" vs "legal link"; teammate's hard-negative list; exception clauses п.25, п.32, п.49–50; X-4/X-47 and X-5/X-48 in the same CV fold.

## Adilet verification (orchestrator, Chrome, 2026-10-17)
Verified verbatim on adilet.zan.kz (official site, not mirror): S1 V1900019680 п.12, п.19 пп.2, п.20 (both parts), п.22, п.24 пп.3–4, п.42, п.80 (incl. «именных штампов»); S2 V2000020075 п.8, п.16 пп.1 & пп.6 (in the current "действует в следующей редакции" wording); АРРФР №53 V2600038336 п.71 пп.9.
Caveat: S2 п.21 «ограничение доступа и просмотра третьими лицами» is about the collection **box / loading area**, not the cash room → don't use it for T6; T6 basis = S2 п.8 (isolated cash unit, one closable service entrance) + S2 п.16 пп.1.
- Round 2 (Chrome, adilet): ALL 22 citations in deliverables/rules_spec.json verified verbatim on adilet incl. point numbers: №231 п.12 ч.1–2, 19 пп.1–2, 20 ч.1–2, 22, 24 пп.1/3/4, 61, 80; №14 п.8, 16 пп.1; АРРФР №53 п.11, 71 пп.9. Mirror-only status removed for these.

## Human review round 1 (teammate, 4 of 42 videos — review/verdicts/teammate1_partial.json)
- X-1 ✔ T10 confirmed: stack split, only part handed to client, rest not put in safe.
- X-2 ◐ → primary type = **T15** (client not identified / no ID shown before accepting cash); "closed fist" cue demoted to secondary.
- X-4 ✔ T2 (client cash not moved to storage). X-11 ✔ T4.

## Human review round 2 (team, all 42 — review/verdicts/team_full_2026-10-17.json)
- Violations: 19/22 confirmed, 2 partial, 1 wrong.
  - X-1 ◐: confirmed concealment — at 22 s part of the cash in the right hand leaves view; left hand hands the rest to the client; at 26 s the right hand pressed to the chest, stack never reappears on desk or in safe → T10 holds.
  - X-2 ✗ (vs our T10/T15 hypothesis): client enters 5 s, puts cash 9–10 s, cashier unbands 12 s, loads machine 15–16 s, at 16 s hands empty, then cash whereabouts unknown. Combined with team comment round 1 → T15 (no identification) + possible T10 (cash disappears after counting).
  - X-11 ✔ + extra cue: at 12 s cashier touches the KEY IN THE SAFE LOCK (T3 secondary).
  - X-13 ◐: T8 (no counting) correct, but laptop screen was OFF → drop T4 secondary.
- Clean videos: 16/20 confirmed; **X-19, X-35, X-37, X-39 flagged by team: no ID document presented/checked** (X-19: first client). **Team decision (17.10 ~18:43): treat missing identification as a procedure violation (T15) even where the organizer label says clean.** Approach suggested by team: first identify participants → infer the procedure (инкассация / внесение / снятие) → check that procedure's required steps.
