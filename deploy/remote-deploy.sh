#!/usr/bin/env bash
set -euo pipefail
umask 077

stage="${1:?Missing upload directory}"
if [[ ! $stage =~ ^\.happ-vless-upload\.[a-zA-Z0-9]{8}$ ]]; then
    echo 'Invalid SSH upload directory.' >&2
    exit 1
fi
stage="$HOME/$stage"
deploy_dir="$HOME/happ-vless"
candidate=''
cleanup() {
    if [[ -n $candidate ]]; then
        rm -f -- "$candidate"
    fi
    rm -rf -- "$stage"
}
trap cleanup EXIT

mkdir -p -- "$deploy_dir"
exec 9>"$deploy_dir/.deploy.lock"
flock 9
candidate="$(mktemp "$deploy_dir/.installer.XXXXXXXX")"
cp -- "$stage/happ-vless.py" "$candidate"
python3 "$candidate" deploy "$stage/settings.json"
mv -f -- "$candidate" "$deploy_dir/happ-vless.py"
candidate=''
printf '%s\n' 'Xray is running and the published TCP port is accepting connections.'
