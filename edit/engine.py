#!/usr/bin/env python3
"""Plan-driven short-form edit engine (ffmpeg + numpy).

A plan (JSON) lists segments cut from source clips. Each segment can be
slowed (optical-flow interpolated), punched in, shaken, flashed in from
white, or turned black & white. Text overlays animate in and out. The
soundtrack is synthesised: a minimal dark beat that drops out during
slow-motion, plus whooshes, impacts and risers placed on the cuts, with
the source audio pitched down in slow-mo the way edits usually do it.

  python3 edit/engine.py plan.json out.mp4 --src DIR [--work DIR]

Plan schema (see edit/plans/*.json for real ones):
{
  "grade": "aesthetic" | "night" | "none",
  "audio": "source" (default, real clip audio) | "synth" (legacy, user disliked),
  "music": {"file": "song.mp3", "start": 12.0, "gain": 0.9, "src_gain": 0.25},
  "letterbox": true,
  "bpm": 100,
  "segments": [
    {"src": "IMG_7071.MOV", "in": 3.8, "out": 4.6, "speed": 1.0,
     "zoom": "punch" | "push" | "pull" | null, "shake": 0.0-1.0,
     "flash": true, "bw": true, "sfx": ["whoosh", "impact", "riser", "drop"],
     "src_audio": 0.0-1.0}
  ],
  "texts": [
    {"text": "night session", "start": 1.0, "end": 4.0, "size": 72,
     "y": 0.2, "font": "serif" | "serif_italic" | "sans_bold"}
  ]
}
"""
import argparse
import json
import math
import os
import subprocess
import wave

import numpy as np

W, H, FPS, SR = 1080, 1920, 60, 48000
FONTS = {
    "serif": "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
    "serif_italic": "/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf",
    "serif_bold": "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
    "sans_bold": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "label": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
}
GRADES = {
    "aesthetic": ("eq=contrast=0.95:saturation=0.86:brightness=0.015:gamma=1.02,"
                  "colorbalance=rh=0.05:bh=-0.04:rs=0.02:bs=0.03,"
                  "curves=all='0/0.05 0.5/0.52 1/0.97'"),
    "night": ("eq=contrast=1.06:saturation=0.85:gamma=0.97,"
              "colorbalance=bs=0.04:rh=0.03:bh=-0.02,"
              "curves=all='0/0.03 0.5/0.49 1/0.98'"),
    "none": "null",
}


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"ffmpeg failed:\n{' '.join(cmd)}\n{r.stderr[-3000:]}")


def esc(t):
    return (t.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’")
            .replace("%", "\\%").replace(",", "\\,"))


# ---------------------------------------------------------------- video ----
def zoom_expr(kind, dur):
    if kind == "punch":   # fast punch-in that settles
        return "1.0+0.16*exp(-ot*7)"
    if kind == "push":    # slow continuous push
        return f"1.0+0.10*min(ot/{dur:.3f},1)"
    if kind == "pull":    # start tight, ease out
        return f"1.12-0.12*min(ot/{dur:.3f},1)"
    if kind == "drift":   # barely-there motion for stills
        return f"1.0+0.035*min(ot/{dur:.3f},1)"
    return "1.0"


