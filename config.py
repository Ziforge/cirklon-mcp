"""Configuration for Cirklon MCP server."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv


@dataclass
class CirklonConfig:
    """Configuration loaded from environment / .env file."""

    output_port: str = ""
    input_port: str = ""
    remote_channel: int = 16  # 1-16
    auto_connect: bool = False
    default_bpm: float = 120.0

    @classmethod
    def from_env(cls, env_path: str | None = None) -> CirklonConfig:
        load_dotenv(env_path)
        return cls(
            output_port=os.getenv("CIRKLON_OUTPUT_PORT", ""),
            input_port=os.getenv("CIRKLON_INPUT_PORT", ""),
            remote_channel=int(os.getenv("CIRKLON_REMOTE_CHANNEL", "16")),
            auto_connect=os.getenv("CIRKLON_AUTO_CONNECT", "false").lower()
            in ("true", "1", "yes"),
            default_bpm=float(os.getenv("CIRKLON_DEFAULT_BPM", "120")),
        )
