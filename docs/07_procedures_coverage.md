# 07 — Покрытие процедур: кассовая операция → обязательные шаги → нарушение → статус детектора

Date: 2026-10-18. Detector frozen at `38f9946`; this is an analysis document, no code changes.
Approach (team decision 17.10): **first identify the cash operation from the participants
(клиент / инкассатор / второй сотрудник / кассир один), then check that operation's required visible steps.**

Sources used — and only these:
- `docs/regulations.md` (verbatim quotes; S1 = Правила 231, НБРК № 231; S2 = Правила 14, НБРК № 14; S5 = АРРФР № 53).
  Points verified on adilet (per `01_findings.md`): S1 п.12, 19 пп.1–2, 20, 22, 24 пп.1/3/4, 42, 61, 80; S2 п.8, 16 пп.1/6; S5 п.11, 71 пп.9.
  Other points are quoted in regulations.md from the zakon.uchet.kz mirror only (marked *mirror*).
- `docs/01_findings.md` (taxonomy, training labels), `docs/deliverables/04_*.md`, `05_*.md`, `detector/taxonomy/rules.json`,
  `docs/status_robust.md` (external-clip evidence).

**«не сверено»** = the point is mentioned or expected, but its text is NOT quoted in regulations.md. Do not cite it in the service output.

## Легенда статуса детектора (`38f9946`)

| Status | Meaning |
|---|---|
| **R✔** | rule exists and is tested on organizer data with recall (05 §5.2 per-type table) |
| **R◐** | rule exists, partly tested / known weak (miss or wrong type in 05, or fails on external clips) |
| **R○** | rule exists, **no recall evidence** on real footage (only FP check, synthetic, or nothing) |
| **—** | no rule; only an indirect type may fire by accident |

Per-type evidence summary (from 05 §5.2/5.4 and status_robust.md):
T1 2/2 · T1b 1/1 · T2 4/4 (type 3/4) · T3 2/3 (type 1/3; key probe 43% precision; ext13 safe-left-open close-up **missed**) ·
T4 2/2 · T5 3/3 (but not run when no door in frame; lost X-32/X-33 on new-angle sim) · T6 2/2 verdict, type 1/2 (**type-hit 0/8 on external intruder clips**) ·
T7 1/1 verdict, type 0/1 · T8 **no rule** · T9 1/1 · T10 2/2 (ext01 pocketing + leaving **missed**; e2_01, e2_31 theft **missed**) ·
T11 1/1 · T12 1/1 · T13 (67d1dbe) phone set 10–11/12 (was 7/12; 0 T13 on 4 clean), MEVA 4/8 (0/3 neg), e2_26 caught; misses ext02 (cashier ≈ client holding a phone) and e2_27 (half-hidden) ·
T14 synthetic 10/10 (FP 0/6), covered-from-frame-0 8/8, dim/lights-off FP 0/8, real spray clip 1/1; miss: hand at the lens from frame 0 · T15 low-confidence, fires on 4 organizer-clean clips · T99 never fired, untested.

---

## (a)+(b)+(c) Процедуры, шаги, нарушения

Columns: **Шаг** — required visible step; **Норма** — point (verbatim-backed in regulations.md unless marked);
**Нарушение при пропуске**; **Train** — present in the 42 organizer videos (video ids); **T** — code; **Статус**.

### P1. Приём наличных от клиента (взнос)
Identification cue: one client at the window/chair, client hands cash, cashier counts.

