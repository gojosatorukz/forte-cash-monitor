# Tech options — models, CV pipeline, stack (researched 2026-10-17)

Hardware baseline: **RTX 3060 Laptop, 6 GB VRAM** (verified with `nvidia-smi`, driver 610.57). Input: 464x848 portrait .mov, 30 fps, 10–145 s, single overhead camera. Budget is 5 min per video; we aim for under 2 min.

Status legend: **OK** means usable as-is. **CHECK** means usable, but read the license terms first. **NO** means do not use: non-commercial, (A)GPL, or a research-only license.

---

## 0. TL;DR recommendation

1. **VLM:** **Qwen3-VL-4B-Instruct** (Apache-2.0, Oct 2025) or **Qwen3.5-4B** (Apache-2.0, Mar 2026, native multimodal). Run it as GGUF Q4_K_M plus the mmproj file under **llama.cpp `llama-server`**, which gives an OpenAI-compatible local endpoint. It needs about 3.5–4.5 GB VRAM. Fallback: Qwen3-VL-2B or Qwen3.5-2B. If we get a 16–24 GB GPU, switch to Qwen3-VL-8B or Qwen3.5-9B: AWQ under vLLM, which accepts native video input with timestamps.
2. **CV front-end (no Ultralytics):**
   - Person detection: **D-FINE-S/M** or **RT-DETRv2** (Apache), or **RF-DETR Nano–Large** (Apache, not XL/2XL).
   - Tracking: **ByteTrack** (MIT, via `supervision`, which is also MIT).
   - Pose: **RTMO / RTMPose / RTMW** via **`rtmlib`** (Apache, ONNX, no mmcv).
   - Object zones: **Grounding DINO / MM-Grounding-DINO** or **OWLv2** (Apache), or **Florence-2** (MIT), prompted with "safe", "desk", "door", "banknote counting machine", "bag".
3. **Hybrid pipeline:** decode at 2–4 fps, then detect and track people, auto-calibrate zones on keyframes, compute angle-invariant event features, and propose candidate segments. A VLM verifies and classifies each segment into a **fixed taxonomy** (plus `other_suspicious`). Each type then maps to a **local rule KB** (`regulations.md`), and **only KB citations are emitted**, so the model never writes a citation itself. The CV stage also picks the evidence frames.
4. **Stack:** FastAPI with one in-process GPU worker (asyncio queue plus a thread), job state in a dict or SQLite, and a static HTML/JS UI served by the same FastAPI app. Make it public with a **cloudflared named or quick tunnel**: 100 MB body limit (we need 29 MB), no SSE on quick tunnels, so use polling.
5. **Time:** about 40–100 s per 145 s video on 6 GB (section 5).

---

## 1. Local VLMs

