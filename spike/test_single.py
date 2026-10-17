"""Single-frame cue tests. Ground truth from docs/videos/batch_C.md + manual frame checks.
Usage: python test_single.py [mode]   mode in {full, crop, both}
Writes results/single_<mode>.jsonl
"""
import json, sys, os
from vlm import ask, load, t2k

MODE = sys.argv[1] if len(sys.argv) > 1 else "both"
os.makedirs("results", exist_ok=True)

LAPTOP_CROP_A = (220, 290, 360, 420)  # setup A laptop, x3
DESK_CROP_A = (80, 260, 420, 520)     # setup A desk, x2
DOOR_CROP_B = (0, 0, 300, 460)        # setup B door, x1.5

YESNO = {"type": "object", "properties": {"answer": {"type": "string", "enum": ["yes", "no"]},
                                          "reason": {"type": "string", "maxLength": 200}},
         "required": ["answer", "reason"]}
COUNT = {"type": "object", "properties": {"num_people": {"type": "integer"},
                                          "people": {"type": "array", "items": {"type": "string"}}},
         "required": ["num_people", "people"]}

Q_LAPTOP = ("Look at the laptop on the desk. Is its screen showing an unlocked application "
            "(e.g. a spreadsheet, document, white window with rows) rather than a lock screen, "
            "wallpaper or black screen? Answer yes if application content is visible.")
Q_LAPTOP_STATE = {"type": "object", "properties": {"screen": {"type": "string",
                  "enum": ["application_content", "lock_screen_or_wallpaper", "black_off", "not_visible"]}},
                  "required": ["screen"]}
Q_DOOR = ("This is a cash room seen from a ceiling camera. The glass door is in the upper-left part "
          "(a glass panel with a metal frame and a handle). Is the glass door open or ajar "
          "(not fully closed)?")
Q_CASH = ("This is a bank cash room. Are banknotes / cash lying on the desk while no person is at "
          "the desk (cash left unattended)? Ignore the counting machine, phone and laptop.")
Q_COUNT = ("Count the people inside this room (ignore people seen through the glass in the office "
           "behind). For each person give a short role guess, e.g. 'cashier seated at desk', "
           "'visitor standing', 'person at door'.")

# (question_id, prompt, schema, vid, t_sec, expected, crop, scale)
CASES = []
def add(q, vid, ts, exp):
    for t in ts:
        CASES.append((q, vid, t, exp))

# laptop: X-28 content whole clip; X-29 lock screen from ~6.5 s
add("laptop", "X-28", [1, 8, 12, 15], "yes")
add("laptop", "X-29", [1], "yes")
add("laptop", "X-29", [8, 12, 16], "no")
# door: X-32 open from ~6 s; closed at 0. X-31 closed after 10 s. X-30 open 5-18 s
add("door", "X-32", [0], "no")
add("door", "X-32", [8, 15, 20], "yes")
add("door", "X-31", [12, 18, 23], "no")
add("door", "X-30", [8, 11], "yes")
# cash: X-26 empty room + cash from 15.5 s; X-27 empty room+empty desk from ~24 s; X-26 0-7 s person present
add("cash", "X-26", [16, 17.5, 19], "yes")
add("cash", "X-27", [24, 25, 26], "no")
add("cash", "X-26", [3], "no")
# count
add("count", "X-30", [14, 16, 18], 2)
add("count", "X-31", [15, 22], 1)
add("count", "X-27", [25], 0)
add("count", "X-26", [18], 0)
add("count", "X-28", [3], 1)

PROMPTS = {"laptop": (Q_LAPTOP, YESNO), "door": (Q_DOOR, YESNO), "cash": (Q_CASH, YESNO),
           "count": (Q_COUNT, COUNT)}
CROPS = {"laptop": (LAPTOP_CROP_A, 3), "cash": (DESK_CROP_A, 2), "door": (DOOR_CROP_B, 1.5), "count": None}


def run(mode):
    out = open(f"results/single_{mode}{os.environ.get('SUF','')}.jsonl", "w")
    ok = n = 0
    per_q = {}
    for q, vid, t, exp in CASES:
        prompt, schema = PROMPTS[q]
        k = t2k(t)
        if mode == "crop" and CROPS[q]:
            crop, sc = CROPS[q]
            im = load(vid, k, crop=crop, scale=sc)
        elif mode == "crop":
            continue
        else:
            im = load(vid, k)
        r = ask([im], prompt, schema=schema, max_tokens=200)
        j = r.get("json") or {}
        got = j.get("answer") if q != "count" else j.get("num_people")
        hit = got == exp
        ok += hit; n += 1
        a = per_q.setdefault(q, [0, 0]); a[0] += hit; a[1] += 1
        rec = {"q": q, "vid": vid, "t": t, "exp": exp, "got": got, "hit": hit, **r}
        out.write(json.dumps(rec, ensure_ascii=False) + "\n"); out.flush()
        print(f"{mode:5s} {q:6s} {vid} t={t:5} exp={exp!s:4} got={got!s:4} {'OK ' if hit else 'BAD'} "
              f"{r['wall_s']:.2f}s pn={r['prompt_n']} | {(j.get('reason') or j.get('people') or '')!s:.110}")
    print(f"== {mode}: {ok}/{n}", {k: f"{v[0]}/{v[1]}" for k, v in per_q.items()})


if __name__ == "__main__":
    for m in (["full", "crop"] if MODE == "both" else [MODE]):
        run(m)
