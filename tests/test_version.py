# * Testes do módulo version.py — parser e comparador de versões.
# * Cobre: parsing de versões simples, com epoch, com sufixo, com pré-release;
# *   parsing de constraints com operadores (>, >=, <, <=, =, !=, ==);
# *   função satisfies() com casos positivos e negativos;
# *   função compare() com ordenação correta.
# * Arquivo: tests/test_version.py

"""Testes do módulo version.py."""

import unittest

from thornspkg.version import (
    Constraint,
    Version,
    VersionError,
    compare,
    dep_name,
    parse_constraint,
    parse_version,
    satisfies,
)


class TestVersionParsing(unittest.TestCase):
    """Testa o parsing de strings de versão."""

    def test_simple_version(self):
        v = parse_version("3.12.4")
        self.assertEqual(v.numeric, (3, 12, 4))
        self.assertEqual(v.epoch, 0)
        self.assertEqual(v.suffix_rank, 0)

    def test_short_version(self):
        v = parse_version("9.1")
        self.assertEqual(v.numeric, (9, 1))

    def test_epoch_version(self):
        v = parse_version("1:5.0")
        self.assertEqual(v.epoch, 1)
        self.assertEqual(v.numeric, (5, 0))

    def test_release_suffix(self):
        v = parse_version("2.40-1")
        self.assertEqual(v.numeric, (2, 40))
        self.assertEqual(v.suffix_rank, 2)  # release suffix

    def test_prerelease_suffix(self):
        v = parse_version("3.5rc1")
        self.assertEqual(v.numeric, (3, 5))
        self.assertEqual(v.suffix_rank, -1)  # rc < release

    def test_alpha_suffix(self):
        v = parse_version("1.0alpha2")
        self.assertEqual(v.suffix_rank, -4)  # alpha < beta < rc < release

    def test_empty_version_raises(self):
        with self.assertRaises(VersionError):
            parse_version("")

    def test_invalid_version_raises(self):
        with self.assertRaises(VersionError):
            parse_version("abc")


class TestVersionComparison(unittest.TestCase):
    """Testa a comparação de versões."""

    def test_basic_compare(self):
        self.assertEqual(compare("1.2.3", "1.2.3"), 0)
        self.assertLess(compare("1.2.3", "1.2.4"), 0)
        self.assertGreater(compare("1.2.4", "1.2.3"), 0)

    def test_length_padding(self):
        # 1.0 == 1.0.0 (padding com zeros)
        self.assertEqual(compare("1.0", "1.0.0"), 0)

    def test_epoch_dominates(self):
        # 1:5.0 > 5.0 (epoch maior)
        self.assertGreater(compare("1:5.0", "5.0"), 0)
        self.assertLess(compare("5.0", "1:5.0"), 0)

    def test_prerelease_lt_release(self):
        # 1.2.3 > 1.2.3rc1 (release > pré-release)
        self.assertGreater(compare("1.2.3", "1.2.3rc1"), 0)
        # 1.2.3rc1 > 1.2.3beta1 (rc > beta)
        self.assertGreater(compare("1.2.3rc1", "1.2.3beta1"), 0)
        # 1.2.3beta1 > 1.2.3alpha1 (beta > alpha)
        self.assertGreater(compare("1.2.3beta1", "1.2.3alpha1"), 0)

    def test_release_suffix(self):
        # 1.2.3-1 > 1.2.3 (com sufixo de release é maior)
        self.assertGreater(compare("1.2.3-1", "1.2.3"), 0)
        # 1.2.3-2 > 1.2.3-1
        self.assertGreater(compare("1.2.3-2", "1.2.3-1"), 0)

    def test_version_object_comparison(self):
        v1 = parse_version("1.0")
        v2 = parse_version("2.0")
        self.assertTrue(v1 < v2)
        self.assertTrue(v2 > v1)
        self.assertTrue(v1 != v2)

    def test_version_equality(self):
        v1 = parse_version("1.0.0")
        v2 = parse_version("1.0")
        self.assertEqual(v1, v2)
        self.assertEqual(hash(v1), hash(v2))


