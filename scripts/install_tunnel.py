"""Install a checksum-verified official cloudflared binary inside ignored .runtime/."""

import argparse
import hashlib
import json
import os
import platform
import tarfile
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VERSION = "2026.10.1"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=DEFAULT_VERSION)
    args = parser.parse_args()
    system = {"Darwin": "darwin", "Linux": "linux", "Windows": "windows"}.get(platform.system())
    arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "amd64", "AMD64": "amd64"}.get(
        platform.machine()
    )
    if not system or not arch:
        raise SystemExit(
            "Unsupported platform; install cloudflared from Cloudflare's official site."
        )
    suffix = ".tgz" if system == "darwin" else ".exe" if system == "windows" else ""
    asset_name = f"cloudflared-{system}-{arch}{suffix}"
    target_dir = ROOT / ".runtime/bin"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / ("cloudflared.exe" if system == "windows" else "cloudflared")
    metadata = target_dir / "cloudflared-install.json"
    if target.is_file() and metadata.is_file():
        installed = json.loads(metadata.read_text())
        if (
            installed["version"] == args.version
            and hashlib.sha256(target.read_bytes()).hexdigest() == installed["binary_sha256"]
        ):
            print(f"Verified cloudflared {args.version} is already installed: {target}")
            return
    url = f"https://api.github.com/repos/cloudflare/cloudflared/releases/tags/{args.version}"
    request = Request(url, headers={"User-Agent": "forte-cash-monitor-local-setup"})
    with urlopen(request, timeout=30) as response:
        release = json.load(response)
    asset = next((item for item in release["assets"] if item["name"] == asset_name), None)
    if not asset or not (asset.get("digest") or "").startswith("sha256:"):
        raise SystemExit("No official asset/checksum for this platform; installation stopped.")
    expected = asset["digest"].removeprefix("sha256:")
    download = target_dir / "cloudflared-download.part"
    digest = hashlib.sha256()
    try:
        print(
            f"Downloading official cloudflared {args.version} ({asset['size']} bytes)…", flush=True
        )
        with (
            urlopen(asset["browser_download_url"], timeout=60) as response,
            download.open("wb") as out,
        ):
            size = 0
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > 100 * 1024 * 1024:
                    raise ValueError("Unexpectedly large download")
                digest.update(chunk)
                out.write(chunk)
        if digest.hexdigest() != expected:
            raise ValueError("Official asset SHA-256 mismatch; binary will not be installed")
        if system == "darwin":
            with tarfile.open(download) as archive:
                members = [item for item in archive.getmembers() if item.name == "cloudflared"]
                if len(members) != 1 or not members[0].isfile():
                    raise ValueError("Unexpected archive layout")
                binary = archive.extractfile(members[0]).read()
        else:
            binary = download.read_bytes()
        temporary = target.with_suffix(".new")
        temporary.write_bytes(binary)
        os.chmod(temporary, 0o755)
        temporary.replace(target)
        metadata.write_text(
            json.dumps(
                {
                    "version": args.version,
                    "source": release["html_url"],
                    "asset": asset_name,
                    "asset_sha256": expected,
                    "binary_sha256": hashlib.sha256(binary).hexdigest(),
                },
                indent=2,
            )
            + "\n"
        )
        print(f"Installed verified binary: {target}")
    finally:
        download.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
