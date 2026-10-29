"""OCR accuracy of a vision model on synthetic images with known text, from clean to nearly illegible.

    python tools/ocr_bench.py --model qwen3.6-35b-a3b --label base
    python tools/ocr_bench.py --model qwen3.6-35b-a3b --label skip08 --set moe_hybrid_skip_weight=0.08 --set moe_hybrid_tail_skip=4

Starts llama-server with telecode's own command line for the model (as configured: context, slots, mmproj; `--set` edits a model key,
`--extra` appends flags), asks it to transcribe every image (greedy, thinking off) and scores the answers against the text that was drawn.
The images are generated from --seed, so every run sees exactly the same pictures and two configurations compare directly.

Levels (--per-level images each, the text kinds rotate):
  clean      large black text on white
  mild       slight blur, noise and rotation
  hard       small, low contrast, blur, rotation, JPEG artifacts
  very_hard  smaller, fainter, stronger blur and noise, uneven lighting, speckles, heavier JPEG
  extreme    tiny text rendered at low resolution and scaled up, faint, blurred, heavy JPEG
Text kinds: codes made of look-alike characters (0/O, 1/l/I, 5/S, 8/B, 2/Z), prices and dates, random word sequences (no language prior to
lean on), and multi-line blocks mixing them.

Score per image: exact (whitespace-normalised, case-sensitive), CER (character edit distance / length of the truth), empty (no text) and
off (CER > 0.8: unrelated or hallucinated). Prints one JSON line per image (--verbose) and a summary per level plus overall; writes
ocr_<label>.json (answers) and, with --save-images, the PNGs to --out.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import random
import shlex
import shutil
import subprocess
import sys
import time
import urllib.request

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import moe_cache_bench as B  # noqa: E402  (server launch helpers, VRAM kill)

FONTS = [os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", f) for f in
         ("arial.ttf", "times.ttf", "consola.ttf", "verdana.ttf", "georgia.ttf", "cour.ttf", "calibri.ttf", "arialbd.ttf")]
CONFUSABLE = "0O1lI5S8B2Z6G9qQD7T"
ALNUM = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
WORDS = ("copper lamp ladder quietly beneath orange window seventeen marble harbor velvet crisp lantern meadow"
         " anchor bitter cobalt drift ember falcon granite hollow ivory juniper kettle lumber mosaic nimble"
         " oyster pebble quartz ripple saddle thistle umber vessel walnut yonder zephyr basalt cinder").split()
LEVELS = ("clean", "mild", "hard", "very_hard", "extreme", "page")
PROMPT = "Read the text in this image and write it out exactly, character for character, keeping the line breaks. Output only the text."


# ── text ─────────────────────────────────────────────────────────────────────

def _code(r: random.Random) -> str:
    pool = CONFUSABLE + ALNUM
    a = "".join(r.choice(pool) for _ in range(r.randint(4, 5)))
    b = "".join(r.choice(pool) for _ in range(r.randint(4, 5)))
    return f"{a}-{b}"


def _money_date(r: random.Random) -> str:
    return (f"INV {r.randint(10000, 99999)}  {r.randint(1, 28):02d}/{r.randint(1, 12):02d}/{r.randint(2019, 2031)}"
            f"  ${r.randint(1, 9999)}.{r.randint(0, 99):02d}")


def _words(r: random.Random, n: int) -> str:
    return " ".join(r.choice(WORDS) for _ in range(n)) + f" {r.randint(2, 999)}"


def _page(r: random.Random) -> str:
    """A dense block: 150-220 words with codes and figures inside, wrapped at about 60 characters."""
    toks = []
    for _ in range(r.randint(150, 220)):
        x = r.random()
        toks.append(_code(r) if x < 0.06 else str(r.randint(2, 99999)) if x < 0.14 else r.choice(WORDS))
    lines, cur = [], ""
    for t in toks:
        if cur and len(cur) + 1 + len(t) > 60:
            lines.append(cur)
            cur = t
        else:
            cur = f"{cur} {t}".strip()
    return "\n".join(lines + [cur])


def make_text(r: random.Random, kind: int) -> str:
    if kind == 0:
        return _code(r)
    if kind == 1:
        return _money_date(r)
    if kind == 2:
        return _words(r, r.randint(5, 7))
    return "\n".join([_code(r), _words(r, 4), _money_date(r)])


# ── image ────────────────────────────────────────────────────────────────────

def _font(r: random.Random, size: int) -> ImageFont.FreeTypeFont:
    for _ in range(len(FONTS)):
        f = r.choice(FONTS)
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    return ImageFont.load_default(size)  # type: ignore[return-value]


def render(text: str, level: str, r: random.Random) -> Image.Image:
    size = {"clean": 30, "mild": 24, "hard": 18, "very_hard": 17, "extreme": 16, "page": 18}[level]
    bg = {"clean": 255, "mild": 245, "hard": 205, "very_hard": 210, "extreme": 205, "page": 225}[level]
    fg = {"clean": 0, "mild": 30, "hard": 80, "very_hard": 75, "extreme": 70, "page": 50}[level]
    font = _font(r, size)
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    box = probe.multiline_textbbox((0, 0), text, font=font, spacing=size // 3)
    w, h = box[2] - box[0] + 2 * size, box[3] - box[1] + 2 * size
    img = Image.new("L", (w, h), bg)
    ImageDraw.Draw(img).multiline_text((size - box[0], size - box[1]), text, fill=fg, font=font, spacing=size // 3)

    if level == "extreme":  # rendered at low resolution, then scaled back up: blocky, smeared glyphs
        img = img.resize((max(1, int(w * 0.72)), max(1, int(h * 0.72))), Image.BILINEAR).resize((w, h), Image.BILINEAR)
    angle = {"clean": 0.0, "mild": 1.5, "hard": 3.0, "very_hard": 5.0, "extreme": 3.5, "page": 1.2}[level]
    if angle:
        img = img.rotate(r.uniform(-angle, angle), resample=Image.BICUBIC, expand=True, fillcolor=bg)
    if level == "very_hard":  # uneven lighting: a darker band across the page
        grad = Image.linear_gradient("L").resize(img.size).rotate(r.choice((0, 90, 180, 270)))
        img = Image.blend(img, Image.eval(grad, lambda v: 120 + v // 2), 0.25)
    blur = {"clean": 0.0, "mild": 0.6, "hard": 0.9, "very_hard": 1.0, "extreme": 0.7, "page": 0.6}[level]
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    noise = {"clean": 0, "mild": 12, "hard": 20, "very_hard": 26, "extreme": 24, "page": 16}[level]
    if noise:
        img = Image.blend(img, Image.effect_noise(img.size, noise).point(lambda v: max(0, min(255, v))), 0.18)
    if level in ("very_hard", "extreme"):  # speckles
        d = ImageDraw.Draw(img)
        for _ in range(img.size[0] * img.size[1] // 1500):
            x, y = r.randrange(img.size[0]), r.randrange(img.size[1])
            d.point((x, y), fill=r.choice((60, 90, 230)))
    q = {"clean": 0, "mild": 0, "hard": 35, "very_hard": 30, "extreme": 25, "page": 40}[level]
    if q:
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=q)
        img = Image.open(io.BytesIO(buf.getvalue())).convert("L")
    return img


def build_set(seed: int, per_level: int) -> list[dict]:
    r = random.Random(seed)
    items = []
    for level in LEVELS:
        for i in range(per_level):
            text = _page(r) if level == "page" else make_text(r, i % 4)
            items.append({"id": f"{level}-{i:02d}", "level": level, "text": text, "image": render(text, level, r)})
    return items


# ── scoring ──────────────────────────────────────────────────────────────────

def norm(s: str) -> str:
    # llama-server with --reasoning-format none leaves the (empty) think block in the content; telecode's proxy strips it for clients
    if "</think>" in s:
        s = s.split("</think>", 1)[1]
    lines = [" ".join(ln.split()) for ln in s.strip().strip("`").strip().splitlines()]
    return "\n".join(ln for ln in lines if ln)


def edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def score(truth: str, answer: str) -> dict:
    t, a = norm(truth), norm(answer)
    cer = edit_distance(a, t) / max(1, len(t))
    return {"exact": a == t, "cer": round(cer, 4), "empty": not a, "off": bool(a) and cer > 0.8}


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    return {"n": n, "exact": sum(x["exact"] for x in rows), "exact_pct": round(100 * sum(x["exact"] for x in rows) / max(1, n), 1),
            "cer_mean": round(sum(x["cer"] for x in rows) / max(1, n), 4), "empty": sum(x["empty"] for x in rows),
            "off": sum(x["off"] for x in rows), "sec_mean": round(sum(x["sec"] for x in rows) / max(1, n), 2)}


# ── server ───────────────────────────────────────────────────────────────────

def ask(port: int, img: Image.Image, max_tokens: int, thinking: bool) -> tuple[str, float]:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    body = {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}}, {"type": "text", "text": PROMPT}]}],
            "max_tokens": max_tokens, "temperature": 0, "top_k": 1, "seed": 1, "cache_prompt": False,
            "chat_template_kwargs": {"enable_thinking": thinking}}
    req = urllib.request.Request(f"http://{B.BENCH_HOST}:{port}/v1/chat/completions", json.dumps(body).encode(), {"Content-Type": "application/json"})
    t0 = time.time()
    r = json.load(urllib.request.urlopen(req, timeout=900))
    return r["choices"][0]["message"].get("content") or "", time.time() - t0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--model", required=True, help="model name in llamacpp.models (needs an mmproj)")
    ap.add_argument("--label", default="ocr")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="model key edit (repeatable)")
    ap.add_argument("--extra", default="", help="extra llama-server args, one quoted string")
    ap.add_argument("--exe", default="", help="llama-server binary (default: llamacpp.binary)")
    ap.add_argument("--seed", type=int, default=20261029)
    ap.add_argument("--per-level", type=int, default=8)
    ap.add_argument("--max-tokens", type=int, default=0, help="per answer (default 160, page 900; with --thinking 4096 / 6144)")
    ap.add_argument("--thinking", action="store_true", help="reasoning on (an answer still thinking at the limit counts as empty)")
    ap.add_argument("--levels", default="", help="comma-separated subset of " + ",".join(LEVELS))
    ap.add_argument("--vram-limit", type=int, default=11700, help="kill the server above this many MiB in use (0 = off)")
    ap.add_argument("--port", type=int, default=18096)
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "logs", "ocr_bench"))
    ap.add_argument("--save-images", action="store_true")
    ap.add_argument("--images-only", action="store_true", help="write the images and the truth, start no server")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--rescore", default="", metavar="OCR_JSON", help="score a saved ocr_<label>.json again, start no server")
    a = ap.parse_args()

    if a.rescore:
        rows = json.load(open(a.rescore, encoding="utf-8"))
        for row in rows:
            row.update(score(row["truth"], row["answer"]))
        json.dump(rows, open(a.rescore, "w", encoding="utf-8"), indent=1)
        print(json.dumps({"label": a.label, "overall": summarize(rows), "levels": {lv: summarize([x for x in rows if x["level"] == lv]) for lv in LEVELS if any(x["level"] == lv for x in rows)}}))
        return 0

    items = build_set(a.seed, a.per_level)
    if a.levels:
        keep = set(a.levels.split(","))
        items = [it for it in items if it["level"] in keep]
    os.makedirs(a.out, exist_ok=True)
    if a.save_images or a.images_only:
        for it in items:
            it["image"].save(os.path.join(a.out, f"{it['id']}.png"))
        json.dump({it["id"]: it["text"] for it in items}, open(os.path.join(a.out, "truth.json"), "w", encoding="utf-8"), indent=1)
    if a.images_only:
        print(json.dumps({"images": len(items), "out": a.out}))
        return 0

    for _ in range(60):  # a previous server may still be shutting down
        if not B._server_running():
            break
        time.sleep(1)
    if B._server_running():
        print(json.dumps({"label": a.label, "error": "a llama-server.exe is already running; stop it first"}))
        return 2
    ns = argparse.Namespace(model=a.model, set=a.set, port=a.port, exe=a.exe, no_safe=True, ctx=None, max_threads=8)
    os.environ["TELECODE_SETTINGS"] = B._make_settings(ns)
    from llamacpp import argv as A
    from llamacpp import config as lcfg
    from process import kill_process_tree

    cmd = A.build_argv(a.model)
    cmd[0] = shutil.which(cmd[0]) or cmd[0]
    if "--mmproj" not in cmd:
        print(json.dumps({"label": a.label, "error": f"{a.model} has no mmproj configured"}))
        return 2
    if a.extra:
        cmd += [x.strip('"') for x in shlex.split(a.extra, posix=(os.name != "nt"))]
    log = open(os.path.join(a.out, f"ocr_{a.label}.log"), "w")
    peak = B._VramPeak(a.vram_limit)
    proc = None
    rows: list[dict] = []
    try:
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=lcfg.spawn_env(a.model),
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        peak.kill = lambda: kill_process_tree(proc.pid)
        peak.start()
        t0 = time.time()
        while True:
            if proc.poll() is not None:
                print(json.dumps({"label": a.label, "error": f"server exited ({proc.returncode}); see {log.name}"}))
                return 1
            if time.time() - t0 > 600:
                print(json.dumps({"label": a.label, "error": "NOT READY"}))
                return 1
            try:
                if json.load(urllib.request.urlopen(f"http://{B.BENCH_HOST}:{a.port}/health", timeout=2)).get("status") == "ok":
                    break
            except Exception:
                pass
            time.sleep(2)
        for it in items:
            mt = a.max_tokens or ((6144 if it["level"] == "page" else 4096) if a.thinking else (900 if it["level"] == "page" else 160))
            answer, sec = ask(a.port, it["image"], mt, a.thinking)
            if a.thinking and "<think>" in answer and "</think>" not in answer:
                answer = ""  # still thinking at the limit: no answer
            row = {"id": it["id"], "level": it["level"], "truth": it["text"], "answer": answer, "sec": round(sec, 2), **score(it["text"], answer)}
            rows.append(row)
            if a.verbose:
                print(json.dumps({k: row[k] for k in ("id", "exact", "cer", "sec")}), flush=True)
        json.dump(rows, open(os.path.join(a.out, f"ocr_{a.label}.json"), "w", encoding="utf-8"), indent=1)
        per = {lv: summarize([x for x in rows if x["level"] == lv]) for lv in LEVELS if any(x["level"] == lv for x in rows)}
        print(json.dumps({"label": a.label, "overall": summarize(rows), "levels": per, "peak_vram_mib": peak.peak}))
        return 0
    finally:
        peak.stop()
        if proc is not None and proc.poll() is None:
            kill_process_tree(proc.pid, force=False)
            try:
                proc.wait(20)
            except subprocess.TimeoutExpired:
                kill_process_tree(proc.pid, force=True)
        log.close()


if __name__ == "__main__":
    sys.exit(main())
