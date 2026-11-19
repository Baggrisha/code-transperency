"""Regression checks for contract and item identifier normalization."""
import unittest

from kontrol.normalize import normalize_identifier


class NormalizeIdentifierTests(unittest.TestCase):
    def test_numero_sign_is_removed_before_unicode_compatibility_normalization(self):
        for value, expected in (
            ("№ К-1", "К-1"),
            ("№К–01", "К-01"),
            ("NOК-1", "NOК-1"),
            ("Ｎｏ－1", "NO-1"),
            ("А-1", "А-1"),
            ("A-1", "A-1"),
            (None, ""),
        ):
            with self.subTest(value=value):
                self.assertEqual(normalize_identifier(value), expected)
                self.assertEqual(normalize_identifier(expected), expected)


if __name__ == "__main__":
    unittest.main()