| Model | Params | License | Commercial | 6 GB fit (Q4) | Video input | Russian | Notes |
|---|---|---|---|---|---|---|---|
| **Qwen3-VL-2B / 4B / 8B-Instruct** | 2/4/8B dense | Apache-2.0 | **OK** | 2B: about 2 GB. 4B: about 3.5 GB with mmproj. 8B: about 6 GB, too tight | Native in transformers/vLLM (timestamp-aligned, "second-level indexing"). In llama.cpp/Ollama, pass frames as multiple images | Good (Qwen is strong in RU) | GGUF in llama.cpp since 2025-10-30. Unsloth fine-tuning supported. **Primary pick.** |
| **Qwen3.5-0.8B / 2B / 4B / 9B** | dense, Gated-DeltaNet hybrid | Apache-2.0 | **OK** | 4B: about 3.5 GB. 9B Q4_K_M: about 5.3 GB weights + KV, too much on 6 GB | Native text + image + video | Good | Released 2026-03-02. Day-1 vLLM and llama.cpp support. Hybrid attention, so verify our llama.cpp build handles it. Strong alternative to Qwen3-VL-4B; A/B test both on 5 clips. |
| Qwen2.5-VL-3B | 3B | **Qwen Research License** | **NO** | – | – | – | Non-commercial. 7B is Apache, but superseded by Qwen3-VL. |
| Gemma 4 E2B / E4B | 2.3/4.5B effective | Apache-2.0 (new with Gemma 4, 2026-04) | **OK** | Yes | E2B/E4B take image + audio (no audio allowed for us). Video only on 26B/31B (up to 60 s) | OK | Use frames as images. Weaker on temporal reasoning than Qwen. |
| Gemma 3 4B | 4B | Gemma Terms of Use | **CHECK** (commercial allowed under the prohibited-use policy; not OSI) | Yes | Images only | OK | Prefer Gemma 4 or Qwen. |
| InternVL3.5-1B/2B/4B/8B | 1–8B | Code MIT; weights inherit the LLM license (Qwen3-based, Apache) | **OK** (check each card) | 4B: yes | Multi-frame | Medium | Good, but less tooling (GGUF) than Qwen. |
| MiniCPM-V 4.5 | 8B | Code Apache; weights under the **MiniCPM Model License** (free commercial use *after a registration questionnaire*) | **CHECK** | Q4: about 5 GB, tight | Strong video (3D-resampler, high-fps) | Medium | Registration is friction; avoid unless needed. |
| SmolVLM2 (256M/500M/2.2B) | ≤2.2B | Apache-2.0 | **OK** | Yes, easily | Yes | **Weak** | Fast, but English-centric and weak on fine actions. Possible cheap pre-filter. |
| Molmo 2 (4B/8B) | 4–8B | Apache-2.0 (weights) | **OK** (check data terms) | 4B: maybe | Video **pointing and tracking** | Medium | Interesting for localization; tooling is less mature. |
| LLaVA-OneVision(-1.5) | 0.5–8B | Apache-2.0 | **OK** | Yes | Yes | Medium | Superseded by Qwen3-VL. |
| Llama 3.2 Vision 11B | 11B | Llama Community License | **CHECK** | No | Images | OK | Too big; skip. |

**Serving on 6 GB**
- **llama.cpp `llama-server`** (MIT): the best fit. Flags: `-m Qwen3-VL-4B-Instruct-Q4_K_M.gguf --mmproj mmproj-F16.gguf -ngl 99 -c 16384`. It exposes an OpenAI-style `/v1/chat/completions` with base64 images and supports a JSON grammar (`response_format` / `--json-schema`), so output is forced into our schema. It sends frames as images and has no temporal token merge, so keep frames small (about 336x616) and use 8–16 frames per call.
- **Ollama** (MIT): it wraps llama.cpp and is easiest to install. It has fewer knobs (grammar via `format` JSON schema works). Qwen3-VL is available in the Ollama library.
- **vLLM** (Apache): has native `video` input for Qwen3-VL/Qwen3.5 with temporal 2x merge and absolute timestamps. That is the best quality and throughput, but vision encoder plus KV plus CUDA graphs does not fit comfortably in 6 GB. **Use it on a 24 GB box** (Qwen3-VL-8B-AWQ or Qwen3.5-9B-AWQ, `--max-model-len 32768`, `--limit-mm-per-prompt`).
- **transformers + bitsandbytes 4-bit:** simplest Python path with native video. It is slower, but fine for a few calls per video. Good fallback if llama.cpp has trouble with Qwen3.5.

**Token math (Qwen3-VL, 16 px patches, 2x2 merge = 32 px per token):**
- A full 464x848 frame is about 15x27 = **about 400 tokens**. Downscaled to 320x576, it is about 10x18 = **180 tokens**.
- Video mode in transformers/vLLM also merges frame pairs, halving that.
- 16 frames at 320x576 is about 3k tokens. On a 3060 Laptop, 4B Q4 prefill is roughly 1–2k tok/s and decode about 40–60 tok/s, so **about 5–8 s per call**.

