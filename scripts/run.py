"""Run the single-worker local service from any current directory."""

import argparse
import os
from pathlib import Path

import uvicorn

parser = argparse.ArgumentParser()
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--port", type=int, default=8000)
args = parser.parse_args()
os.chdir(Path(__file__).resolve().parents[1])
uvicorn.run("backend.app.main:create_app", factory=True, host=args.host, port=args.port)