def render_segment(i, seg, src_dir, work, grade):
    src = os.path.join(src_dir, seg["src"])
    speed = float(seg.get("speed", 1.0))
    if speed > 1.0 and not seg.get("allow_speedup"):
        raise SystemExit(f"segment {i}: speed {speed} > 1 needs \"allow_speedup\": true. The user wants "
                         "speed-ups rarely - one deliberate moment per edit (see LEARNINGS.md).")
    a, b = float(seg["in"]), float(seg["out"])
    out_dur = (b - a) / speed
    still = src.lower().endswith((".jpg", ".jpeg", ".png", ".heic"))
    if still:  # photos: a = 0, b = how long to hold it
        if src.lower().endswith(".heic"):
            jpg = os.path.join(work, os.path.basename(src) + ".jpg")
            if not os.path.exists(jpg):
                import pillow_heif
                from PIL import Image, ImageOps
                pillow_heif.register_heif_opener()
                ImageOps.exif_transpose(Image.open(src)).convert("RGB").save(jpg, quality=95)
            src = jpg
        speed, a = 1.0, 0.0
        out_dur = b
    pre = 0.0 if still else max(0.0, a - 0.5)  # fast input seek, then exact trim
    vf = [f"trim={a - pre:.3f}:{b - pre:.3f}", "setpts=PTS-STARTPTS",
          f"scale={W}:{H}:force_original_aspect_ratio=increase", f"crop={W}:{H}",
          "fps=60"]
    if speed < 1.0:
        vf += [f"setpts=PTS/{speed}",
               "minterpolate=fps=60:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1"]
    elif speed > 1.0:  # velocity ramp: speed up with motion blur
        vf += [f"setpts=PTS/{speed}", f"tmix=frames={min(5, max(2, round(speed * 1.5)))}", "fps=60"]
    z = zoom_expr(seg.get("zoom"), out_dur)
    sh = float(seg.get("shake", 0))
    sx = f"+{sh * 18:.1f}*sin(ot*53)*exp(-ot*4)" if sh else ""
    sy = f"+{sh * 14:.1f}*sin(ot*61+1.3)*exp(-ot*4)" if sh else ""
    vf.append(f"zoompan=z='{z}':d=1:fps=60:s={W}x{H}:"
              f"x='iw/2-(iw/zoom/2){sx}':y='ih/2-(ih/zoom/2){sy}'")
    vf.append(GRADES[grade])
    if seg.get("bw"):
        vf.append("hue=s=0,eq=contrast=1.12")
    if seg.get("flash"):
        vf.append("fade=t=in:st=0:d=0.1:color=white")
    out = os.path.join(work, f"seg{i:02d}.mp4")
    key = json.dumps({k: v for k, v in seg.items() if not k.startswith("_")}, sort_keys=True) + grade
    keyf = out + ".key"
    cached = os.path.exists(out) and os.path.exists(keyf) and open(keyf).read() == key
    if not cached:
      inp = ["-loop", "1", "-framerate", "60", "-t", f"{out_dur + 0.1:.3f}", "-i", src] if still \
          else ["-ss", f"{pre:.3f}", "-i", src]
      run(["ffmpeg", "-y", "-hide_banner", *inp, "-an", "-vf", ",".join(vf),
         "-t", f"{out_dur:.3f}", "-r", "60", "-c:v", "libx264", "-preset", "fast",
         "-crf", "14", "-pix_fmt", "yuv420p", out])
      open(keyf, "w").write(key)

    # source audio: pitched down + slowed for slow-mo, the classic edit sound
    aout = os.path.join(work, f"seg{i:02d}.wav")
    if still:
        silence(aout, out_dur)
        return out, aout, out_dur
    af = f"atrim={a - pre:.3f}:{b - pre:.3f},asetpts=PTS-STARTPTS"
    if speed < 1.0:
        af += f",asetrate={int(SR * speed)},aresample={SR},lowpass=f=3000"
    elif speed > 1.0:
        af += f",atempo={min(speed, 2.0)}" + (f",atempo={speed / 2.0}" if speed > 2.0 else "")
    af += f",aformat=sample_fmts=s16:channel_layouts=mono,apad,atrim=0:{out_dur:.3f}"
    r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{pre:.3f}", "-i", src,
                        "-vn", "-af", af, "-ar", str(SR), aout], capture_output=True)
    if r.returncode:
        silence(aout, out_dur)
    return out, aout, out_dur


def silence(path, dur):
    write_wav(path, np.zeros(int(dur * SR)))


