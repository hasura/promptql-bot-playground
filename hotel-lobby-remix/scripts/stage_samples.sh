#!/usr/bin/env bash
# Optional: regenerate the poster frame and samples.json from the bundled clip.
# static/samples/hotel-lobby.mp4, hotel-lobby.jpg and samples.json are all committed,
# so a fresh clone needs nothing from here. Needs ffmpeg/ffprobe/jq.
set -eu
cd "$(dirname "$0")/.."
f=static/samples/hotel-lobby.mp4
[ -s "$f" ] || { echo "missing $f"; exit 1; }
d=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$f")
IFS=, read -r w h < <(ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=p=0 "$f")
ffmpeg -v error -y -ss 2.0 -i "$f" -frames:v 1 -vf "scale=640:-2" static/samples/hotel-lobby.jpg
jq -n --arg d "$d" --arg w "$w" --arg h "$h" \
  --arg title "Hotel Lobby — the orange room" \
  --arg credit "Quavo & Takeoff · A COLORS SHOW, 2022" \
  --arg license "third-party footage; posting rights are the poster's call" \
  '[{id:"hotel-lobby", title:$title, tagline:"the original", credit:$credit, license:$license,
     file:"/samples/hotel-lobby.mp4", poster:"/samples/hotel-lobby.jpg",
     duration:($d|tonumber|.*100|round/100), width:($w|tonumber), height:($h|tonumber)}]' \
  > static/samples/samples.json
jq -c '.[]|{id,duration,width,height}' static/samples/samples.json