class TestConstraintParsing(unittest.TestCase):
    """Testa o parsing de constraints (ex: openssl>=3.0)."""

    def test_simple_name(self):
        c = parse_constraint("zlib")
        self.assertEqual(c.name, "zlib")
        self.assertIsNone(c.op)
        self.assertIsNone(c.version)

    def test_ge_operator(self):
        c = parse_constraint("openssl>=3.0")
        self.assertEqual(c.name, "openssl")
        self.assertEqual(c.op, ">=")
        self.assertEqual(c.version, "3.0")

    def test_le_operator(self):
        c = parse_constraint("python<3.15")
        self.assertEqual(c.name, "python")
        self.assertEqual(c.op, "<")
        self.assertEqual(c.version, "3.15")

    def test_eq_operator(self):
        c = parse_constraint("curl=8.9.1")
        self.assertEqual(c.name, "curl")
        self.assertEqual(c.op, "=")
        self.assertEqual(c.version, "8.9.1")

    def test_double_eq_alias(self):
        c = parse_constraint("curl==8.9.1")
        # == é normalizado para =
        self.assertEqual(c.op, "=")

    def test_ne_operator(self):
        c = parse_constraint("bash!=5.0")
        self.assertEqual(c.name, "bash")
        self.assertEqual(c.op, "!=")

    def test_with_spaces(self):
        c = parse_constraint("glibc >= 2.40")
        self.assertEqual(c.name, "glibc")
        self.assertEqual(c.op, ">=")
        self.assertEqual(c.version, "2.40")

    def test_dep_name_helper(self):
        self.assertEqual(dep_name("openssl>=3.0"), "openssl")
        self.assertEqual(dep_name("python<3.15"), "python")
        self.assertEqual(dep_name("zlib"), "zlib")

    def test_invalid_constraint_raises(self):
        with self.assertRaises(VersionError):
            parse_constraint("")
        with self.assertRaises(VersionError):
            parse_constraint("openssl>=")  # operador sem versão


class TestSatisfies(unittest.TestCase):
    """Testa a função satisfies()."""

    def test_ge_satisfied(self):
        self.assertTrue(satisfies("openssl", "3.5", "openssl>=3.0"))
        self.assertTrue(satisfies("openssl", "3.0", "openssl>=3.0"))

    def test_ge_not_satisfied(self):
        self.assertFalse(satisfies("openssl", "2.9", "openssl>=3.0"))

    def test_lt_satisfied(self):
        self.assertTrue(satisfies("python", "3.14", "python<3.15"))

    def test_lt_not_satisfied(self):
        self.assertFalse(satisfies("python", "3.15", "python<3.15"))

    def test_eq_satisfied(self):
        self.assertTrue(satisfies("curl", "8.9.1", "curl=8.9.1"))

    def test_eq_not_satisfied(self):
        self.assertFalse(satisfies("curl", "8.9.2", "curl=8.9.1"))

    def test_ne_satisfied(self):
        self.assertTrue(satisfies("bash", "5.1", "bash!=5.0"))

    def test_ne_not_satisfied(self):
        self.assertFalse(satisfies("bash", "5.0", "bash!=5.0"))

    def test_no_constraint(self):
        # Sem operador/versão, qualquer versão satisfaz
        self.assertTrue(satisfies("zlib", "1.0", "zlib"))
        self.assertTrue(satisfies("zlib", "99.0", "zlib"))

    def test_name_mismatch(self):
        # Nome diferente → False independente da versão
        self.assertFalse(satisfies("openssl", "3.5", "python>=3.0"))

    def test_constraint_object(self):
        c = parse_constraint("openssl>=3.0")
        self.assertTrue(satisfies("openssl", "3.5", c))
        self.assertFalse(satisfies("openssl", "2.9", c))


if __name__ == "__main__":
    unittest.main()
