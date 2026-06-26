# * Testes do módulo cache.py — cache persistente de downloads.
# * Cobre: cache_stats, cache_clean (seletivo), cache_list, _dir_stats.
# * Arquivo: tests/test_cache.py

"""Testes do módulo cache.py."""

import tempfile
import unittest
from pathlib import Path

from thornspkg.cache import (
    CacheStats,
    cache_clean,
    cache_list,
    cache_stats,
    indexes_cache_dir,
    packages_cache_dir,
    sources_cache_dir,
)
from thornspkg.config import Config


def make_test_cfg(td: str) -> Config:
    """Cria uma Config com cache_root dentro de td."""
    cache_root = Path(td) / "cache"
    return Config(
        recipes_dir=Path(td) / "recipes",
        patches_dir=Path(td) / "patches",
        sources_dir=cache_root / "sources",
        build_dir=Path(td) / "build",
        db_dir=Path(td) / "db",
        root_dir=Path(td) / "root",
        prefix="/usr",
        hooks_dir=Path(td) / "hooks",
        jobs=1,
    )


def populate_cache(cfg, *, sources=0, packages=0, indexes=0):
    """Cria arquivos fake no cache."""
    for d, n, prefix in [
        (sources_cache_dir(cfg), sources, "src"),
        (packages_cache_dir(cfg), packages, "pkg"),
        (indexes_cache_dir(cfg), indexes, "idx"),
    ]:
        d.mkdir(parents=True, exist_ok=True)
        for i in range(n):
            (d / f"{prefix}_{i}.bin").write_bytes(b"x" * (i + 1) * 100)


class TestCacheStats(unittest.TestCase):

    def test_empty_cache(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = make_test_cfg(td)
            stats = cache_stats(cfg)
            self.assertEqual(stats.total_count, 0)
            self.assertEqual(stats.total_size, 0)

    def test_populated_cache(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = make_test_cfg(td)
            populate_cache(cfg, sources=2, packages=1, indexes=1)
            stats = cache_stats(cfg)
            self.assertEqual(stats.sources_count, 2)
            self.assertEqual(stats.packages_count, 1)
            self.assertEqual(stats.indexes_count, 1)
            self.assertGreater(stats.total_size, 0)

    def test_human_size(self):
        stats = CacheStats()
        self.assertEqual(stats.human_size(0), "0.0 B")
        self.assertEqual(stats.human_size(1024), "1.0 KB")
        self.assertEqual(stats.human_size(1024 * 1024), "1.0 MB")


class TestCacheClean(unittest.TestCase):

    def test_clean_packages_only(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = make_test_cfg(td)
            populate_cache(cfg, sources=2, packages=1, indexes=1)
            removed = cache_clean(cfg, sources=False, packages=True, indexes=False)
            self.assertEqual(removed, 1)
            stats = cache_stats(cfg)
            self.assertEqual(stats.sources_count, 2)  # intact
            self.assertEqual(stats.packages_count, 0)  # cleaned
            self.assertEqual(stats.indexes_count, 1)  # intact

    def test_clean_default_no_indexes(self):
        """Por padrão, indexes não é limpo."""
        with tempfile.TemporaryDirectory() as td:
            cfg = make_test_cfg(td)
            populate_cache(cfg, sources=1, packages=1, indexes=1)
            cache_clean(cfg)  # defaults
            stats = cache_stats(cfg)
            self.assertEqual(stats.sources_count, 0)
            self.assertEqual(stats.packages_count, 0)
            self.assertEqual(stats.indexes_count, 1)  # intact

    def test_clean_all_including_indexes(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = make_test_cfg(td)
            populate_cache(cfg, sources=1, packages=1, indexes=1)
            cache_clean(cfg, sources=True, packages=True, indexes=True)
            stats = cache_stats(cfg)
            self.assertEqual(stats.total_count, 0)


class TestCacheList(unittest.TestCase):

    def test_list_empty(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = make_test_cfg(td)
            files = cache_list(cfg)
            self.assertEqual(len(files["sources"]), 0)
            self.assertEqual(len(files["packages"]), 0)
            self.assertEqual(len(files["indexes"]), 0)

    def test_list_with_files(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = make_test_cfg(td)
            populate_cache(cfg, sources=3, packages=2, indexes=1)
            files = cache_list(cfg)
            self.assertEqual(len(files["sources"]), 3)
            self.assertEqual(len(files["packages"]), 2)
            self.assertEqual(len(files["indexes"]), 1)


if __name__ == "__main__":
    unittest.main()