| # | Шаг | Норма | Нарушение при пропуске / неверном выполнении | Train | T | Статус |
|---|---|---|---|---|---|---|
| 1.1 | Визуальная идентификация по документу (или цифровой документ) | S1 п.19 пп.1) | Деньги приняты без предъявления документа | X-2 (secondary); team flags X-19/35/37/39 (organizer: clean) | T15 | R◐ (low legal conf; digital ID invisible) |
| 1.2 | Полистный пересчёт + проверка подлинности/платёжности | S1 п.19 пп.2) («…в соответствии с пунктами 57 и 58») | Купюры не прошли детектор/счётную машину | X-44, X-13 | T8 | **—** (no rule; X-44 missed, X-13 found as T2) |
| 1.2а | Проверка подлинности купюр под УФ-детектором (кассир подносит купюры к детектору) | S1 п.19 пп.2) («с проверкой на подлинность…»); порядок проверки п.57/58 — **не сверено** | Купюры не поднесены к детектору | X-44 (detector not used vs clean pair) | T8 | **—** (no rule, no «detector used» cue). Side effect: cashier holding notes at the UV detector can look like «деньги к телу» → **possible T10 FP** (05 §5.4, T10 row) |
| 1.3 | Приём и пересчёт **в присутствии клиента** | S1 п.20 ч.1 | Клиент ушёл до/во время пересчёта | X-34 | T7 | R◐ (found, type 0/1). Hard negative: повторная служебная сортировка после завершённого приёма — не T7 (09 §3.7, B ПР02 / анти-FP 06) |
| 1.4 | На рабочем месте только деньги текущего клиента; ранее принятые — в закрывающихся устройствах | S1 п.20 ч.2 | Деньги прошлого клиента на столе/в машине при следующем; смешение | X-4/X-47, X-38, X-18 | T2 | R✔ |
| 1.5 | Не покидать рабочее место до окончания обслуживания | S1 п.22 | Кассир уходит при клиенте/с открытыми деньгами | X-15, X-26 (with cash left) | T1 | R✔ (T1); «ушёл при клиенте, стол пуст» — **—** |
| 1.6 | Вся принятая сумма уходит в хранилище (сейф/ящик), ничего «к телу» — по уровням доказанности (09 §3.6–3.7): | | | | | |
| 1.6а | — пачка **перестала быть видна** | — (наблюдение, не нарушение) | — | X-1 (split stack), X-2 (fist) | T10 (low fact_conf, «требует проверки по записи») | R◐ (organizer 2/2) |
| 1.6б | — пачка **прослежена до кармана / личной вещи** (непрерывно) | S1 п.20 ч.2 ✔adilet; п.12 ч.1 (личные деньги вне узла) | Часть принятой суммы в кармане / личной сумке | no | T10 | **—** не утверждается: непрерывного прослеживания по одной камере нет; external pocketing ext01, e2_01, e2_31 **missed**; roadmap 09 §4 #2 |
| 1.6в | — **состав пачки изменён после пересчёта** | S1 п.19 пп.4 ✔adilet (сверка суммы с документом) / п.31 пп.4 ✔adilet | Сумма в документе ≠ переданной | X-1 (alt.) | T10 | R◐ (only as T10 cue); в продукте — чек-лист `cash_traced_to_storage` (п.20 ч.2; п.19 пп.4) |
| 1.8 | Проверка подписи клиента; сверка суммы приходного кассового документа с принятой суммой; оформление операции (ноутбук) | S1 п.19 пп.3)–4) (verified adilet, regulations.md Addendum); ввод в систему как таковой цитатой не нормирован | Подпись / сумма не сверены | not shown in training | — | **—** — не детектируется: правильность документа и проводки на видео не проверяема (ТЗ §7); экран ноутбука не читается; в продукте — чек-лист `client_signature_checked` (п.19 пп.3 / п.31 пп.2) |
| 1.9 | Подписание и штамп на всех экземплярах приходного кассового документа; выдача клиенту документа, подтверждающего операцию | S1 п.19 пп.5)–6) (verified adilet, regulations.md Addendum) | Клиент ушёл без подтверждающего документа | not shown in training | — | **—** — выдача квитанции на камере почти не видна (small paper, hands at the window) — **не детектируется**; в продукте — чек-лист проверяющего (`cash_doc_signed`, `client_got_receipt`) |
| 1.7 | Подозрительная купюра — см. P10 | S1 п.59, п.61 | — | — | — | — |

### P2. Выдача наличных клиенту
Identification cue: cashier takes cash from safe/drawer, passes it to client.

