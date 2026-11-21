#!/usr/bin/env python3
"""Install and run a single Xray VLESS REALITY profile on a Linux VPS."""

import argparse
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote, urlencode
import uuid

IMAGE = "ghcr.io/xtls/xray-core:26.11.9"
CONTAINER = "happ-vless"
BACKUP_CONTAINER = "happ-vless-previous"
OWNER_LABEL = "local.happ-vless.state"
CONTAINER_PORT = 8443
FLOW = "xtls-rprx-vision"
ROOT = Path(__file__).resolve().parent
STATE = ROOT / "state"
PROFILE = STATE / "profile"

DOCKER_OPTIONS = [
    "--cap-drop=ALL", "--security-opt=no-new-privileges:true", "--read-only",
    "--user", f"{os.getuid()}:{os.getgid()}",
]


def docker(*args, capture=False):
    result = subprocess.run(
        ["docker", *args], check=True, text=True,
        stdout=subprocess.PIPE if capture else sys.stderr,
    )
    return result.stdout.strip() if capture else None


def host(value, *, require_domain=False):
    value = value.lower().removesuffix(".")
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        labels = value.split(".")
        label = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
        if (len(value) <= 253 and len(labels) >= 2
                and all(label.fullmatch(part) for part in labels)
                and not labels[-1].isdigit()):
            return value
    else:
        if not require_domain and address.version == 4:
            return str(address)
    raise argparse.ArgumentTypeError("Укажите IPv4 или полное доменное имя.")


def sni_host(value):
    return host(value, require_domain=True)


def tcp_port(value):
    try:
        port = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Порт должен быть целым числом.") from error
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("Порт должен быть в диапазоне 1–65535.")
    return port


def reality_keys():
    output = docker("run", "--rm", *DOCKER_OPTIONS, "--network=none", IMAGE,
                    "x25519", capture=True)
    fields = {}
    for line in output.splitlines():
        name, separator, value = line.partition(":")
        if separator:
            fields[name.strip().casefold()] = value.strip()

    private_key = fields.get("privatekey") or fields.get("private key")
    public_key = (
        fields.get("password (publickey)") or fields.get("password")
        or fields.get("publickey") or fields.get("public key")
    )
    if not all(key and re.fullmatch(r"[A-Za-z0-9_-]{43}", key)
               for key in (private_key, public_key)):
        raise ValueError("Xray не вернул ожидаемую пару ключей X25519.")
    return private_key, public_key


def config_mount(profile):
    return f"type=bind,src={profile / 'config.json'},dst=/config.json,readonly"


def validate_config(profile):
    docker("run", "--rm", *DOCKER_OPTIONS, "--network=none",
           "--mount", config_mount(profile), IMAGE,
           "run", "-test", "-config", "/config.json")


def create_profile(settings):
    private_key, public_key = reality_keys()
    client_id = str(uuid.uuid4())
    short_id = secrets.token_hex(8)
    sni = settings["sni"]
    config = {
        "log": {"loglevel": "warning"},
        "inbounds": [{
            "listen": "0.0.0.0",
            "port": CONTAINER_PORT,
            "protocol": "vless",
            "settings": {
                "clients": [{"id": client_id, "flow": FLOW}],
                "decryption": "none",
            },
            "streamSettings": {
                "network": "raw",
                "security": "reality",
                "realitySettings": {
                    "target": f"{sni}:443",
                    "serverNames": [sni],
                    "privateKey": private_key,
                    "shortIds": [short_id],
                },
            },
        }],
        "outbounds": [{"protocol": "freedom"}],
    }
    query = urlencode({
        "type": "tcp", "encryption": "none", "security": "reality",
        "flow": FLOW, "sni": sni, "fp": "chrome", "pbk": public_key,
        "sid": short_id, "spx": "/",
    })
    label = quote(settings["name"], safe="")
    link = f"vless://{client_id}@{settings['server']}:{settings['port']}?{query}#{label}"

    pending = Path(tempfile.mkdtemp(prefix=".pending-", dir=STATE))
    try:
        (pending / "config.json").write_text(
            json.dumps(config, indent=2) + "\n", encoding="utf-8",
        )
        (pending / "settings.json").write_text(
            json.dumps(settings, ensure_ascii=False) + "\n", encoding="utf-8",
        )
        (pending / "happ-url.txt").write_text(link + "\n", encoding="utf-8")
        validate_config(pending)
        pending.rename(PROFILE)
    finally:
        if pending.exists():
            shutil.rmtree(pending)


def current_container(name=CONTAINER):
    container_id = docker(
        "container", "ls", "--all", "--filter", f"name=^/{name}$",
        "--format", "{{.ID}}", capture=True,
    )
    if not container_id:
        return None
    details = json.loads(docker("container", "inspect", container_id, capture=True))[0]
    if (details["Config"].get("Labels") or {}).get(OWNER_LABEL) != str(STATE):
        raise ValueError(f"Имя контейнера {name} занято другой установкой.")
    return details


