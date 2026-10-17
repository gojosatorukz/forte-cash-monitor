"""Expose only the local HTTP service via a temporary Cloudflare Quick Tunnel."""

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.config import Settings  # noqa: E402

QUICK_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def write_private(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".private-")
    try:
        os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def export_access(base_url: str, api_key: str, directory: Path, model_ready: bool) -> Path:
    if not QUICK_URL.fullmatch(base_url):
        raise ValueError("Expected the generated HTTPS trycloudflare.com origin")
    directory = directory.resolve()
    if directory.is_relative_to(ROOT) and not directory.is_relative_to(ROOT / ".runtime"):
        raise ValueError("Access credentials must be outside tracked source or inside .runtime/")
    target = directory / "Доступ_для_жюри.txt"
    model = (
        "Анализатор подключён. Проверить полный анализ и время на видео перед сдачей."
        if model_ready
        else (
            "Модель ещё не подключена. Анализ пока отвечает 503. "
            "Это подготовка доступа, не готовое AI-решение."
        )
    )
    content = f"""FORTE CASH MONITOR · DATATHON 26
ПОДГОТОВЛЕННЫЙ ДОСТУП К СЕРВИСУ

Сайт: {base_url}/
API base URL: {base_url}
Документация API: {base_url}/docs
OpenAPI: {base_url}/openapi.json
Сервер: {base_url}/healthz
Готовность модели: {base_url}/readyz

Авторизация: заголовок X-API-Key
X-API-Key: {api_key}

POST {base_url}/api/v1/analyze
  multipart-поле file, видео .mov
  Ответ: {{"job_id":"…"}}
GET {base_url}/api/v1/jobs/{{job_id}}
  Статусы: processing, done, failed
Оба метода требуют X-API-Key. Ключ вводится также на сайте кнопкой «Подключить ключ».

СТАТУС НА МОМЕНТ СОЗДАНИЯ ФАЙЛА
{model}

ПЕРЕД ОТПРАВКОЙ ЖЮРИ
Согласовать с организаторами допустимость HTTPS-туннеля для закрытых тестовых видео.
Проверить полный анализ через этот адрес и доступ с мобильного интернета.
Завершение обработки должно укладываться в 5 минут, в том числе с учётом очереди.

АДРЕС ВРЕМЕННЫЙ
Он действует, пока запущен именно этот cloudflared-процесс. Перезапуск Python-сервиса
адрес не меняет; перезапуск туннеля выдаст другой адрес. Ноутбук, интернет и сервис
должны работать во время проверки. Не закрывайте крышку; держите ноутбук на питании.
Quick Tunnel не гарантирует бесперебойную работу. Для постоянного адреса нужен named
Tunnel с собственным доменом либо другой согласованный способ публикации.

Файл содержит секрет доступа. Передавайте его жюри отдельно, не загружайте в GitHub.
Создано (UTC): {datetime.now(UTC).isoformat(timespec="seconds")}
"""
    write_private(target, content)
    return target


@contextmanager
def single_instance(path: Path):
    with path.open("a+b") as stream:
        try:
            if os.name == "nt":
                import msvcrt

                stream.seek(0)
                stream.write(b"0")
                stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise SystemExit(
                "A public-link process is already running. Keep its existing URL."
            ) from exc
        yield


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000, metavar="PORT")
    parser.add_argument("--handoff-dir", type=Path, default=ROOT / ".runtime/access")
    parser.add_argument(
        "--refresh-access",
        action="store_true",
        help="Refresh the private handoff file without restarting the tunnel",
    )
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("--port must be between 1024 and 65535")
    os.chdir(ROOT)
    settings = Settings()
    origin = f"http://127.0.0.1:{args.port}"
    try:
        with urlopen(f"{origin}/healthz", timeout=5) as response:
            if json.load(response).get("service") != "forte-cash-monitor":
                raise ValueError("The origin is not Forte Cash Monitor")
    except (OSError, ValueError) as exc:
        raise SystemExit("Start the local service first: python scripts/run.py") from exc
    model_ready = False
    try:
        with urlopen(f"{origin}/readyz", timeout=5) as response:
            model_ready = bool(json.load(response).get("ready"))
    except HTTPError:
        pass
    if args.refresh_access:
        state_file = ROOT / ".runtime/public-link.json"
        if not state_file.is_file():
            raise SystemExit("No public link is registered yet.")
        state = json.loads(state_file.read_text())
        if not state.get("active") or not state.get("base_url"):
            raise SystemExit("The registered tunnel is not active.")
        target = export_access(
            state["base_url"], settings.api_key.get_secret_value(), args.handoff_dir, model_ready
        )
        print(f"Refreshed private access file: {target} (key not printed)")
        return 0
    binary_name = "cloudflared.exe" if os.name == "nt" else "cloudflared"
    binary = ROOT / ".runtime/bin" / binary_name
    if not binary.is_file():
        found = shutil.which(binary_name)
        if not found:
            raise SystemExit("Install the connector first: python scripts/install_tunnel.py")
        binary = Path(found)
    runtime = ROOT / ".runtime"
    runtime.mkdir(exist_ok=True)
    state_file = runtime / "public-link.json"
    logfile = runtime / "public-link.log"
    command = [
        str(binary),
        "tunnel",
        "--no-autoupdate",
        "--url",
        origin,
        "--protocol",
        "http2",
        "--edge-ip-version",
        "4",
        "--loglevel",
        "info",
        "--metrics",
        "127.0.0.1:0",
    ]
    with single_instance(runtime / "public-link.lock"):
        child = None
        keep_awake = None
        state = {
            "active": False,
            "base_url": None,
            "origin": origin,
            "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "supervisor_pid": os.getpid(),
        }

        def save_state():
            write_private(state_file, json.dumps(state, indent=2) + "\n")

        def interrupted(signum, frame):
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, interrupted)
        try:
            print("Starting HTTPS tunnel to local Forte Cash Monitor…", flush=True)
            child = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
            )
            state["connector_pid"] = child.pid
            save_state()
            if sys.platform == "darwin" and Path("/usr/bin/caffeinate").exists():
                keep_awake = subprocess.Popen(["/usr/bin/caffeinate", "-i", "-w", str(child.pid)])
            with logfile.open("a", encoding="utf-8") as log:
                os.chmod(logfile, 0o600)
                for line in child.stdout:
                    log.write(line)
                    log.flush()
                    match = QUICK_URL.search(line)
                    if match and state["base_url"] is None:
                        state["base_url"] = match.group()
                        access = export_access(
                            match.group(),
                            settings.api_key.get_secret_value(),
                            args.handoff_dir,
                            model_ready,
                        )
                        state["access_file"] = str(access)
                        save_state()
                        print(f"Public website: {match.group()}/", flush=True)
                        print(f"Public API: {match.group()}/api/v1/analyze", flush=True)
                        print(f"Private access file: {access} (key not printed)", flush=True)
                    if "Registered tunnel connection" in line:
                        state["active"] = True
                        save_state()
                        print(
                            "HTTPS connector is connected. Keep this process running.", flush=True
                        )
                    if " ERR " in line:
                        print(f"Tunnel reported a connection error; details: {logfile}", flush=True)
            return child.wait()
        except KeyboardInterrupt:
            print("Stopping the public tunnel…", flush=True)
            return 0
        finally:
            if child and child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
            if keep_awake and keep_awake.poll() is None:
                keep_awake.terminate()
                keep_awake.wait()
            state["active"] = False
            state["stopped_at"] = datetime.now(UTC).isoformat(timespec="seconds")
            save_state()


if __name__ == "__main__":
    raise SystemExit(main())