| # | Шаг | Норма | Нарушение | Train | T | Статус |
|---|---|---|---|---|---|---|
| 2.1 | Пересчёт подготовленных к выдаче денег **в присутствии клиента**, затем выдача | S1 п.31 пп.4) (verified adilet, regulations.md Addendum) | Выдача без видимого пересчёта | no (X-1/X-2 are deposits per 04 §T10) | — (T7-style rule possible; verified basis now exists) | **—**; в продукте — чек-лист `payout_counted_in_presence` |
| 2.2 | Исключение: полные и неполные **пачки** и полные **корешки** в неповреждённой упаковке выдаются по сумме на упаковке без полистного пересчёта; неполный или повреждённый **корешок** и монеты из неполного мешка — пересчёт обязателен | S1 п.32 *mirror* | (not a violation — hard negative) | — | — | must NOT flag wrapped packs; «корешок vs пачка» по видео обычно не различим → не детектируется |
| 2.3 | Клиент пересчитывает, не отходя от кассы, в присутствии кассира | S1 п.35 *mirror* | Клиент сразу уходит (client-side, soft) | no | — | **—** |
| 2.4 | Идентификация получателя | S1 п.31 пп.1) (verified adilet, regulations.md Addendum) | Выдача без документа | — | T15 is worded for deposits (п.19 пп.1) | — (do not reuse п.19 for payouts; п.31 пп.1 is the payout basis) |
| 2.4а | Подписание расходного кассового документа и выдача клиенту подтверждающего документа | S1 п.31 пп.5)–6) (verified adilet, regulations.md Addendum) | Клиент ушёл без подтверждающего документа | no | — | **—** — не детектируется; в продукте — чек-лист проверяющего |
| 2.5 | Выдать всю сумму (не утаить часть) | S1 п.31 пп.4) (context) | Часть пачки остаётся у кассира | no | T10 | R◐ |
| 2.6 | Деньги между выдачами — в закрывающихся устройствах | S1 п.20 ч.2 | Резерв для выдач лежит открыто на столе | partly (T2 scenes) | T1/T2 | R✔ only at checkpoints (exit / client change) |

### P3. Обмен / размен
S2 п.2 пп.2) lists «размену, обмену» in the definition of «касса». **Step-by-step procedure for обмен/размен is not quoted in regulations.md — не сверено.**
Visually it is P1 (receive + count in presence, п.20 ч.1) followed by P2 (count out in presence, п.31 пп.4). Same coverage as P1/P2; nothing exchange-specific is detectable or cited. Not in training.

### P4. Приём сумки от инкассатора
Identification cue: person in vest/uniform (п.103 equipment) arrives with a bag.

| # | Шаг | Норма | Нарушение | Train | T | Статус |
|---|---|---|---|---|---|---|
| 4.1 | Проверка препроводительного документа (сведения п.106) | S1 п.24 пп.1); п.106 *mirror* | Документ не передан/не просмотрен | X-17 | T9 | R✔ (content itself N) |
| 4.2 | Регистрация суммы | S1 п.24 пп.2) | (N — not visible) | — | — | not claimed |
| 4.3 | Проверка целостности сумки | S1 п.24 пп.3) | Сумка сразу убрана/вскрыта без осмотра | X-17 | T9 | R✔. Норма не задаёт длительность осмотра; краткий осмотр по движению рук не исключается (B ИН01/ИН02) |
| 4.4 | Проверка пломбы | S1 п.24 пп.4) | То же | X-17 | T9 | R✔ |
| 4.5 | Бригада ≥3 (или 2 по п.99) | S1 п.98, п.99 *mirror* | Инкассатор один | no | — | **—** (low; exceptions invisible) |
| 4.6 | Инкассация без доступа/просмотра третьих лиц | S2 п.21 *mirror* — **applies to the bokс/loading area, not the cash room (01_findings)** | Клиент присутствует при сдаче сумки | no | (T6) | — do not cite п.21 for the cash room |

### P5. Передача сумки инкассатору (сдача)

| # | Шаг | Норма | Нарушение | Train | T | Статус |
|---|---|---|---|---|---|---|
| 5.1 | Инкассатор-сборщик проверяет целостность, пломбу (шпагат, оттиск), явочную карточку, препроводительный документ | S1 п.107 *mirror* (duty of the collector) | Сумка взята без осмотра пломбы | no | — | **—** (T9 is wired to receipt, not hand-out) |
| 5.2 | Сумка сформирована и опломбирована до прихода инкассатора; деньги не лежат открыто | S1 п.20 ч.2 | Деньги/сумка без пломбы на столе, кассир уходит | partly | T1 | R✔ only if cashier exits |
| 5.3 | Препроводительный документ в 3 экз. | S1 п.106 *mirror* | (N) | — | — | not claimed |