**Prompting rules:** use a Russian or English system prompt, require strict JSON, and enumerate allowed `type` codes from the taxonomy plus `other_suspicious` and `none`. Ask for `frame_index` instead of free-form seconds, then map the index to seconds ourselves. Burn a small timestamp into each frame (as in `work/sheets`) to help temporal grounding.

---

## 2. Classic CV components

### Detection (people, bags, objects)
| Model | License | Status |
|---|---|---|
| Ultralytics YOLOv5/v8/11/YOLO26, YOLOE, YOLOv10 | **AGPL-3.0** | **NO.** This is the trap the organizers flagged; it also covers `ultralytics` pip trackers. |
| YOLOv7 / YOLOv9 (WongKinYiu) | GPL-3.0 | **NO** (copyleft; avoid) |
| YOLO-World (Tencent) | **GPL-3.0** | **NO** |
| YOLO-NAS (Deci) weights | non-commercial | **NO** |
| **RT-DETR / RT-DETRv2** (lyuwenyu, also in HF transformers) | Apache-2.0 | **OK** |
| **D-FINE**, **DEIM / DEIMv2** | Apache-2.0 | **OK.** Fast and accurate, COCO "person" works out of the box, ONNX export. |
| **RF-DETR** Nano/S/M/L (Roboflow) | Apache-2.0 | **OK.** *XL/2XL use the PML-1.0 license: avoid.* |
| YOLOX (Megvii) | Apache-2.0 | **OK** (older, small, ONNX; it is the default detector in rtmlib) |
| RTMDet (OpenMMLab) | Apache-2.0 | **OK** (mmdet dependency is heavy; use the ONNX build via rtmlib) |

**Recommended:** run **D-FINE-S** or **RT-DETRv2-S** via HF transformers for persons and COCO "handbag/backpack/suitcase/cell phone/laptop". Alternatively use rtmlib's bundled YOLOX + RTMO ONNX for one dependency-light package.

### Tracking
**ByteTrack** (MIT) via `supervision` (MIT) is enough: one room and 1–4 people. Do **not** add ReID or face embeddings, since identification is forbidden. Track IDs are anonymous and exist only for the session.

### Pose / hands
**rtmlib** (Apache-2.0; ONNXRuntime; models RTMPose, RTMO body-17, RTMW whole-body-133 incl. hands, DWPose). Use wrist keypoints for:
- hand-over-desk or hand-in-safe events
- hand-to-hand handover between two tracks (cash transfer without the counter)
- hands under the desk or out of view
- hand-to-pocket

Body pose is coarse from an overhead view, but it still yields usable wrists.

### Open-vocabulary detection (zone auto-calibration, objects of interest)
| Model | License | Use |
|---|---|---|
| **Grounding DINO** (IDEA) / **MM-Grounding-DINO** (OpenMMLab) | Apache-2.0 | Text prompts such as "safe . cash counting machine . desk . door . bag . banknotes". HF transformers support. About 0.3–0.5 s per frame on a 3060. |
| **OWLv2** (Google) | Apache-2.0 | Text or image-exemplar queries; good for "find this safe in a new angle" using an exemplar crop from training frames. |
| **Florence-2** (Microsoft) | MIT | Fast (0.23B/0.77B), with captioning, OD and grounding. Good all-rounder. |
| YOLO-World | GPL-3.0 | **NO** |
| SAM 2 | Apache-2.0 | **OK** for masks of safe and door (to detect "open/closed" via mask area change). |
| SAM 3 | Meta **SAM License** (custom) | **CHECK** (custom terms). Not needed. |

