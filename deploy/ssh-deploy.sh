#!/usr/bin/env bash
set -euo pipefail
umask 077

: "${VPN_SSH_HOST:?Set VPN_SSH_HOST}"
: "${VPN_SSH_USER:?Set VPN_SSH_USER}"
: "${VPN_SSH_PRIVATE_KEY:?Set VPN_SSH_PRIVATE_KEY}"
: "${VPN_SSH_KNOWN_HOSTS:?Set VPN_SSH_KNOWN_HOSTS}"
ssh_port="${VPN_SSH_PORT:-22}"

if [[ ! $VPN_SSH_HOST =~ ^[A-Za-z0-9][A-Za-z0-9.-]*$ ||
      ! $VPN_SSH_USER =~ ^[A-Za-z_][A-Za-z0-9_-]*$ ||
      ! $ssh_port =~ ^[1-9][0-9]{0,4}$ ]] || (( ssh_port > 65535 )); then
    echo 'Invalid SSH host, user, or port.' >&2
    exit 1
fi

temp_dir="$(mktemp -d)"
target="$VPN_SSH_USER@$VPN_SSH_HOST"
ssh_options=(
    -F /dev/null
    -o "Port=$ssh_port"
    -o BatchMode=yes
    -o IdentitiesOnly=yes
    -o StrictHostKeyChecking=yes
    -o HostKeyAlias=happ-vless-vps
    -o "UserKnownHostsFile=$temp_dir/known_hosts"
    -o GlobalKnownHostsFile=/dev/null
    -o ConnectTimeout=15
    -o ServerAliveInterval=15
    -o ServerAliveCountMax=3
    -i "$temp_dir/key"
)
stage=''
cleanup() {
    if [[ -n $stage ]]; then
        ssh "${ssh_options[@]}" "$target" "rm -rf -- $stage" >/dev/null 2>&1 || true
    fi
    rm -rf -- "$temp_dir"
}
trap cleanup EXIT

printf '%s\n' "$VPN_SSH_PRIVATE_KEY" > "$temp_dir/key"
printf '%s\n' "$VPN_SSH_KNOWN_HOSTS" > "$temp_dir/known_hosts"
unset VPN_SSH_PRIVATE_KEY VPN_SSH_KNOWN_HOSTS
ssh-keygen -y -P '' -f "$temp_dir/key" >/dev/null
ssh-keygen -F happ-vless-vps -f "$temp_dir/known_hosts" >/dev/null

python3 - "$temp_dir/settings.json" <<'PY'
import json
import os
from pathlib import Path
import sys

env = os.environ
settings = {
    "server": env.get("VPN_PUBLIC_HOST") or env["VPN_SSH_HOST"],
    "port": env.get("VPN_PORT") or "443",
    "sni": env.get("VPN_SNI") or "www.microsoft.com",
    "name": env.get("VPN_NAME") or "Мой VPN",
}
Path(sys.argv[1]).write_text(json.dumps(settings), encoding="utf-8")
PY

remote_path="$(ssh "${ssh_options[@]}" "$target" 'umask 077; mktemp -d .happ-vless-upload.XXXXXXXX')"
if [[ ! $remote_path =~ ^\.happ-vless-upload\.[a-zA-Z0-9]{8}$ ]]; then
    echo 'Unexpected SSH upload directory.' >&2
    exit 1
fi
stage="$remote_path"
scp "${ssh_options[@]}" happ-vless.py "$temp_dir/settings.json" "$target:$stage/"
ssh "${ssh_options[@]}" "$target" bash -s -- "$stage" < deploy/remote-deploy.sh
stage=''
