# VLM feasibility spike (Qwen3-VL on local GPU)

Date: 2026-10-17 · Session: forte-vlm-spike · Code: `spike/` · Raw outputs: `spike/results/*.jsonl|log`

## TL;DR
- **Qwen3-VL-4B-Instruct Q4_K_M runs on the RTX 3060 Laptop 6 GB** under llama.cpp `llama-server` (CUDA). VRAM is **4.4 GB** with 8k context and a q8 KV cache.
- **Focused single-cue questions on single frames work well.** Run them one question per call, with a yes/no or count JSON schema:
  - laptop unlocked vs lock screen: **8/8**
  - cash left on the desk with nobody present: **7/7**
  - people count: **8/8**
  - Frame-by-frame timelines line up with the labels. The X-30 count goes to 2 at exactly 13 s, which is the labeled second.
  - Latency is **~0.6–1.8 s per call**.
- **What does not work on 4B:**
  - **Door open/closed** gets answered "open/ajar" on almost every frame, whether we send a full frame, a crop, a closed-door reference pair, or a geometric prompt. → Use CV for the door, or 8B (see the 8B section).
  - **Holistic multi-frame classification** with 10 frames and a fixed type list: the model calls nearly everything `none`. Verdict accuracy was 3/7 at 320 px and 4/7 at 464 px.
  - **One combined 7-field "state" JSON per frame** is much worse than separate focused questions. With it, cash was never detected and the laptop was mostly `not_visible`.
- **8B (16 GB tier) is not better.** Qwen3-VL-8B Q4_K_M, tested for accuracy with partial offload on 6 GB, matched or underperformed 4B on every cue. It also fails the door cue and segment classification. → **Use 4B on both machines** and re-measure speed on the 5080. Details are in the 16 GB section.
- **Recommendation for the pipeline:**
  - CV (detector + tracker + change detection) proposes the moments and the people count.
  - The VLM then answers **one narrow yes/no question per cue on 1–3 keyframes**, using either the full frame or an upscaled ROI crop.
  - Do **not** ask the VLM to classify a whole segment.
  - Door state comes from CV, as an ROI diff against an empty-scene or closed reference.

## Setup (exact commands)
There is no CUDA toolkit on this laptop, so we use the prebuilt llama.cpp CUDA 12.8 release, which bundles cudart.
```bash
cd spike
B=b11707
curl -LO https://github.com/ggml-org/llama.cpp/releases/download/$B/llama-$B-bin-ubuntu-cuda-12.8-x64.tar.gz
curl -LO https://github.com/ggml-org/llama.cpp/releases/download/$B/cudart-llama-$B-bin-ubuntu-cuda-12.8-x64.tar.gz
mkdir -p bin && for f in *.tar.gz; do tar -xzf $f -C bin; done
# weights: official Qwen repo, Apache-2.0 (license: apache-2.0 in model card front-matter)
R=https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct-GGUF/resolve/main
./pdl.sh $R/Qwen3VL-4B-Instruct-Q4_K_M.gguf models/Qwen3VL-4B-Instruct-Q4_K_M.gguf 2497281664 8
./pdl.sh $R/mmproj-Qwen3VL-4B-Instruct-F16.gguf models/mmproj-Qwen3VL-4B-Instruct-F16.gguf 836180256 4
./run_server.sh          # 127.0.0.1:8090, OpenAI-compatible /v1/chat/completions
uv venv .venv && uv pip install --python .venv/bin/python pillow requests
.venv/bin/python test_single.py both     # single-frame cue tests
.venv/bin/python test_timeline.py         # focused question on every second of clip pairs
.venv/bin/python test_segment.py 10 464 plain
```
Server flags (see `run_server.sh`): `-ngl 99 -c 8192 -ctk q8_0 -ctv q8_0 -np 1 -fa on --temp 0 --host 127.0.0.1`.
- Do not use `-c 16384`: it ran **out of memory** on 6 GB. The f16 KV cache needs about 2.4 GB at 16k, and the weights plus mmproj take about 3.3 GB.
- Bind to localhost only. Nothing is exposed publicly.

