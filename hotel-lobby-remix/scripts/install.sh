#!/usr/bin/env bash
# Install (or refresh) Hotel Lobby Remix as a systemd service on a PromptQL bot VM (v2).
# Usage, from the VM shell (where PROMPTQL_PLATFORM_API_URL is set):
#   ./scripts/install.sh [HF_PROVIDER] [HF_PROVIDER_LABEL]
# Defaults target the Playground Project: higgsfield-user-private / "Higgsfield (user private)".
set -eu
cd "$(dirname "$0")/.."
APP=$(pwd)
: "${PROMPTQL_PLATFORM_API_URL:?not set — run this from the bot VM shell}"
PORT="${PORT:-8790}"
HF_PROVIDER="${1:-${HF_PROVIDER:-higgsfield-user-private}}"
HF_PROVIDER_LABEL="${2:-${HF_PROVIDER_LABEL:-Higgsfield (user private)}}"

for t in python3 ffmpeg ffprobe; do command -v "$t" >/dev/null || { echo "missing dependency: $t (sudo apt-get install -y ffmpeg)"; exit 1; }; done
[ -s static/samples/hotel-lobby.mp4 ] || { echo "missing static/samples/hotel-lobby.mp4 (it is committed — re-clone?)"; exit 1; }

cat > .env <<EOF
PORT="$PORT"
PROMPTQL_PLATFORM_API_URL="$PROMPTQL_PLATFORM_API_URL"
HF_PROVIDER="$HF_PROVIDER"
HF_PROVIDER_LABEL="$HF_PROVIDER_LABEL"
EOF

sed -e "s#@APP@#$APP#g" -e "s#@USER@#$(id -un)#g" hotel-lobby-remix.service \
  | sudo tee /etc/systemd/system/hotel-lobby-remix.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable hotel-lobby-remix.service >/dev/null
sudo systemctl restart hotel-lobby-remix.service
sleep 1
printf 'readyz -> '; curl -s -o /dev/null -w '%{http_code}\n' "http://127.0.0.1:$PORT/readyz"
echo "installed: $APP  provider=$HF_PROVIDER  port=$PORT"
echo "next: publish the app artifact (see README → Publishing)"