# ---------------------------------------------------------------- audio ----
def write_wav(path, x, stereo=False):
    x = np.clip(x, -1, 1)
    data = (x * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(2 if stereo else 1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data.tobytes())


def read_wav(path):
    with wave.open(path) as w:
        return np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(float) / 32767


def onepole_lp(x, coef):
    """Simple one-pole lowpass; coef may be scalar or per-sample array."""
    y = np.empty_like(x)
    c = np.broadcast_to(coef, x.shape)
    acc = 0.0
    for n in range(len(x)):
        acc += c[n] * (x[n] - acc)
        y[n] = acc
    return y


def t_(d):
    return np.arange(int(d * SR)) / SR


def kick():
    t = t_(0.45)
    f = 45 + 95 * np.exp(-t * 28)
    ph = 2 * np.pi * np.cumsum(f) / SR
    return np.sin(ph) * np.exp(-t * 7) * 0.95 + np.random.randn(len(t)) * np.exp(-t * 300) * 0.15


def clap():
    t = t_(0.25)
    n = np.random.randn(len(t))
    n = n - onepole_lp(n, 0.25)  # crude high-pass
    env = np.exp(-t * 22) * (1 + 0.6 * (t < 0.012))
    return n * env * 0.45


def hat(open_=False):
    t = t_(0.18 if open_ else 0.05)
    n = np.random.randn(len(t))
    n = np.diff(np.diff(n, prepend=0), prepend=0) * 0.5
    return n * np.exp(-t * (18 if open_ else 80)) * 0.18


def impact():
    t = t_(2.2)
    f = 30 + 50 * np.exp(-t * 6)
    sub = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 1.6)
    n = onepole_lp(np.random.randn(len(t)), 0.05) * np.exp(-t * 5) * 2.5
    return np.tanh((sub + n) * 1.4) * 0.9


def whoosh(d=0.55):
    t = t_(d)
    n = np.random.randn(len(t))
    env = np.sin(np.pi * np.clip(t / d, 0, 1)) ** 2
    coef = 0.02 + 0.35 * env
    return onepole_lp(n, coef) * env * 0.6


def riser(d=1.6):
    t = t_(d)
    n = onepole_lp(np.random.randn(len(t)), 0.02 + 0.5 * (t / d) ** 2)
    tone = np.sin(2 * np.pi * np.cumsum(180 + 900 * (t / d) ** 2) / SR) * 0.15
    return (n * 0.5 + tone) * (t / d) ** 2.2


def drop(d=0.9):
    """Down-sweep that sells the switch into slow-mo."""
    t = t_(d)
    f = 60 + 500 * np.exp(-t * 5)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 3) * 0.45


def pad(dur, bpm):
    """Dark A-minor drone bed with slow filter movement."""
    t = t_(dur)
    out = np.zeros_like(t)
    for f, g in ((55, 0.5), (110, 0.3), (130.81, 0.18), (164.81, 0.16), (220, 0.08)):
        for det in (-0.6, 0.6):
            ph = 2 * np.pi * (f + det) * t
            out += g * (2 * ((ph / (2 * np.pi)) % 1) - 1)  # saw
    lfo = 0.03 + 0.025 * (1 + np.sin(2 * np.pi * t * bpm / 60 / 8))
    out = onepole_lp(out, lfo) * 0.22
    return out


def place(buf, clip, at, gain=1.0):
    i = int(at * SR)
    if i >= len(buf):
        return
    n = min(len(clip), len(buf) - i)
    buf[i:i + n] += clip[:n] * gain


def build_audio(plan, segs, total, work):
    mode = plan.get("audio", "source")
    if mode != "synth":
        return build_audio_real(plan, segs, total, work)
    return build_audio_synth(plan, segs, total, work)


def build_audio_real(plan, segs, total, work):
    """Real audio only: the clips' own sound, optionally under a music track."""
    src = np.zeros(int(total * SR) + SR)
    for (start, dur, seg) in segs:
        place(src, read_wav(seg["_wav"]), start, float(seg.get("src_audio", 0.8)))
    mix = src[:int(total * SR)]
    music = plan.get("music")
    if music:
        mp = os.path.join(work, "music.wav")
        run(["ffmpeg", "-y", "-hide_banner", "-ss", str(music.get("start", 0)), "-i", music["file"],
             "-t", f"{total:.3f}", "-ac", "1", "-ar", str(SR), "-sample_fmt", "s16", mp])
        m = read_wav(mp)
        m = np.pad(m, (0, max(0, len(mix) - len(m))))[:len(mix)]
        mix = m * float(music.get("gain", 0.9)) + mix * float(music.get("src_gain", 0.25))
    fade = int(0.4 * SR)
    mix[-fade:] *= np.linspace(1, 0, fade)
    peak = np.max(np.abs(mix)) or 1
    mix = mix / peak * 0.89
    path = os.path.join(work, "mix.wav")
    write_wav(path, mix)
    return path


