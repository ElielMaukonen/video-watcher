#!/usr/bin/env python3
"""Cycling/MTB short-form edit presets built on ffmpeg.

Styles (reverse-engineered from reference shorts' public thumbnails):

  aesthetic  9:16 full frame, soft warm faded grade, small centred serif
             title + italic subtitle in the upper third ("MTB lifestyle" look).
  gritty     1:1 square plate centred on black 9:16, desaturated warm-sepia
             grade, film grain, vignette, thin white inset frame and a small
             spaced-caps footer line ("velocity edit" look).

Example:
  python3 edit/bike_edit.py clip.mp4 out.mp4 --style aesthetic \
      --title "MTB lifestyle" --subtitle "(MTB aesthetic)" --slowmo 3.2-4.6 \
      --music song.mp3 --music-start 12
"""
import argparse
import json
import subprocess
import sys

W, H = 1080, 1920
SERIF = "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf"
SERIF_IT = "/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf"
SANS_B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def esc(t: str) -> str:
    return t.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’").replace("%", "\\%")


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type",
         "-of", "json", path], capture_output=True, text=True, check=True).stdout
    j = json.loads(out)
    return float(j["format"]["duration"]), any(s["codec_type"] == "audio" for s in j["streams"])


def speed_graph(dur, slowmo, factor):
    """Split [0:v] into normal / slow / normal and concat. Returns (graph, label, new_dur)."""
    if not slowmo:
        return "[0:v]setpts=PTS-STARTPTS[v0]", "[v0]", dur
    a, b = (float(x) for x in slowmo.split("-"))
    a, b = max(0.0, a), min(dur, b)
    g = (f"[0:v]split=3[s1][s2][s3];"
         f"[s1]trim=0:{a},setpts=PTS-STARTPTS[p1];"
         f"[s2]trim={a}:{b},setpts=(PTS-STARTPTS)/{factor},"
         f"minterpolate=fps=60:mi_mode=blend[p2];"
         f"[s3]trim={b},setpts=PTS-STARTPTS[p3];"
         f"[p1]fps=60[q1];[p3]fps=60[q3];"
         f"[q1][p2][q3]concat=n=3:v=1:a=0[v0]")
    return g, "[v0]", dur + (b - a) * (1 / factor - 1)


def style_aesthetic(lbl, title, subtitle):
    f = (f"{lbl}scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
         "eq=contrast=0.94:saturation=0.88:brightness=0.02:gamma=1.03,"
         "colorbalance=rh=0.05:bh=-0.04:rs=0.02:bs=0.03,"
         "curves=all='0/0.06 0.5/0.52 1/0.97'")
    if title:
        f += (f",drawtext=fontfile={SERIF}:text='{esc(title)}':fontsize=64:"
              "fontcolor=white@0.9:shadowcolor=black@0.25:shadowx=2:shadowy=2:"
              "x=(w-text_w)/2:y=h*0.20")
    if subtitle:
        f += (f",drawtext=fontfile={SERIF_IT}:text='{esc(subtitle)}':fontsize=40:"
              "fontcolor=white@0.85:shadowcolor=black@0.25:shadowx=2:shadowy=2:"
              "x=(w-text_w)/2:y=h*0.20+80")
    return f + "[vout]"


def style_gritty(lbl, title, subtitle):
    S = W  # square plate
    footer = subtitle or "© BIKE EDIT"
    f = (f"{lbl}scale={S}:{S}:force_original_aspect_ratio=increase,crop={S}:{S},"
         "hue=s=0.32,"
         "colorchannelmixer=rr=1.05:gg=0.98:bb=0.88,"
         "curves=all='0/0.04 0.25/0.18 0.75/0.80 1/0.96',"
         "noise=alls=14:allf=t+u,vignette=PI/4.5,"
         "drawbox=x=28:y=28:w=iw-56:h=ih-56:color=white@0.85:t=3,"
         f"drawtext=fontfile={SANS_B}:text='{esc(footer.upper())}':fontsize=22:"
         "fontcolor=white@0.9:box=1:boxcolor=black@1:boxborderw=8:"
         "x=(w-text_w)/2:y=h-28-text_h/2")
    if title:
        f += (f",drawtext=fontfile={SANS_B}:text='{esc(title.upper())}':fontsize=54:"
              "fontcolor=white@0.95:x=(w-text_w)/2:y=70")
    return f + f",pad={W}:{H}:0:(oh-ih)/2:black[vout]"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--style", choices=["aesthetic", "gritty"], default="aesthetic")
    ap.add_argument("--title", default="")
    ap.add_argument("--subtitle", default="")
    ap.add_argument("--slowmo", help="START-END seconds to slow down, e.g. 3.2-4.6")
    ap.add_argument("--slowmo-factor", type=float, default=0.4)
    ap.add_argument("--music", help="audio file to lay under the edit")
    ap.add_argument("--music-start", type=float, default=0.0)
    ap.add_argument("--keep-audio", action="store_true", help="keep original clip audio")
    a = ap.parse_args()

    dur, has_audio = probe(a.input)
    g, lbl, out_dur = speed_graph(dur, a.slowmo, a.slowmo_factor)
    style = style_aesthetic if a.style == "aesthetic" else style_gritty
    g += ";" + style(lbl, a.title, a.subtitle)
    g += f";[vout]fade=t=in:st=0:d=0.25,fade=t=out:st={max(0, out_dur - 0.4):.2f}:d=0.4[vf]"

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", a.input]
    maps = ["-map", "[vf]"]
    if a.music:
        cmd += ["-ss", str(a.music_start), "-i", a.music]
        g += (f";[1:a]atrim=0:{out_dur:.2f},afade=t=in:d=0.2,"
              f"afade=t=out:st={max(0, out_dur - 0.8):.2f}:d=0.8[af]")
        maps += ["-map", "[af]"]
    elif a.keep_audio and has_audio and not a.slowmo:
        maps += ["-map", "0:a"]
    cmd += ["-filter_complex", g, *maps, "-r", "60", "-c:v", "libx264", "-preset", "medium",
            "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", "-shortest", a.output]
    r = subprocess.run(cmd)
    sys.exit(r.returncode)


if __name__ == "__main__":
    main()