### Action recognition / few-shot (about 41 clips, labels are only yes/no plus one second)
- **VideoMAE** (MCG-NJU weights): **CC-BY-NC-4.0, NO.** **V-JEPA 2**: reported **CC-BY-NC, NO**. Avoid both.
- **X-CLIP** (MIT, HF transformers): zero-shot text-video scoring. Weak on fine, domain-specific actions.
- **SigLIP 2** (Apache) or OpenCLIP (MIT) **frame embeddings, plus a logistic regression or kNN** trained on windows around the labeled second (positive) versus clean-clip windows (negative). Train with leave-one-video-out. It is cheap (about 1 s per video) and works as a **third, learned, vote** for the verdict and as a "something unusual here" saliency score. It does not give a type.
- **LoRA fine-tune of Qwen3-VL-2B with Unsloth:** technically possible on 6 GB, but **not recommended** under the 12:00 deadline. With no type labels, we would have to write those labels ourselves first.
- Generic temporal tricks that need no training:
  - motion energy per zone (frame differencing inside the safe, door and desk polygons)
  - person count over time
  - dwell time per zone
  - "cashier absent while cash or safe exposed"

These give the timecodes, and they work on unknown violation types as "anomaly" proposals.

---

## 3. Recommended hybrid architecture

```
.mov ──ffmpeg/PyAV decode @ 4 fps (320x576 for VLM, 640 for detector)
  │
  ├─[A] Scene calibration (first ~5 keyframes, median):
  │     Grounding-DINO/Florence-2: safe, desk, cash counter, door, laptop
  │     → zones as polygons relative to detected object boxes (NOT fixed pixels)
  │
  ├─[B] Per-frame CV (4 fps): D-FINE/RT-DETR persons+bags+phones → ByteTrack
  │     → rtmlib pose (wrists) on person crops
  │
  ├─[C] Event features (angle-invariant; distances normalised by person-box height):
  │     n_persons(t), person-in-zone(safe/desk/door), entries/exits via door zone,
  │     cashier-absent(t) [desk zone empty] while counter/safe/bag present,
  │     wrist-in-safe / wrist-near-other-person's-wrist (handover),
  │     bag appears/disappears, phone near head/hands, motion energy per zone,
  │     SigLIP-2 kNN anomaly score
  │
  ├─[D] Candidate segments = rule triggers ∪ top-k anomaly peaks ∪ coarse uniform
  │     windows (safety net: whole video in 4–6 windows so nothing is unseen)
  │
  ├─[E] VLM verify+classify per segment (Qwen3-VL-4B, 8–16 frames, JSON schema):
  │     {is_violation, type ∈ TAXONOMY ∪ {other_suspicious, none}, confidence,
  │      key_frame_index, description_ru}
  │     + CV context injected as text ("2 persons, cashier left desk at 34.5 s")
  │
  ├─[F] Fusion: score = w1·rule + w2·VLM_conf + w3·kNN; thresholds tuned on the
  │     41 clips for recall≈1 then minimal FP; merge overlapping events (NMS in time)
  │
  └─[G] Output: type → KB lookup (rule_id, document, point, quote) — citations come
        ONLY from KB; timecode = key frame; evidence = 1–3 frames (peak ± 1 s)
        with drawn boxes/zones (no faces blurred-out needed, but no identities).
```

### Taxonomy and citations (anti-hallucination)
- Keep `rules_kb.yaml` with entries of the form `type_code, title_ru, visual_criteria, doc_name, doc_point, url(adilet), quote`. Build it from `regulations.md`.
- The VLM may only output a `type_code` from the enum. The JSON grammar enforces this, and the enum list goes into the prompt with short visual criteria.
- `other_suspicious` maps to a generic KB entry (general cash-room security or internal-control requirement) and a generic RU description. This covers **unknown types** without inventing a rule.
- Never let the VLM write a document name or point number. Fill those from the KB after classification.

### Robustness to a different camera angle (final test)
- **No hard-coded pixel zones.** Derive zones each video from open-vocab detections: safe box, desk box (+ margin), door box (or the glass-door region from a "door" prompt). Express everything as box IoU or overlap and as distances normalized by person height.
- **Fallbacks when calibration fails** (object not found at confidence ≥ 0.3):
  - Drop the rules that depend on the missing zone. Do not error out.
  - Lean on the VLM and the uniform-window safety net.
  - Log it in the job result (`"calibration": {"safe": null}`). This is good for the product score.