Client: `spike/vlm.py`.
- Images go in as base64 `image_url` parts.
- Frame labels go in as interleaved text parts ("Frame 3 (t=4.5s):").
- Output is forced with `response_format: {type: json_schema, json_schema: {schema}}`, which llama-server enforces as a grammar.

## Results: Qwen3-VL-4B Q4_K_M, RTX 3060 Laptop 6 GB
Ground truth comes from `docs/videos/batch_C.md`, checked by eye on frame montages. Each frame is 464×848, which is about 450 image tokens.

### (a) Single-frame focused questions (`test_single.py`)
| Cue | Full frame | ROI crop (upscaled) | Latency per call (full) |
|---|---|---|---|
| Laptop shows application vs lock screen/wallpaper (X-28 vs X-29) | 8/8 | 8/8 | ~1.2–1.4 s |
| Banknotes on desk, nobody present (X-26 vs X-27, plus X-26 with cashier present) | 7/7 | 7/7 | ~1.1–1.8 s |
| People count (X-30 = 2, X-31 = 1, empty = 0) | 8/8 | n/a | ~0.8 s |
| Glass door open/ajar (X-32/X-30 open vs X-31/X-32@0 closed) | 3/9 (3 JSON truncated) | 5/9 | 1.5 s |

Timelines (`test_timeline.py`): one focused question per second over the clip pairs, one character per second from t=0. Results were identical for full frames and crops except for the laptop row on X-29.
```
cash   X-26: nnnnnnnnnnnnnyyyyyyy        (cashier stands at 9 s, exits ~15 s → fires from 13 s)
cash   X-27: nnnnnnnnnnnnnnnnnnnnnnnnnnn (clean: never fires)
laptop X-28: yyyyyyyyyyyyyyyyy           (unlocked whole clip)
laptop X-29: yyyyyynynnnnnnnynn  full    (locks at ~6 s; 2 flickers)
laptop X-29: yyyyyynnnnnnnnnnnn  crop    (perfect with 3x laptop ROI crop)
count  X-30: 0001111111111222222         (2 people from 13 s = labeled second)
count  X-31: 000000011111111111111111    (never >1)
door   X-32: yyyyyyyyyyyyyyyyyyyyyy      (says open always, incl. t=0 when closed)
door   X-31: yyyyyyyyyyyyyyyyyyyyyyyy    (clean clip, door closed most of the time → all FP)
```
Latency for focused questions on full frames: median **1.43 s**, mean 1.33 s per call over 171 calls. By question, the median/max was:
- count: 0.55/0.67 s
- laptop: 1.15/4.0 s
- door: 1.5/1.8 s
- cash: 1.77/3.5 s

Count is fastest because its answer is short. Most of the time goes to output tokens: the `reason` field runs 30–50 tokens. Drop `reason` in production, or put a `maxLength` on it.

During the crop run the host load average was about 68 because other jobs were on the CPU, and per-call outliers reached 26–70 s. So treat the crop-run mean (5.1 s) as noise. Its median of 1.95 s is the meaningful number.

### Door attempts that failed (`test_door.py`)
- **Reference pair:** a closed-door reference crop plus the current crop, asking "same as A or open?". Score 8/13, and it still said "open" on 4 of 5 truly closed frames.
- **Geometric single-frame prompt:** "closed = in the plane of the neighbouring panels, open = rotated, with a gap". Score 7/13, and it answers "open" on every frame.
- **The visual cue is real but subtle.** An open door shows a dark vertical band in the left glass panel; a closed one shows the office behind it. The 4B model does not pick it up. → Use **CV**: ROI difference against the first frame or the median background in the door polygon, combined with the person track leaving the door zone.

### (b) Multi-frame segment classification (`test_segment.py`)
Setup: 10 frames sampled uniformly over the clip, interleaved with time labels, a fixed type enum (`cash_left_unattended`, `pc_left_unlocked`, `door_left_open`, `unauthorized_person`, `safe_left_open`, `other_suspicious`, `none`) and JSON output.

