"""ForteBank Datathon 2026 cash-room violation detector."""
from forte_detector import config as _config

_config.apply_offline_env()  # before any submodule imports transformers / huggingface_hub