- Detectors, pose, tracker and VLM are all viewpoint-agnostic. The **kNN head is the most viewpoint-fragile**, so give it the lowest weight.
- Quick self-test: re-run the 41 clips after a horizontal flip, a 10–15% crop/zoom and a ±10° rotation. This approximates a camera change and catches hidden pixel assumptions.

### False-positive control while keeping recall
- Two-stage: a CV trigger alone gives at most a "weak" flag, and the VLM must confirm it. Exception: a high-confidence hard rule (e.g. 3+ people present where only 2 are allowed) can fire alone.
- For the verdict, tune thresholds so that **all 22 positive training clips fire** (leave-one-out), then pick the highest threshold that keeps that recall.
- Merge events closer than about 5 s that share a type. Report at most about 3 events per video.

---

## 4. Backend / UI stack

- **FastAPI + uvicorn** (MIT/BSD). Endpoints:
  - `POST /api/v1/analyze`: multipart `file` plus `X-API-Key`. Save the file to disk and return `{job_id}` immediately.
  - `GET /api/v1/jobs/{id}`: return the job state.
  - Optionally `GET /api/v1/rules`, which exposes the KB (good for the product and rule-grounding scores).
- **Job queue:**
  - Use an in-process `queue.Queue` plus **one GPU worker thread**. Do not use Celery or Redis; that is overkill.
  - Keep job state in a dict, persisted to `jobs/{id}.json` so a restart survives.
  - Load the models once at startup and keep them warm, so there is no per-job load cost.
  - Keep the llama-server process running alongside the API.
  - Enforce a per-job watchdog of about 240 s. When it fires, return partial results (CV-only verdict) rather than "failed".
- **UI:**
  - A static `index.html` with vanilla JS (or Alpine/htmx) served by FastAPI. It covers upload, a polling progress bar, the verdict, and violation cards: type, timecode, RU description, rule citation with link, evidence frames, and a `<video>` player that seeks to the timecode on click.
  - **Gradio** (Apache) is possible via `gr.mount_gradio_app(app, demo, path="/ui")` on the same FastAPI app. However, Gradio's own `/gradio_api` does **not** match the required REST contract, so the REST routes must be plain FastAPI regardless. A static page is equally quick and looks more like a product.
- **Public URL:**
  - **cloudflared**: a quick tunnel (`cloudflared tunnel --url http://localhost:8000`) is free and needs no account. Limits: 100 MB request body (fine), 200 in-flight requests, **no SSE** (we use polling). The URL changes on restart, so a **named tunnel** on a Cloudflare account domain gives a stable URL. Recommended.
  - ngrok free: shows a browser interstitial on HTML pages (API clients are fine). Works as a backup.
  - Tailscale Funnel: alternative.
  - A rented GPU VPS (RunPod/Vast/Lambda, 24 GB) running the same docker-compose plus vLLM is the backup if the laptop is unreliable. Inference remains self-hosted, so it is still "local models".
- Offline guarantee:
  - Pre-download all weights into `models/`.
  - Set `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`.
  - Test with networking off (apart from the tunnel).

---

## 5. Time budget per video (6 GB, worst case 145 s clip)

| Stage | Work | Est. time |
|---|---|---|
| Upload + save | 29 MB | 1–3 s |
| Decode | ffmpeg/PyAV @ 4 fps → about 580 frames (2 sizes) | 3–6 s |
| Zone calibration | Grounding DINO-T / Florence-2 on 5 frames | 2–4 s |
| Person/object detection | D-FINE-S @ 640, 580 frames, batched FP16 | 6–12 s |
| Tracking | ByteTrack | <1 s |
| Pose | RTMO-s / RTMPose-m ONNX-GPU on person crops, about 580 frames | 5–10 s |
| SigLIP-2 embeddings + kNN | at 1 fps, 145 frames | 2–4 s |
| VLM | Qwen3-VL-4B Q4 via llama.cpp, about 6 segments x 12 frames @ 320x576 (about 2.5k prompt tokens, ≤250 output tokens) at about 6–9 s each | 40–55 s |
| Fusion, KB, evidence JPEG encode | | <2 s |
| **Total** | | **about 65–100 s** (10 s clip: about 15–25 s) |

