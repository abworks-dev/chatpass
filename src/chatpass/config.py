"""Deployment configuration with safe local-only defaults."""

import math
import os
from dataclasses import dataclass, field
from ipaddress import ip_address
from pathlib import Path


def default_data_dir() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "ChatPass"
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "chatpass"


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=default_data_dir)
    host: str = "127.0.0.1"
    port: int = 19840
    upstream_base_url: str = field(default="https://api.openai.com/v1", init=False)
    max_body_bytes: int = 1048576
    max_concurrency: int = 8
    timeout_seconds: float = 120.0

    def __post_init__(self):
        try:
            local = ip_address(self.host).is_loopback
        except ValueError:
            local = False
        if not local:
            raise ValueError("This initial release only supports literal loopback addresses.")
        if not 1 <= self.port <= 65535:
            raise ValueError("Port must be between 1 and 65535.")
        if not 1 <= self.max_concurrency <= 64 or not 1024 <= self.max_body_bytes <= 16777216:
            raise ValueError("Invalid concurrency or request-size limit.")
        if not math.isfinite(self.timeout_seconds) or not 1 <= self.timeout_seconds <= 600:
            raise ValueError("Timeout must be finite and between 1 and 600 seconds.")
