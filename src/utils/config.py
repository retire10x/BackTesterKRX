"""프로젝트 YAML 설정 SSOT 래퍼 (v4.0 등)."""
from __future__ import annotations

from src.v4_config import V4Config, load_v4_config

__all__ = ["load_v4_config", "get_v4_settings", "V4Config"]


def get_v4_settings(cfg: dict | None = None) -> V4Config:
    """v4.0 설정 블록 로드 (settings.yaml)."""
    return load_v4_config(cfg)


def __getattr__(name: str):
  if name == "settings":
    return get_v4_settings()
  raise AttributeError(name)