Notes:
- VRAM:
  - llama-server with the 4B Q4 model plus mmproj and a 16k context takes about 4–4.5 GB.
  - D-FINE-S, rtmlib ONNX and SigLIP-2-base take about 1–1.5 GB together.
  - That fits in 6 GB, but run the CV stage *before* the VLM stage, and free the CV CUDA cache or keep CV on ONNX-CUDA with small arenas.
  - If memory is tight, run pose on the CPU (rtmlib on onnxruntime CPU at 4 fps is fine).
- Concurrency: process one job at a time. If the jury submits in parallel, queued jobs can exceed 5 min. Mitigations:
  - Cap VLM segments dynamically by queue length (e.g. 3 segments when ≥2 jobs are waiting).
  - Use a 24 GB VPS for the final run.
- On 24 GB with vLLM and Qwen3-VL-8B-AWQ, one native-video call over the whole clip (2 fps, timestamps) plus about 4 segment checks takes about 30–45 s total.

---

## 6. License pitfalls checklist
- **Do not install `ultralytics`** (AGPL), even just for its trackers. Also avoid YOLO-World (GPL-3), YOLOv7/v9 (GPL-3) and YOLO-NAS weights (non-commercial).
- **Qwen2.5-VL-3B is non-commercial** (Qwen Research License). Use Qwen3-VL or Qwen3.5 (Apache-2.0).
- **VideoMAE and V-JEPA 2 weights are CC-BY-NC.** Do not use them.
- RF-DETR XL/2XL use the PML license. Nano–Large are Apache.
- MiniCPM-V needs commercial registration. Gemma 3 and Llama use custom licenses. SAM 3 uses the custom SAM License. Prefer Apache/MIT alternatives.
- ffmpeg: calling the binary or PyAV is fine. Do not statically link GPL builds into the code we ship.
- DINOv3 uses a custom license. Use SigLIP-2 or DINOv2 (Apache) instead.
- Add `LICENSES.md` listing every model, its license and its source URL. This is cheap and helps the tech score.

## Sources
- Qwen3-VL (Apache-2.0, sizes, llama.cpp GGUF): https://docs.unsloth.ai/models/qwen3-vl , https://www.local-llm.net/models/qwen3-vl/
- Qwen3.5 small models (2026-03-02, Apache-2.0, native video): https://gigazine.net/gsc_news/en/20260303-qwen-3-5-small , https://debuggercafe.com/introduction-to-qwen3-5-overview-vllm-and-llama-cpp/ , https://huggingface.co/unsloth/Qwen3.5-4B-GGUF
- Gemma 4 Apache-2.0 (2026-04-02): https://the-decoder.com/googles-gemma-4-is-now-available-with-apache-2-0-licensing-for-the-first-time/
- InternVL3.5: https://huggingface.co/OpenGVLab/InternVL3_5-1B-Instruct
- MiniCPM-V license: https://www.llmreference.com/model-family/minicpm
- SmolVLM2: https://roboflow.com/model/smolvlm2 ; Molmo 2: https://secure.businesswire.com/news/home/20251216910167/en/
- RF-DETR licensing: https://rfdetr.roboflow.com/latest/ ; YOLO-World GPL-3.0: https://roboflow.com/model-licenses/yolo-world
- SAM 3 license: https://blog.roboflow.com/what-is-sam3/
- rtmlib (Apache-2.0): https://github.com/Tau-J/rtmlib
- VideoMAE CC-BY-NC: https://huggingface.co/MCG-NJU/videomae-base-short/blob/main/README.md
- Cloudflare quick tunnel limits: https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/
- llama.cpp multimodal: https://github.com/ggml-org/llama.cpp/blob/master/docs/multimodal.md
