"""Generate local configuration without printing the API key or overwriting a file."""

import os
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
target = root / ".env"
template = (root / ".env.example").read_text(encoding="utf-8")
if target.exists():
    print(".env already exists; left unchanged.")
else:
    content = template.replace("replace-with-a-random-local-secret", secrets.token_urlsafe(32))
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(content)
    print("Created local .env with a random API key. The key was not printed.")
