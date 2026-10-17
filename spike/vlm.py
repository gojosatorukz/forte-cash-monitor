"""Minimal client for a local llama-server (OpenAI-compatible) with image input."""
import base64, io, json, time
import requests
from PIL import Image

FRAMES = "/home/datamata/Projects/datathon2026/work/frames"
URL = "http://127.0.0.1:8090/v1/chat/completions"


def frame_path(vid, k):
    return f"{FRAMES}/{vid}/f_{k:04d}.jpg"


def t2k(t):
    """seconds -> frame index (2 fps, t=(k-1)/2)."""
    return int(round(t * 2)) + 1


def load(vid, k, crop=None, scale=1.0):
    im = Image.open(frame_path(vid, k)).convert("RGB")
    if crop:
        im = im.crop(crop)
    if scale != 1.0:
        im = im.resize((int(im.width * scale), int(im.height * scale)), Image.BICUBIC)
    return im


def b64(im, q=90):
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=q)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def ask(images, prompt, schema=None, max_tokens=256, system=None, temperature=0.0, labels=None):
    content = []
    for i, im in enumerate(images):
        if labels:
            content.append({"type": "text", "text": labels[i]})
        content.append({"type": "image_url", "image_url": {"url": b64(im)}})
    content.append({"type": "text", "text": prompt})
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": content})
    body = {"messages": msgs, "max_tokens": max_tokens, "temperature": temperature}
    if schema:
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": "out", "schema": schema}}
    t0 = time.time()
    r = requests.post(URL, json=body, timeout=600)
    dt = time.time() - t0
    r.raise_for_status()
    d = r.json()
    txt = d["choices"][0]["message"]["content"]
    tm = d.get("timings", {})
    out = {"text": txt, "wall_s": round(dt, 2),
           "prompt_n": tm.get("prompt_n"), "prompt_ms": tm.get("prompt_ms"),
           "pred_n": tm.get("predicted_n"), "pred_ms": tm.get("predicted_ms")}
    if schema:
        try:
            out["json"] = json.loads(txt)
        except Exception:
            out["json"] = None
    return out
