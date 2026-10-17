"""Check a real running service. Does not create synthetic model verdicts."""

import argparse
import os
import sys
import time
from pathlib import Path
from uuid import UUID

import httpx

# Allow direct `python scripts/smoke_test.py` in an editable checkout.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from backend.app.config import Settings  # noqa: E402
from backend.app.schemas import DoneJob, FailedJob  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    settings = Settings()
    if not args.video.is_file():
        print("Видео не найдено.", file=sys.stderr)
        return 2
    headers = {"X-API-Key": settings.api_key.get_secret_value()}
    started = time.monotonic()
    with httpx.Client(base_url=args.base_url, headers=headers, timeout=90) as client:
        ready = client.get("/readyz")
        if ready.status_code != 200 or not ready.json().get("ready"):
            print("Анализатор не готов. /readyz не вернул ready=true.", file=sys.stderr)
            return 2
        with args.video.open("rb") as stream:
            response = client.post(
                "/api/v1/analyze", files={"file": (args.video.name, stream, "video/quicktime")}
            )
        response.raise_for_status()
        job_id = str(UUID(response.json()["job_id"]))
        print(f"Создана задача: {job_id}")
        while time.monotonic() - started < 300:
            response = client.get(f"/api/v1/jobs/{job_id}")
            response.raise_for_status()
            body = response.json()
            if body["status"] == "done":
                result = DoneJob.model_validate(body)
                print(
                    f"Готово: {result.verdict}, событий: {len(result.violations)}, "
                    f"время: {time.monotonic() - started:.1f} с"
                )
                return 0
            if body["status"] == "failed":
                result = FailedJob.model_validate(body)
                print(f"Обработка завершилась ошибкой: {result.error.code}", file=sys.stderr)
                return 1
            time.sleep(1)
    print("За 300 секунд результат не получен.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (httpx.HTTPError, ValueError):
        print("Ошибка подключения или формата ответа. Проверьте сервер и ключ.", file=sys.stderr)
        raise SystemExit(1) from None
