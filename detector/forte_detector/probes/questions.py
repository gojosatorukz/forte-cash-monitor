"""VLM prompts: ONE narrow cue per call, yes/no (or a count), English (the spike's proven wording).

Rules from docs/spike_vlm.md: name the object and what counts as "yes", say what to ignore, never
ask for several fields at once, never ask about the glass door (door state is CV only).

`roi`: which calibrated region to crop (None = full frame); `margin`: relative padding added
around that region before cropping (safe doors swing outward, so the safe gets a wide margin);
`multi`: "any" = keyframe + extra frames go into one call and the answer is "in any frame" (short
actions), "key" = keyframe only (persistent states).
"""
from __future__ import annotations

from dataclasses import dataclass

SCENE = "This is a bank cash room seen from a ceiling security camera. "
FIXED = "Ignore the fixed equipment: the banknote counting machine, the desk telephone and the laptop. "


@dataclass(frozen=True)
class QuestionSpec:
    prompt: str
    kind: str = "yesno"  # "yesno" | "count"
    roi: str | None = None  # calibration key: "laptop" | "safe" | "desk" | None
    margin: float = 0.15
    multi: str = "key"  # "key" | "any"
    min_side: int = 448  # crops are upscaled so their long side reaches this (at most 4x, runner._crop)
    full_prompt: str | None = None  # used instead of `prompt` when the ROI is unknown (full frame)
    autocontrast: bool = False  # stretch the crop's 1-99 % luminance (dark / washed-out cameras)


DESK = "Close-up of a cashier's desk in a bank cash room, seen from a ceiling camera. "
DESK_FIXED = ("Ignore the fixed equipment: the banknote counting machine, the small banknote detector, "
              "the desk telephone and the laptop. ")

# Desk cues use the calibrated desk crop upscaled (672 px; cash 896 px): on full 464x848 frames a small cash stack
# or two stamps are a few pixels and the 4B model answers "no" (X-38 @30-34 s, X-24 @12 s).
# calibration gate for the door zone (not a cue): a room view vs a close-up of a table / objects.
# Asking "is there a door?" directly failed: the 4B model says "no" for glass doors that look like part
# of a glass partition (PC test on t=0 frames, 42 open + 14 external clips: door question 34/56, this
# room-view question 55/56; the door-less close-ups ext11-14 -> no, every cash-room clip -> yes).
DOOR_GATE_PROMPT = ("Is this a view of a room (walls, floor or furniture visible), as opposed to a close-up of "
                    "a table top or of objects? Answer yes for a room view.")

BNH_PROMPT = (SCENE + "Is someone holding paper money in their hand, or passing banknotes or a bundle of notes "
              "from hand to hand? Ignore money lying on the desk or inside the counting machine. A small "
              "plastic ID card or a passport is not money.")

