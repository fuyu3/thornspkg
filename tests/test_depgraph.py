# * Testes do módulo depgraph.py — resolução de dependências com versionamento.
# * Cobre: resolve_order com constraints, detecção de VersionConflictError,
# *   reverse_deps com versionamento, dep_tree_lines com constraints.
# * Arquivo: tests/test_depgraph.py

"""Testes do módulo depgraph.py."""

import unittest
from pathlib import Path

from thornspkg.depgraph import (
    DependencyCycleError,
    MissingDependencyError,
    VersionConflictError,
    dep_tree_lines,
    resolve_order,
    reverse_deps,
)
from thornspkg.recipe import Recipe


def make_recipe(name, version, depends=None, optional_deps=None, provides=None):
    """Helper para criar receitas rapidamente."""
    return Recipe(
        name=name, version=version, path=Path(f"{name}.toml"),
        depends=depends or [], optional_deps=optional_deps or [],
        provides=provides or [],
    )


class TestResolveOrderVersioned(unittest.TestCase):

    def setUp(self):
        self.recipes = {
            "zlib": make_recipe("zlib", "1.3.1"),
            "openssl": make_recipe("openssl", "3.3.1", depends=["zlib>=1.0"]),
            "python": make_recipe("python", "3.12.4",
                                  depends=["openssl>=3.0", "zlib"]),
            "curl": make_recipe("curl", "8.9.1", depends=["openssl=3.3.1"]),
        }

    def test_basic_resolution(self):
        order = resolve_order(self.recipes, ["python"], installed_versions={})
        self.assertEqual(order, ["zlib", "openssl", "python"])

    def test_resolution_with_constraints(self):
        order = resolve_order(self.recipes, ["curl"], installed_versions={})
        # curl → openssl=3.3.1 → zlib>=1.0
        self.assertEqual(order, ["zlib", "openssl", "curl"])

    def test_version_conflict_raises(self):
        # zlib 0.5 instalado não satisfaz openssl>=1.0
        with self.assertRaises(VersionConflictError) as ctx:
            resolve_order(
                self.recipes, ["python"],
                installed={"zlib"},
                installed_versions={"zlib": "0.5"},
            )
        self.assertIn("zlib", str(ctx.exception))

    def test_version_conflict_eq_operator(self):
        # openssl 3.2.0 instalado não satisfaz curl=3.3.1
        with self.assertRaises(VersionConflictError):
            resolve_order(
                self.recipes, ["curl"],
                installed={"openssl"},
                installed_versions={"openssl": "3.2.0"},
            )

    def test_satisfied_constraint_no_conflict(self):
        # openssl 3.3.1 instalado satisfaz curl=3.3.1
        order = resolve_order(
            self.recipes, ["curl"],
            installed={"openssl"},
            installed_versions={"openssl": "3.3.1"},
        )
        # resolve_order retorna a ordem completa; chamador filtra
        self.assertIn("openssl", order)
        self.assertIn("curl", order)


class TestResolveOrderCycles(unittest.TestCase):

    def test_cycle_detected(self):
        recipes = {
            "a": make_recipe("a", "1.0", depends=["b"]),
            "b": make_recipe("b", "1.0", depends=["a"]),
        }
        with self.assertRaises(DependencyCycleError):
            resolve_order(recipes, ["a"])

    def test_missing_dependency(self):
        recipes = {
            "a": make_recipe("a", "1.0", depends=["nonexistent"]),
        }
        with self.assertRaises(MissingDependencyError):
            resolve_order(recipes, ["a"])


class TestReverseDeps(unittest.TestCase):

    def test_reverse_deps_with_versioning(self):
        recipes = {
            "openssl": make_recipe("openssl", "3.3.1"),
            "curl": make_recipe("curl", "8.9.1", depends=["openssl>=3.0"]),
            "python": make_recipe("python", "3.12", depends=["openssl"]),
        }
        rdeps = reverse_deps(recipes, "openssl")
        self.assertIn("curl", rdeps)
        self.assertIn("python", rdeps)


class TestDepTreeLines(unittest.TestCase):

    def test_tree_with_constraint(self):
        recipes = {
            "zlib": make_recipe("zlib", "1.3.1"),
            "openssl": make_recipe("openssl", "3.3.1", depends=["zlib>=1.0"]),
        }
        lines = dep_tree_lines(recipes, "openssl", {})
        # Deve incluir a versão de openssl e a constraint
        text = "\n".join(lines)
        self.assertIn("openssl", text)
        self.assertIn("3.3.1", text)
        self.assertIn("zlib", text)


if __name__ == "__main__":
    unittest.main()