### P6. Пересчёт / сортировка / упаковка / формирование корешков и пачек

| # | Шаг | Норма | Нарушение | Train | T | Статус |
|---|---|---|---|---|---|---|
| 6.1 | В присутствии контролирующего работника (кроме приёма/выдачи клиенту) | S1 п.42 (verified) | Кассир один пересчитывает/упаковывает | no as a labelled type | — | **deliberately not a trigger** (clean videos show solo counting while serving clients; 04 «не заявляет») |
| 6.2 | На оборудовании, проверяющем ≥2 машиночитаемых признака | S1 п.41 *mirror* (bank-equipment requirement) | — | — | — | not cited (01_findings) |
| 6.3 | Сортировка по номиналам и износу | S1 п.43 *mirror* | (P; correctness invisible) | — | — | not claimed |
| 6.4 | Остатки — полистный пересчёт и объединение; «сборные» пачки | S1 п.49, п.50 *mirror* | (allowed — hard negative) | — | — | must NOT flag |
| 6.5 | Реквизиты бандероли/накладки (п.46 ч.2 — **не сверено**; п.50 накладки *mirror*) | — | (N) | — | — | not claimed |
| 6.6 | Пересчитанные пачки — в хранилище, не на столе при уходе | S1 п.20 ч.2 | Пачки оставлены | X-15/X-26 style | T1 | R✔ |

### P7. Работа с сейфом / хранилищем (кладовая, сейфовая комната)

| # | Шаг | Норма | Нарушение | Train | T | Статус |
|---|---|---|---|---|---|---|
| 7.1 | Наличные хранятся в устройствах, закрывающихся на ключ | S1 п.20 ч.2 | Сейф открыт, кассир не у сейфа / ушёл | X-20 | T3 | R◐ (2/3; ext13 missed) |
| 7.2 | Ключи от устройств на рабочем месте — порядок, исключающий несанкц. использование | S1 п.80 (verified) | Ключ в замке / ключи на столе | X-22 (missed), X-11 (secondary) | T3 | R◐ (key probe 43% precision) |
| 7.3 | Именные штампы, пломбираторы, клише — то же | S1 п.80 | Штампы/журнал на столе при уходе | X-24 | T1b | R✔ |
| 7.4 | Вход в кладовую/сейфовую комнату — только ответственные лица или с сопровождением | S1 п.75 *mirror* | Посторонний у хранилища | no | (T6/T99) | **—** — п.75 is vault/safe-room only, **not** the desk safe (01_findings) |
| 7.5 | Ключи/коды двери кладовой — у ответственных | S1 п.76 *mirror* | Ключ передан третьему лицу | no | — | **—** (vault only) |
| 7.6 | Раздельное хранение по валюте/номиналу | S1 п.77 *mirror* | (P) | — | — | not claimed |

### P8. Начало / конец рабочего дня (и любой уход из кассы)
Start-of-day issue of cash to the cashier from the vault — procedure **не сверено** (not quoted).

| # | Шаг | Норма | Нарушение | Train | T | Статус |
|---|---|---|---|---|---|---|
| 8.1 | Личные вещи — вне кассового узла | S1 п.12 ч.1 | Сумка/куртка на столе | X-40 | T11 | R✔ (external FP-prone) |
| 8.2 | Вносить можно только лекарства, медизделия, связь **без фото/видео** | S1 п.12 ч.2 | Смартфон в руке/на столе | **no** | T13 | **R○** (0 recall evidence; ext02, e2_27 missed) |
| 8.3 | (то же, толкование) | S1 п.12 (interpretation) | Еда/напитки | X-46 | T12 | R✔ (low legal conf) |
| 8.4 | Сверка сумм в конце дня кассир + контролирующий | S1 п.56 *mirror* | Сверка одним | no | — | **—** (not claimed; N/P) |
| 8.5 | При уходе: деньги убраны (п.20 ч.2), сейф закрыт (п.20 ч.2, п.80), штампы убраны (п.80), экран заблокирован (S5 п.71 пп.9, п.11), дверь закрыта (S2 п.8, п.16 пп.1) | see left | Any wrong state left at the exit checkpoint | X-15/24/26, X-20/22, X-28/11, X-32/33 | T1/T1b/T3/T4/T5 | R✔ except T3 R◐; T5 needs door in frame |
| 8.6 | Касса оборудуется видеокамерами — камера не перекрывается | S2 п.16 пп.6) | Камера закрыта/отвёрнута/закрашена | **no** | T14 | R○+ (synthetic 10/10, 1 real clip; none if covered from frame 0) |
| 8.7 | Кассовый узел изолирован, один закрывающийся вход | S2 п.8; п.16 пп.1) | Второе лицо заходит на сторону кассира | X-30, X-33 | T6 | R◐ (organizer ok; **external type-hit 0/8**) |