SPECS: dict[str, QuestionSpec] = {
    "cash_on_desk": QuestionSpec(
        # tuned live (4B): pointing at the desk surface and saying the bundle may be small fixes X-38
        # (small brick of notes next to the laptop); crops upscaled to 896 px
        "Close-up of a cashier's desk seen from a ceiling camera. Look at the desk surface itself, not at "
        "the machines. Is any money lying there: banknotes or a bundle / stack of banknotes (it may be "
        "small, a few centimetres; e.g. light blue, green or beige notes)? Do not count notes that are "
        "inside the counting machine (in its feed hopper or output stacker). Answer yes if you see any.",
        roi="desk", margin=0.05, min_side=896,
        full_prompt=SCENE + "Are banknotes or bundles of cash lying on the desk surface? " + FIXED
        + "Answer yes only if paper money is visible on the desk.",
    ),
    "bag_on_desk": QuestionSpec(
        DESK + "Is a cash-collection bag (a green fabric bag or sack, possibly sealed) lying on the desk? "
        "Answer no if the bag is only held in someone's hands.",
        roi="desk", margin=0.05, min_side=672,
        full_prompt=SCENE + "Is a cash-collection bag (a green fabric bag or sack, possibly sealed) lying "
        "on the desk? Answer no if the bag is only held in someone's hands.",
    ),
    "stamps_or_journal_on_desk": QuestionSpec(
        DESK + "Is any of these lying on the desk top: a rubber or self-inking stamp (a small cylinder), "
        "a seal, or a journal, register book, notebook or folder? " + DESK_FIXED
        + "Answer no for loose banknotes and bags.",
        roi="desk", margin=0.05, min_side=672,
        full_prompt=SCENE + "Is a rubber stamp, a seal, or a journal / register book (a bound log book) "
        "lying on the desk? " + FIXED + "Answer no for loose paper sheets, banknotes and bags.",
    ),
    "screen_on_unlocked": QuestionSpec(
        # robustness (newcam_combo X-11: blur + JPEG q30 erase the spreadsheet rows, leaving a bright
        # white screen that the old "rows visible" wording answered "no"): say that a bright, mostly
        # white/light window counts even when its text is unreadable
        "Look at the laptop. Is its screen showing an unlocked application window (e.g. a document, "
        "spreadsheet or form)? An application window looks bright and mostly white or light grey, "
        "possibly with rows or text; on a blurry or low-quality image the text may be unreadable, which "
        "still counts as yes. Answer no for a black or switched-off screen, a closed lid, or a colourful "
        "photo-like wallpaper / lock screen.",
        roi="laptop", margin=0.25, autocontrast=True,
    ),
    "safe_open": QuestionSpec(
        SCENE + "Look at the grey metal safe (a small steel cabinet). Is the safe door open, i.e. swung away "
        "from the safe body so that the dark inside of the safe or the edge of the door is visible? "
        "Answer no if the door is flat and closed.",
        roi="safe", margin=0.6,
    ),
    "key_in_safe": QuestionSpec(
        "Look closely at the lock on the door of the grey metal safe. Is a key inserted in the keyhole "
        "(a small key or a key with a tag or ring sticking out of the lock)? "
        "Answer no if the keyhole is empty.",
        roi="safe", margin=0.15, min_side=640,
    ),
    "cup_or_food": QuestionSpec(
        SCENE + "Is there a drink or food at the workplace: a cup, mug, coffee cup, bottle, food container "
        "or snack, on the desk or in someone's hand? " + FIXED,
    ),
    "handbag_or_coat_on_desk": QuestionSpec(
        DESK + "Is a personal belonging lying on the desk: a handbag, purse, backpack, or a piece of "
        "clothing such as a coat or jacket? Ignore green cash-collection bags and the fixed equipment.",
        roi="desk", margin=0.1, min_side=672,
        full_prompt=SCENE + "Is a personal belonging lying on the desk: a handbag, purse, backpack, or a "
        "piece of clothing such as a coat or jacket? Ignore green cash-collection bags and the fixed equipment.",
    ),
    "phone_visible": QuestionSpec(
        SCENE + "Is anyone holding or using a mobile phone (smartphone) in their hand? Ignore the desk "
        "telephone with a handset and cord and any devices standing on the desk.",
        multi="any",
    ),
    # T13 (PhoneRule): one narrow question per crop. phone_held: the request's roi = one person's box
    # (cashier / visitor near the desk); phone_on_desk: the calibrated desk crop (full frame without one).
    "phone_held": QuestionSpec(
        "Close-up of a person in a bank cash room, cropped from a ceiling security camera image. Is this "
        "person holding a mobile phone (a smartphone, often a small dark rectangle) in a hand? Answer yes "
        "even if the screen is not visible. A banknote, a stack of notes or a plastic card is not a phone; "
        "ignore the desk telephone with a handset and cord.",
        margin=0.2, min_side=672,
    ),
    "phone_on_desk": QuestionSpec(
        DESK + "Is a mobile phone (a smartphone: a flat dark glass rectangle) lying on the desk? It may be "
        "partly covered by banknotes or papers, so look for its edge too. " + DESK_FIXED
        + "Also ignore calculators, keyboards, card terminals and the desk telephone with a handset and cord.",
        roi="desk", margin=0.05, min_side=672,
        full_prompt=SCENE + "Is a mobile phone (a smartphone: a flat dark glass rectangle) lying on the desk "
        "or table? It may be partly covered by banknotes or papers, so look for its edge too. "
        + FIXED + "Also ignore calculators, keyboards, card terminals and the desk telephone with a handset "
        "and cord.",
    ),
    # T15 keys on "no" here. A desk/client-side crop with a card-vs-banknotes prompt was tried
    # (43b541a) and was WORSE on the PC full.analyze probe logs (video-level 7/13 vs 9/13: upscaled
    # notes in hands read as "card"), so the full-frame question stays.
    "id_document_shown": QuestionSpec(
        SCENE + "Is someone handing over, holding up or showing an identity document: a plastic ID card "
        "(credit-card size) or a passport booklet? Banknotes, stacks of cash, cash bags and paper "
        "slips are NOT identity documents - answer no if only money is being handed over.",
        multi="any",
    ),
    # per-frame veto for id_document_shown "yes" (T15): during a banknote hand-over the ID question
    # answers "yes" because notes in hand look like a card; this asks only about money in hands.
    # Crop = the people at the desk (visitor + cashier boxes, margin 0.3) upscaled to 896 px: on the
    # full frame the notes are a few pixels (PC test, 17 cases: full 10/17, hands crop 12/17)
    "banknotes_in_hand": QuestionSpec(
        BNH_PROMPT, roi="hands", margin=0.3, min_side=896, full_prompt=BNH_PROMPT,
    ),
    "counting_machine_used": QuestionSpec(
        SCENE + "Look at the black banknote counting machine. Is it being used: banknotes loaded in it, "
        "or a person putting banknotes into it or taking them out of it?",
        multi="any",
    ),
    "cash_in_hand_to_body": QuestionSpec(
        SCENE + "Look at the cashier's hands. Is the cashier holding banknotes close to the cashier's own "
        "body, at the chest, neckline, lap or clothing, or hiding banknotes in a closed hand below the "
        "desk, instead of handling them openly above the desk?",
        multi="any",
    ),
    "person_count": QuestionSpec(
        "This is a small cash room with a glass wall and a glass door; behind the glass is an open office. "
        "Count only the people who are physically INSIDE this room, on the camera's side of the glass. "
        "Do NOT count anyone seen through the glass wall or glass door (people in the office behind, "
        "people walking past or standing outside the door), and do not count reflections. "
        "If nobody is inside the room, answer 0.",
        kind="count",
    ),
}


def frame_labels(ts: list[float]) -> list[str]:
    return [f"Frame {i + 1} (t={t:.1f}s):" for i, t in enumerate(ts)]


MULTI_SUFFIX = " These frames are from the same camera at different moments. Answer yes if it is true in any frame."