def build_audio_synth(plan, segs, total, work):
    bpm = float(plan.get("bpm", 100))
    beat = 60 / bpm
    music = pad(total, bpm) * float(plan.get("pad_gain", 1.0))
    drums = np.zeros(int(total * SR) + SR)
    sfx = np.zeros_like(drums)
    src = np.zeros_like(drums)

    # drums only in real-time segments; slow-mo = drums out (tension)
    for (start, dur, seg) in segs:
        slow = float(seg.get("speed", 1)) < 1
        if not slow:
            n = 0
            t = start
            while t < start + dur - 0.02:
                step = n % 8
                if step == 0:
                    place(drums, kick(), t)
                if step == 4:
                    place(drums, clap(), t)
                if step in (3, 6):
                    place(drums, kick(), t, 0.6)
                place(drums, hat(open_=(step == 7)), t, 0.8 if step % 2 else 1.0)
                n += 1
                t += beat / 2
        for name in seg.get("sfx", []):
            if name == "impact":
                place(sfx, impact(), start, 0.9)
            elif name == "whoosh":
                place(sfx, whoosh(), max(0, start - 0.3), 0.8)
            elif name == "riser":
                r = riser()
                place(sfx, r, max(0, start + dur - len(r) / SR), 0.7)
            elif name == "drop":
                place(sfx, drop(), start, 0.9)
        place(src, read_wav(seg["_wav"]), start, float(seg.get("src_audio", 0.35)))

    mix = music[:len(drums)] if len(music) >= len(drums) else np.pad(music, (0, len(drums) - len(music)))
    mix = mix + drums * 0.8 + sfx + src
    mix = mix[:int(total * SR)]
    fade = int(0.6 * SR)
    mix[-fade:] *= np.linspace(1, 0, fade)
    mix = np.tanh(mix * 1.2) * 0.8  # headroom for AAC overshoot
    path = os.path.join(work, "mix.wav")
    write_wav(path, mix)
    return path


# ---------------------------------------------------------------- text -----
def text_filters(texts):
    out = []
    for tx in texts:
        s, e = float(tx["start"]), float(tx["end"])
        font = FONTS[tx.get("font", "serif")]
        size = int(tx.get("size", 64))
        y = float(tx.get("y", 0.2))
        # fade + 24px rise in, fade out
        alpha = (f"if(lt(t,{s}),0,if(lt(t,{s + 0.35}),(t-{s})/0.35,"
                 f"if(lt(t,{e - 0.3}),1,if(lt(t,{e}),({e}-t)/0.3,0))))")
        yexp = f"h*{y}+24*max(0,1-(t-{s})/0.35)"
        out.append(f"drawtext=fontfile={font}:text='{esc(tx['text'])}':fontsize={size}:"
                   f"fontcolor=white:alpha='{alpha}':shadowcolor=black@0.6:shadowx=2:shadowy=3:borderw=1:bordercolor=black@0.35:"
                   f"x=(w-text_w)/2:y='{yexp}':enable='between(t,{s},{e})'")
    return out


