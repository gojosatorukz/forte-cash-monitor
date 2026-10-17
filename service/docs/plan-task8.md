# Task 8: Evidence boxes + evidence clips

Orchestrator request, after the organizer clarifications in `/home/datamata/Projects/datathon2026/docs/decisions.md` (end of file).

**Base.** The branch is fast-forwarded to service main `441bdd4`. That includes the forte-deploy commits: job `warnings`, `legal_strength`, VLM health, and the `forte-detector` editable dependency.

**Environment.** From the worktree, `../detector` is reached through a symlink, so `uv sync` and `uv run` work.

## (a) Bounding boxes

**Input from the analyzer.**
- `EvidenceFrame` gains an optional `boxes: [{x, y, w, h, label?, kind?}]`.
- Coordinates are normalized 0..1 relative to the evidence JPEG the analyzer sends. That JPEG is already upright.
- `kind` is one of `"person"`, `"object"`, `"region"`. A missing kind means `"object"`.
- Boxes that are non-finite or have w/h ≤ 0 are dropped. Coordinates are clamped into [0, 1].

**Drawing.** The service draws once, with Pillow (`uv add pillow` as a direct dependency).
- Outline: 3 px. Green (#1DB954) for object/region, red (#E53935) for person.
- Label chip: a filled rectangle in the outline colour with white text.
- Font: bundle `app/assets/fonts/DejaVuSans.ttf` (copy from `/usr/share/fonts/TTF/DejaVuSans.ttf`) together with its permissive license in `app/assets/fonts/LICENSE-DejaVu.txt`.
- Font size is about 2.2% of image height, minimum 12 px. Labels are truncated to about 32 characters.
- A missing label means no chip. The service never invents labels.
- No face identification.

**Output.** The public schema stays unchanged: each frame is `{timestamp_sec, image_base64}`.
- Take the first analyzer frame that has boxes:
  - `evidence_frames[0]` = its annotated version.
  - `[1]` = the raw frame at the same timestamp.
  - Then the remaining analyzer frames follow.
- Cap the total at 3 frames.
- Frames without boxes pass through unchanged.
- Extra per-frame fields: `annotated: bool`, `boxes` (the normalized boxes, echoed back).
- A drawing failure never fails the job: log it and fall back to the raw frames.

## (b) Evidence clips

**Cutting.** After analysis finishes (normal or partial), cut one H.264 MP4 per violation.
- Window: `[start_sec − 2, end_sec + 2]`. `start_sec` defaults to `timecode_sec`; `end_sec` defaults to `start_sec`.
- Duration is clamped to 3–8 s. If the window is longer than 8 s, centre it on `timecode_sec`. Clamp everything to the video's bounds.
- Encoding:
  - scale to height 480, keeping the aspect ratio with even dimensions;
  - flags `-an -movflags +faststart -preset veryfast -crf 28 -pix_fmt yuv420p`;
  - `-ss` before `-i` is fine, because the clip is re-encoded.

**Rotation.** ffmpeg autorotates by the container tag.
- The analyzer may also report `AnalysisResult.meta["applied_rotation_deg"]` (0/90/180/270). This is extra clockwise rotation beyond the tag.
- Apply it so the clips match the frames:

  | applied_rotation_deg | ffmpeg filter |
  |---|---|
  | 90 | `transpose=1` |
  | 270 | `transpose=2` |
  | 180 | `hflip,vflip` |

- If there is no meta (timeout or partial), assume 0. Document this in the README.

**Box overlay.** No overlay in clips.

**Storage.** `DATA_DIR/clips/<job_id>/<n>.mp4`, where `n` is the 1-based index of the violation in the final list.

**Budget.** Clips are cut inside the job's remaining hard budget.
- Run them in parallel with a ThreadPoolExecutor, `max_workers = min(4, n)`.
- Each subprocess timeout = remaining time to the hard deadline − 1 s. Kill ffmpeg on timeout.
- If fewer than 2 s remain, skip all clips.
- A failed or skipped clip adds a job `warnings` entry, using the existing warnings mechanism. It never fails the job and never exceeds the hard deadline.
- The job becomes `done` only after its clips are finished or skipped.

**Fields.**
- Each violation gets `clip_url` only when its clip exists.
- Public API: `/api/v1/jobs/{id}/clips/{n}.mp4`.
- UI payload: `/ui-api/jobs/{id}/clips/{n}.mp4`.

**Routes.**
- `GET|HEAD /api/v1/jobs/{id}/clips/{n}.mp4`: API key required.
- `GET|HEAD /ui-api/jobs/{id}/clips/{n}.mp4`: no key.
- Range support (reuse the video route's helper), content type `video/mp4`.
- 404 for an unknown job or clip. `n` must be a positive int (no traversal).

## (c) UI

Each violation card that has `clip_url` gets an inline `<video muted loop playsinline preload="metadata">`.
- Poster: a data URL of `evidence_frames[0]`.
- Autoplay while the card is visible (IntersectionObserver); pause when it isn't.
- Controls on hover or focus.
- Without a clip, fall back to the frame thumbnails.

Keep the existing timeline seek and the timecode button. Annotated thumbnails get a «Разметка» badge.

## (d) Stub

With `STUB_DEBUG`, the stub's demo violation has 2 frames. The first carries one box: `kind: "region"`, label «Зона кассы (демо)».

## (e) Tests

- **Boxes:** validation and normalization.
- **Drawing:**
  - Output is a different JPEG with the same dimensions.
  - Ordering is annotated first, then raw.
  - The cap of 3 frames holds.
  - A Cyrillic label renders, and the font file is present.
  - A drawing failure falls back to raw frames.
- **Clip window math:** clamps, centring, video bounds.
- **STUB_DEBUG run:** produces a clip. Check it with ffprobe: h264, no audio stream, height ≤ 480, duration 3–8 s.
- **Rotation:** `applied_rotation_deg=90` gives a clip with swapped dimensions.
- **Budget:** a tiny budget skips clips with a warning, and the job still finishes `done` within the cap.
- **Routes:**
  - The `/api/v1` clip route requires the key.
  - A Range request returns 206.
  - An unknown `n` returns 404.
  - A traversal attempt is rejected.
- **Manual check:** run `/home/datamata/Projects/datathon2026/robust/rot90cw/videos/Х-10.mov` and record the result in the report.
