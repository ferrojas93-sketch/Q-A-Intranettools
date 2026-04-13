"""Shared test fixtures: isolated SQLite cache per test."""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture()
def cache_module(tmp_path, monkeypatch):
    """Fresh cache module backed by a tmp_path SQLite DB."""
    from qa_intranet import cache as cache_mod
    from qa_intranet import config as config_mod

    # Reset module-level engine/sessionmaker between tests.
    cache_mod._engine = None
    cache_mod._SessionLocal = None

    monkeypatch.setattr(config_mod, "CACHE_DB_PATH", tmp_path / "cache.db")
    monkeypatch.setattr(cache_mod, "CACHE_DB_PATH", tmp_path / "cache.db")

    importlib.reload(cache_mod)
    cache_mod._engine = None
    cache_mod._SessionLocal = None
    monkeypatch.setattr(cache_mod, "CACHE_DB_PATH", tmp_path / "cache.db")
    cache_mod.get_engine()
    return cache_mod
