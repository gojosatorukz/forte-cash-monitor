"""Run the local website and HTTPS tunnel together in a user-owned terminal."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.public_link import QUICK_URL, single_instance  # noqa: E402

ORIGIN = "http://127.0.0.1:8000"


def service_running() -> bool:
    try:
        with urlopen(f"{ORIGIN}/healthz", timeout=3) as response:
            body = json.load(response)
    except HTTPError as exc:
        raise RuntimeError("Порт 8000 занят другим HTTP-сервисом.") from exc
    except URLError:
        return False
    except (ValueError, OSError) as exc:
        raise RuntimeError("Не удалось проверить сервис на порту 8000.") from exc
    if not isinstance(body, dict) or body.get("service") != "forte-cash-monitor":
        raise RuntimeError("Порт 8000 занят другим сервисом; он не будет остановлен.")
    return True


def existing_tunnel() -> str | None:
    """A cached active flag is insufficient: check the actual singleton lock."""
    try:
        with single_instance(ROOT / ".runtime/public-link.lock"):
            return None
    except SystemExit:
        state = json.loads((ROOT / ".runtime/public-link.json").read_text())
        url = state.get("base_url") or ""
        if not QUICK_URL.fullmatch(url):
            raise RuntimeError("Туннель уже запускается. Посмотрите его окно или лог.") from None
        return url


def stop_owned(process: subprocess.Popen | None) -> None:
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def main() -> int:
    os.chdir(ROOT)
    runtime = ROOT / ".runtime"
    runtime.mkdir(exist_ok=True)
    if not (ROOT / ".env").is_file():
        print("Сначала выполните: python scripts/init_env.py", flush=True)
        return 1
    if not (runtime / "bin/cloudflared").is_file():
        print("Сначала выполните: python scripts/install_tunnel.py", flush=True)
        return 1
    server = tunnel = None
    logfile = None
    try:
        if not service_running():
            descriptor = os.open(
                runtime / "service.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600
            )
            os.chmod(runtime / "service.log", 0o600)
            logfile = os.fdopen(descriptor, "a", encoding="utf-8")
            print("Запускаю сайт. Лог: .runtime/service.log", flush=True)
            server = subprocess.Popen(
                [sys.executable, "scripts/run.py"],
                stdout=logfile,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            deadline = time.monotonic() + 180
            while not service_running():
                if server.poll() is not None:
                    raise RuntimeError("Сервер не запустился. Проверьте .runtime/service.log.")
                if time.monotonic() > deadline:
                    raise RuntimeError("Запуск сервера занял больше 180 секунд. Проверьте лог.")
                time.sleep(0.25)
        url = existing_tunnel()
        if url:
            print(f"Туннель уже запущен. Зарегистрированный адрес: {url}/", flush=True)
            print("Повторный туннель не создан; сохраните работающее окно его запуска.", flush=True)
            if server is None:
                return 0
            return server.wait()
        print("Оставьте это окно открытым. Ctrl+C остановит запущенные здесь процессы.", flush=True)
        print(
            "Временная ссылка появится ниже. При новом запуске адрес может измениться.", flush=True
        )
        tunnel = subprocess.Popen(
            [
                sys.executable,
                "scripts/public_link.py",
                "--handoff-dir",
                str(ROOT.parent / "output/submission"),
            ],
            start_new_session=True,
        )
        while tunnel.poll() is None:
            if server is not None and server.poll() is not None:
                raise RuntimeError("Сервер остановился. Проверьте .runtime/service.log.")
            time.sleep(0.5)
        return tunnel.returncode
    except KeyboardInterrupt:
        print("\nОстанавливаю процессы, запущенные этим окном…", flush=True)
        return 0
    except (RuntimeError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr, flush=True)
        return 1
    finally:
        stop_owned(tunnel)
        stop_owned(server)
        if logfile is not None:
            logfile.close()


if __name__ == "__main__":
    raise SystemExit(main())