| Frames × width | Prompt tokens | Latency | Verdict acc | Type acc |
|---|---|---|---|---|
| 10 × 320 px | ~2.2k | 2.8 s | 3/7 (says `none` for every clip) | 3/7 |
| 10 × 464 px | ~4.5k | 5.1 s | 4/7 (only X-26 cash detected) | 4/7 |

The model writes plausible but wrong summaries, for example "laptop is closed, door is closed". **It is not usable as the classifier.** It is usable only as a description writer once the type is already known.

### (c) Combined per-frame state JSON (`test_state.py`, 147 frames at 1 fps)
We asked for all 7 fields in one schema per frame: people, cashier_at_desk, cash_on_desk, laptop_screen, safe_door, glass_door and person_at_entrance. This took 1.53 s median per frame but quality collapsed:
- cash_on_desk was never true on X-26.
- laptop_screen was `not_visible` on almost all frames, and the X-29 lock screen came back as `application_content`.
- The X-30 people count reached 2 only once.

→ **One cue per call** is the key prompting rule for the 4B model.

## Recommended prompting (4B)
1. **One cue per call.** Use a short yes/no question in plain words that names the object and what counts as yes. Use the schema `{"answer": "yes"|"no", "reason": str(maxLength 200)}`, or `{"num_people": int, "people": [str]}` for counts.
2. Say what to **ignore**: "Ignore people seen through the glass in the office behind" and "Ignore the counting machine, phone and laptop" (for the cash question).
3. **ROI crops help on small objects.** The laptop crop was upscaled 3×. Crop boxes need to come from detections, not hard-coded pixels, because the final test uses a different camera angle.
4. `temperature 0` with a JSON schema always gave valid JSON, except when the free-text `reason` hit `max_tokens`. Cap `reason` with `maxLength`.
5. Use the VLM to **write the RU description** after CV and the focused questions have fixed the type, giving it 1–3 keyframes.

## Budget estimate (6 GB laptop, 4B)
A 145 s video sampled at 1 fps is 145 frames. Running 3 focused questions on every frame would be about 145 × 3 × 1.3 s ≈ 9.4 min, which is **too slow**. The VLM has to run on CV-proposed keyframes only: for example, 10 candidate moments × 3 questions × 1.3 s ≈ 40 s. That fits the 5-minute limit.

## Image-token setting (applies to both models)
llama-server warns: *"Qwen-VL models require at minimum 1024 image tokens ... try `--image-min-tokens 1024`"*. By default our 464×848 frames come to about 450 tokens.

With 4B plus `--image-min-tokens 1024` on full frames:
- Overall: **27/32** (default setting: 26/32).
- laptop 7/8, cash 7/7, count 8/8, door 5/9.
- Median latency 2.0 s per call; prefill is about 1.1k tokens per image.

The difference is within noise for 4B. It matters for 8B (next section).

## 16 GB tier: Qwen3-VL-8B-Instruct Q4_K_M
Weights: official `Qwen/Qwen3-VL-8B-Instruct-GGUF`, Apache-2.0.
- `Qwen3VL-8B-Instruct-Q4_K_M.gguf`: 5.03 GB
- `mmproj-Qwen3VL-8B-Instruct-F16.gguf`: 1.16 GB
- Q8_0 (8.7 GB) is also available. It was not downloaded because the link here is ~3 MB/s.

On the 6 GB laptop the model does **not fit** fully on the GPU: a 1.1 GB cudaMalloc failed with OOM. We tested it for **accuracy only**, with partial offload (`-ngl 20–22`, about 5 GB VRAM). Speeds measured this way say nothing about the 5080.