### P9. Передача денег между кассирами

| # | Шаг | Норма | Нарушение | Train | T | Статус |
|---|---|---|---|---|---|---|
| 9.1 | Передача отражается в документе учёта | S1 п.15 *mirror* | Передача без записи (log invisible) | no | — | **—** (low; at most "event to check the log") |
| 9.2 | Передаваемые деньги не остаются открыто/не смешиваются | S1 п.20 ч.2 | Пачки второго кассира на столе при клиенте | no | T2 (if a client change is detected) | R◐ |
| 9.3 | Второй сотрудник на стороне кассира | S2 п.8 | May be legit (controller, п.42) | — | T6 | R◐ — FP risk on legit handovers |

### P10. Подозрительные / неплатёжные банкноты (п.57–61)

| # | Шаг | Норма | Нарушение | Train | T | Статус |
|---|---|---|---|---|---|---|
| 10.1 | Проверка платёжности по Правилам № 230 | S1 п.57 *mirror* (cross-ref only; Rules 230 **не сверено**) | — | — | — | not cited (04) |
| 10.2 | Проверка иностранной валюты по образцам | S1 п.58 — **не сверено** (mentioned, not quoted) | — | — | — | — |
| 10.3 | Сомнительная купюра: акт в 2 экз. **в присутствии клиента и заведующего кассой**, купюра остаётся в банке | S1 п.59 ч.1 ✔adilet (18.10) | Купюра отложена/возвращена без вызова заведующего | no (X-1 alt. reading) | T10 (alt.) | **—** as a dedicated rule; T10 may fire via split-stack cue; в продукте — чек-лист `suspicious_note_procedure` |
| 10.4 | Признаки подделки: сообщить в правоохранительные органы, изъятие по протоколу | S1 п.60 — **не сверено** | — | — | — | — |
| 10.5 | Поддельные клиенту **не возвращаются** | S1 п.61 ✔adilet | Купюра передана обратно через стол | no (X-1 alt.) | T10 (alt.) | **—** (indirect); чек-лист `suspicious_note_procedure` |

### P11. Повреждённая сумка (п.25)

| # | Шаг | Норма | Нарушение | Train | T | Статус |
|---|---|---|---|---|---|---|
| 11.1 | При нарушении целостности — вскрыть и пересчитать **в присутствии инкассатора** | S1 п.25 ч.1 ✔adilet (18.10) | Сумка вскрыта после ухода инкассатора | no (X-17 is п.24, bag opened after he left is one T9 cue) | T9 | R◐ (cue exists; damaged-bag case never tested); в продукте — чек-лист `damaged_bag_opened_with_collector` |
| 11.2 | При недостаче/излишке — акт | S1 п.25 ч.2 | (N) | — | — | not claimed |
| 11.3 | Если сумка целая — вскрытие без инкассатора допустимо | S1 п.24/п.25 (a contrario) | (hard negative) | — | — | must NOT flag every late opening |

---

## (d) Top-10 most likely UNSEEN test violations

Assumptions: organizers stage in the same small room style (desk, counting machine, laptop, grey safe, glass door) as matched
pairs — one skipped step vs clean; FAQ says every violation has an open RK basis, so they will pick points with a crisp visual.
Ranking = P(organizers stage it) × (how badly we would miss it).