def ensure_container(settings, existing):
    if existing is None:
        docker(
            "run", "--detach", "--name", CONTAINER, "--restart=unless-stopped",
            "--label", f"{OWNER_LABEL}={STATE}", *DOCKER_OPTIONS,
            "--log-opt", "max-size=10m", "--log-opt", "max-file=3",
            "--publish", f"0.0.0.0:{settings['port']}:{CONTAINER_PORT}/tcp",
            "--mount", config_mount(PROFILE), IMAGE,
            "run", "-config", "/config.json",
        )
    elif not existing["State"]["Running"]:
        docker("container", "start", CONTAINER)


def port_is_listening(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def wait_for_listener(port):
    for _ in range(20):
        if port_is_listening(port):
            details = current_container()
            if details and details["State"]["Running"] and not details["State"]["Restarting"]:
                return
        time.sleep(0.5)
    raise ValueError(f"Xray не открыл TCP-порт {port}. Проверьте docker logs {CONTAINER}.")


def restore_previous_container():
    if current_container() is not None:
        docker("container", "rm", "--force", CONTAINER)
    docker("container", "rename", BACKUP_CONTAINER, CONTAINER)
    if not current_container()["State"]["Running"]:
        docker("container", "start", CONTAINER)


def update_container(settings, existing):
    if existing is None or existing["Config"]["Image"] == IMAGE:
        ensure_container(settings, existing)
        wait_for_listener(settings["port"])
        return

    validate_config(PROFILE)
    docker("container", "rename", CONTAINER, BACKUP_CONTAINER)
    try:
        if existing["State"]["Running"]:
            docker("container", "stop", BACKUP_CONTAINER)
        ensure_container(settings, None)
        wait_for_listener(settings["port"])
    except (OSError, ValueError, subprocess.CalledProcessError, KeyboardInterrupt):
        print("Новый контейнер не запустился. Восстанавливаю предыдущий.", file=sys.stderr)
        restore_previous_container()
        wait_for_listener(settings["port"])
        raise
    docker("container", "rm", BACKUP_CONTAINER)


def show_link():
    if not PROFILE.is_dir():
        raise ValueError("Профиль ещё не создан. Сначала запустите команду install.")
    print((PROFILE / "happ-url.txt").read_text(encoding="utf-8"), end="")


def install(settings, *, quiet=False):
    os.umask(0o077)
    STATE.mkdir(mode=0o700, exist_ok=True)
    with (STATE / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if current_container(BACKUP_CONTAINER) is not None:
            if not PROFILE.is_dir():
                raise ValueError("Найдена предыдущая версия контейнера, но профиль отсутствует.")
            restore_previous_container()
        existing = current_container()
        if PROFILE.exists():
            saved = json.loads((PROFILE / "settings.json").read_text(encoding="utf-8"))
            if saved != settings:
                raise ValueError(
                    "Профиль уже создан с другими параметрами. "
                    "Повторите исходную команду или выведите ссылку командой link."
                )
        else:
            if existing is not None:
                raise ValueError("Контейнер существует, но папка state/profile отсутствует.")
            if port_is_listening(settings["port"]):
                raise ValueError("Выбранный TCP-порт уже занят. Укажите свободный --port.")
            create_profile(settings)
        update_container(settings, existing)
        if not quiet:
            show_link()


def deploy(settings_file):
    values = json.loads(settings_file.read_text(encoding="utf-8"))
    settings = {
        "server": host(values["server"]),
        "port": tcp_port(values["port"]),
        "sni": sni_host(values["sni"]),
        "name": values["name"],
    }
    install(settings, quiet=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("install", help="Создать профиль и запустить Xray")
    setup.add_argument("server", type=host, help="Публичный IPv4 или домен VPS с A-записью")
    setup.add_argument("--port", type=tcp_port, default=443, help="Внешний TCP-порт (443)")
    setup.add_argument("--sni", type=sni_host, default="www.microsoft.com",
                       help="Домен цели REALITY (www.microsoft.com)")
    setup.add_argument("--name", default="Мой VPN", help="Имя профиля в Happ")
    ci = commands.add_parser("deploy", help="Установить профиль из файла настроек CI")
    ci.add_argument("settings_file", type=Path)
    commands.add_parser("link", help="Повторно показать ссылку для Happ")
    args = parser.parse_args()

    try:
        if args.command == "link":
            show_link()
        elif args.command == "deploy":
            deploy(args.settings_file)
        else:
            settings = {key: value for key, value in vars(args).items() if key != "command"}
            install(settings)
    except (OSError, ValueError, argparse.ArgumentTypeError, KeyError,
            subprocess.CalledProcessError) as error:
        parser.exit(1, f"Ошибка: {error}\n")


if __name__ == "__main__":
    main()