def red_title_png(tx, path):
    """Andorra-style card: huge, horizontally squashed red serif caps filling the
    frame width, with small letter-spaced red italic credit lines under it."""
    from PIL import Image, ImageDraw, ImageFont
    red = tuple(int(tx.get("color", "e00000")[i:i + 2], 16) for i in (0, 2, 4)) + (255,)
    word = tx["text"].upper()
    f = ImageFont.truetype(FONTS["serif"], 900)
    l, t, r, b = f.getbbox(word)
    layer = Image.new("RGBA", (r - l + 20, b - t + 20), (0, 0, 0, 0))
    ImageDraw.Draw(layer).text((10 - l, 10 - t), word, font=f, fill=red)
    tw = int(W * float(tx.get("width", 0.96)))
    th = int(H * float(tx.get("height", 0.30)))
    layer = layer.resize((tw, th), Image.LANCZOS)
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    cy = int(H * float(tx.get("y", 0.40)))
    canvas.alpha_composite(layer, ((W - tw) // 2, cy - th // 2))
    sub = ImageFont.truetype(FONTS["serif_italic"], int(tx.get("sub_size", 26)))
    d = ImageDraw.Draw(canvas)
    y = cy + th // 2 + 18
    for line in tx.get("sub", []):
        line = line.upper()
        spacing = 4
        width = sum(d.textlength(c, font=sub) + spacing for c in line) - spacing
        x = (W - width) / 2
        for c in line:
            d.text((x, y), c, font=sub, fill=red)
            x += d.textlength(c, font=sub) + spacing
        y += int(tx.get("sub_size", 26) * 1.35)
    canvas.save(path)


# ---------------------------------------------------------------- main -----
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("output")
    ap.add_argument("--src", required=True)
    ap.add_argument("--work", default=None)
    a = ap.parse_args()
    plan = json.load(open(a.plan))
    work = a.work or os.path.splitext(a.output)[0] + "_work"
    os.makedirs(work, exist_ok=True)
    np.random.seed(7)

    fast = [k for k, sg in enumerate(plan["segments"]) if float(sg.get("speed", 1)) > 1]
    if len(fast) > 1:
        raise SystemExit(f"segments {fast} are sped up; the user allows at most one deliberate "
                         "speed-up per edit (see LEARNINGS.md).")
    segs, t = [], 0.0
    files = []
    for i, seg in enumerate(plan["segments"]):
        v, w, d = render_segment(i, seg, a.src, work, plan.get("grade", "aesthetic"))
        seg["_wav"] = w
        segs.append((t, d, seg))
        files.append(v)
        t += d
        print(f"  seg {i:02d} {seg['src']:<16} {seg['in']:>5}-{seg['out']:<5} x{seg.get('speed', 1)} -> {d:.2f}s")
    total = t
    lst = os.path.join(work, "list.txt")
    with open(lst, "w") as f:
        f.writelines(f"file '{os.path.abspath(p)}'\n" for p in files)
    joined = os.path.join(work, "joined.mp4")
    run(["ffmpeg", "-y", "-hide_banner", "-f", "concat", "-safe", "0", "-i", lst,
         "-c", "copy", joined])

    mix = build_audio(plan, segs, total, work)
    vf = [] if plan.get("finish") == "clean" else ["noise=alls=5:allf=t", "vignette=PI/5"]
    if plan.get("letterbox"):
        bar = int(plan.get("letterbox_px", 210))
        vf += [f"drawbox=x=0:y=0:w=iw:h={bar}:color=black:t=fill",
               f"drawbox=x=0:y=ih-{bar}:w=iw:h={bar}:color=black:t=fill"]
    texts = plan.get("texts", [])
    vf += text_filters([t for t in texts if t.get("style") != "red_title"])
    cards = [t for t in texts if t.get("style") == "red_title"]
    inputs, chain, last = [], "[0:v]" + (",".join(vf) or "null") + "[b0]", "[b0]"
    for k, tx in enumerate(cards):
        png = os.path.join(work, f"card{k}.png")
        red_title_png(tx, png)
        inputs += ["-loop", "1", "-i", png]
        chain += (f";{last}[{k + 2}:v]overlay=0:0:shortest=1:"
                  f"enable='between(t,{tx['start']},{tx['end']})'[b{k + 1}]")
        last = f"[b{k + 1}]"
    chain += f";{last}fade=t=out:st={total - 0.5:.2f}:d=0.5[vout]"
    run(["ffmpeg", "-y", "-hide_banner", "-i", joined, "-i", mix, *inputs, "-filter_complex", chain,
         "-map", "[vout]", "-map", "1:a", "-c:v", "libx264", "-preset", "slow", "-crf", "17",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart",
         "-shortest", a.output])
    print(f"done: {a.output} ({total:.1f}s)")


if __name__ == "__main__":
    main()