| Rank | Unseen violation | Basis | Why likely | Our coverage | Risk |
|---|---|---|---|---|---|
| 1 | **Кассир пользуется смартфоном за рабочим местом** | S1 п.12 ч.2 (verified, high) | Trivial to stage, verbatim rule, the most cited "cash-room ban" | T13 R◐ (67d1dbe: phone set 10–11/12, MEVA 4/8; misses ext02, e2_27) | Medium |
| 2 | **Перекрытие / отворот камеры** | S2 п.16 пп.6 (verified) | Classic CCTV-discipline violation; one prop | T14 R◐ (synthetic 10/10, from-frame-0 8/8, 1 real; miss: hand at lens from frame 0) | Low–medium |
| 3 | **Кассир кладёт деньги в карман / личную сумку** (явное хищение) | S1 п.20 ч.2; п.12 ч.1 (личные деньги вне узла); п.19 пп.2 context | Natural "harder" sibling of X-1/X-2; jury-friendly | T10 R◐ — external pocketing clips missed 3/3 | **High** |
| 4 | **Выдача денег без пересчёта при клиенте** | S1 п.31 пп.4 (verified 18.10) | Training has only deposit procedures; payout is the obvious missing procedure | **no rule** (п.32 wrapped-pack exception complicates) | **High** |
| 5 | **Второе лицо (не сотрудник) входит, подходит к столу/сейфу** | S2 п.8, п.16 пп.1 | Seen type but likely re-staged with new actor/angle | T6 R◐ — external type-hit 0/8; T99 untested | Medium |
| 6 | **Сейф оставлен открытым при уходе / ключ в замке** | S1 п.20 ч.2, п.80 | Seen, but key variant was missed; easy to restage | T3 R◐ — key probe 43%, ext13 missed | Medium–high |
| 7 | **Сомнительная купюра возвращена клиенту / не оформлена с заведующим** | S1 п.59 ч.1 ✔adilet, п.61 ✔adilet | Explicit point-pair 57–61 in the act; stageable with a UV lamp | **no rule** (T10 only by accident) | High |
| 8 | **Повреждённая сумка вскрыта без инкассатора** | S1 п.25 ч.1 ✔adilet | Natural pair to X-17 (bag scene already built) | T9 cue R◐, damaged-bag case untested | Medium |
| 9 | **Сдача сумки инкассатору без проверки пломбы / сумка неопломбирована** | S1 п.107 *mirror* | Mirror of X-17 in the other direction | **no rule** | Medium–high |
| 10 | **Деньги оставлены на столе при уходе — новый ракурс / без людей в кадре** | S1 п.20 ч.2, п.22 | Seen type, but organizers may re-shoot at top-down angle; e2_27 (cash on desk, nobody) missed | T1 R✔ on our room, R◐ on new scenes | Medium |

Honourable mentions (lower probability or not claimable): counting/packing alone without controller (S1 п.42 — deliberately not a
trigger, would conflict with clean training videos); collector alone (п.98/99 — exceptions invisible); cash handed between
cashiers without log (п.15 — N); client leaves without recounting (п.35 — client-side); end-of-day reconciliation alone (п.56).

### Implications (no code change — for the reviewer script / pitch)
- Ranks 1, 3, 6, 10 have a rule but weak/no real-footage evidence → synthetic clips (`docs/08_synthetic_prompts.md`).
- Ranks 4, 7, 9 have **no rule**: if staged, only T99 or an incidental T10/T15 can catch them. State this in 05 §5.3 item 6 as known gaps.
- Never cite п.58, п.60, Rules 230, п.46, or any exchange-specific procedure — not verified.
- Also never cite (09 §2.9, §3.7; не сверены): №231 п.10-1, п.11, п.21, п.33, п.34, п.36, п.45, п.46–48, п.51–54, п.94, п.105; АРРФР №53 п.72, п.73;
  №88 п.5(4); Закон РК №94-V «О персональных данных и их защите» (ст.7, 9, 11, 14, 15); «Закон РК от 16.01.2026 №258-VIII “О банках и банковской
  деятельности”» ст.69 — реквизиты и вступление в силу командой не проверены. Внутренние правила банка (B ВН04/07/16/20) — не НПА, не цитировать.