| Test | 4B Q4 (default tokens) | 8B Q4 (default tokens) | 8B Q4 + `--image-min-tokens 1024` |
|---|---|---|---|
| Single-frame full: laptop / cash / count / door | 8/8 · 7/7 · 8/8 · 3/9 | 6/8 · **4/7** · 8/8 · 4/9 | 5/8 · 7/7 · 8/8 · 3/9 |
| Single-frame ROI crop: laptop / cash / door | 8/8 · 7/7 · 5/9 | — | 8/8 · 7/7 · 3/9 |
| Door reference pair / geometric prompt (13 cases) | 8/13 · 7/13 | 7/13 · 7/13 (says "open" for every frame) | — |
| 10-frame segment classification (464 px) | 4/7 | 4/7 (finds the X-30 outsider at 12.5 s; misses cash, PC and door) | — |

**Conclusion: on these cues 8B Q4 is not better than 4B Q4.**
- At default resolution 8B is worse on cash: it said "no banknotes visible" three times.
- It reads the X-29 lock-screen wallpaper as an "application".
- Neither model can see the door state.
- Neither model can do holistic segment classification.

### Expected speed on RTX 5080 16 GB (estimate, NOT measured)
Measured on the 3060 Laptop with 4B fully on GPU (llama-server timings, median): **prefill ≈ 1,100 tok/s, decode ≈ 61 tok/s**. In practice:
- One focused question (≈450 image tokens + ≈40 output tokens) takes about 1.2–1.5 s.
- With 1024-token images it takes about 2.0 s.

For the 5080 (Blackwell sm_120): the prebuilt `llama-b11707-bin-ubuntu-cuda-12.8-x64` release **contains `sm_120` kernels**, so the same binary should work. Use cuda-12.8 or the 13.x builds; the driver must be ≥ 570.

The 5080 has roughly 2.5–3× the memory bandwidth and several times the compute of a 3060 Laptop. On that basis, expect:
- 4B: about 3× faster decode, about 0.4–0.6 s per focused question.
- 8B Q4/Q8 fully on GPU: about 0.8–1.5 s per focused question.

Fit on 16 GB:
- 8B Q4: about 6.2 GB plus KV.
- 8B Q8: about 9.9 GB plus KV. It fits, and a 16k context also fits.
- 8B can run alongside the CV detector.

All of these are extrapolations. **Re-measure on the PC** with `spike/test_single.py` (it prints per-call `wall_s`).

## Recommendation
1. **Primary VLM: Qwen3-VL-4B-Instruct Q4_K_M** with `--image-min-tokens 1024`, on llama-server, on both the 6 GB laptop and the 5080.
   - It matched or beat 8B on every cue we tested.
   - It is 3–4× cheaper, so on the 5080 there is headroom for more keyframes or more questions per video.
   - On the 5080, consider **4B Q8_0 or 8B Q8_0** only if a re-check on the PC shows a gain. Q4 quantization of 8B may be part of why 8B underperformed; that is untested.
2. **The VLM is a verifier for narrow questions, not the detector.** CV and rules decide when to look and what to ask. The VLM answers one yes/no or count question per call on 1–3 keyframes, using a full frame or an upscaled ROI crop.
   - Cues that work: laptop unlocked vs locked, cash on the desk with nobody present, people count/roles, and (8B) an outsider entering.
   - Cues that need CV: door open/closed, and any "absence of a step" logic such as safe not opened or door not closed.
3. Use the VLM to **write the RU description** once the type is fixed. Never let it choose the citation, as already decided.
4. Latency budget per 145 s video on the 3060: ~10 candidate moments × 3 questions × ~1.5–2 s ≈ 45–60 s. On the 5080 this should be several times less.

## Files
- `spike/run_server.sh`: server launcher. `EXTRA` adds flags (for example `EXTRA="--image-min-tokens 1024"`); arguments 1 and 2 override the model and mmproj.
- `spike/vlm.py`: client.
- Tests: `test_single.py`, `test_timeline.py`, `test_door.py`, `test_segment.py`, `test_state.py`.
- `spike/results/`: all raw outputs. Files with the `_8b` / `_mt1024` suffixes are the 8B runs and the 1024-token runs.
- Models: `spike/models/`, about 9.5 GB, not in git.
