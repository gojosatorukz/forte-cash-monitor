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


DESK = "Close-up of a cashier's desk in a bank cash room, seen from a ceiling camera. "
DESK_FIXED = ("Ignore the fixed equipment: the banknote counting machine, the small banknote detector, "
              "the desk telephone and the laptop. ")

# Desk cues use the calibrated desk crop upscaled to 672 px: on full 464x848 frames a small cash stack
# or two stamps are a few pixels and the 4B model answers "no" (X-38 @30-34 s, X-24 @12 s).
SPECS: dict[str, QuestionSpec] = {
    "cash_on_desk": QuestionSpec(
        DESK + "Is there paper money lying on the desk top: a banknote, a small stack of notes or a cash "
        "bundle? Do not count notes inside the counting machine. " + DESK_FIXED,
        roi="desk", margin=0.05, min_side=672,
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
        "Look at the laptop. Is its screen showing an unlocked application (e.g. a spreadsheet, document, "
        "white window with rows) rather than a lock screen, wallpaper or black screen? "
        "Answer yes if application content is visible.",
        roi="laptop", margin=0.25,
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
        SCENE + "Is anyone holding or using a mobile phone (smartphone)? Ignore the desk telephone with "
        "a handset and cord.",
        multi="any",
    ),
    "id_document_shown": QuestionSpec(
        SCENE + "Is someone handing over, holding up or showing an identity document, i.e. a small "
        "card-sized white or plastic card or a passport? Answer no for banknotes.",
        multi="any",
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
