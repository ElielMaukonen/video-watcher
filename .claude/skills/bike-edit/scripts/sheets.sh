#!/usr/bin/env bash
# Contact sheets for picking cut points.
#   sheets.sh overview RAW_DIR OUT_DIR   -> 6 frames per clip, grouped 5 clips per image
#   sheets.sh dense    RAW_DIR OUT_DIR CLIP...  -> ~5 fps, timestamped, one image per clip
set -euo pipefail
mode=$1 raw=$2 out=$3; shift 3
FONT=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf
mkdir -p "$out"
dur() { ffprobe -v error -show_entries format=duration -of csv=p=0 "$1"; }
if [ "$mode" = overview ]; then
  for f in "$raw"/*.[mM][oO4][vV4]; do n=$(basename "${f%.*}"); d=$(dur "$f")
    ffmpeg -y -loglevel error -i "$f" -vf "fps=6/$d,scale=-2:300,drawtext=fontfile=$FONT:text='%{pts\:hms}':x=4:y=4:fontsize=14:fontcolor=yellow:box=1:boxcolor=black,tile=6x1" -frames:v 1 -update 1 "$out/$n.jpg"
  done
  python3 - "$out" <<'PY'
import glob, os, subprocess, sys
o = sys.argv[1]; F = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
fs = sorted(f for f in glob.glob(f"{o}/*.jpg") if not os.path.basename(f).startswith("G_"))
for i in range(0, len(fs), 5):
    g = fs[i:i+5]; args = sum((["-i", f] for f in g), [])
    lab = "".join(f"[{j}:v]scale=1080:-2,drawtext=fontfile={F}:text={os.path.basename(f)[:-4]}:x=w-tw-6:y=6:fontsize=22:fontcolor=white:box=1:boxcolor=red[v{j}];" for j, f in enumerate(g))
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args, "-filter_complex",
                    lab + "".join(f"[v{j}]" for j in range(len(g))) + f"vstack=inputs={len(g)}", f"{o}/G_{i//5}.jpg"], check=True)
PY
else
  for n in "$@"; do f=$(ls "$raw/$n".* | head -1); d=$(dur "$f"); r=$(python3 -c "print(min(5, 48/$d))")
    ffmpeg -y -loglevel error -i "$f" -vf "fps=$r,scale=-2:240,drawtext=fontfile=$FONT:text='%{pts\:flt}':x=3:y=3:fontsize=13:fontcolor=yellow:box=1:boxcolor=black,tile=12x4" -frames:v 1 -update 1 "$out/$n.jpg"
  done
fi
