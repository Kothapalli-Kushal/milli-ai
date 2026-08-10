import unittest


def normalize_username(raw: str) -> str:
    cleaned = " ".join(raw.strip().split())
    if not cleaned:
        raise ValueError("username is empty")
    return cleaned.replace(" ", "_").lower()


class TestNormalizeUsername(unittest.TestCase):
    def test_basic_normalization(self):
        self.assertEqual(normalize_username("John Doe"), "john_doe")

    def test_strips_leading_trailing_whitespace(self):
        self.assertEqual(normalize_username("  Jane Smith  "), "jane_smith")

    def test_collapses_internal_whitespace(self):
        self.assertEqual(normalize_username("John   Q   Public"), "john_q_public")

    def test_already_normalized_lowercase(self):
        self.assertEqual(normalize_username("bob"), "bob")

    def test_mixed_case_single_word(self):
        self.assertEqual(normalize_username("ADMIN"), "admin")

    def test_tabs_and_newlines_treated_as_whitespace(self):
        self.assertEqual(normalize_username("\tAlice\nCooper\t"), "alice_cooper")

    def test_blank_input_raises_value_error(self):
        with self.assertRaises(ValueError):
            normalize_username("")

    def test_whitespace_only_input_raises_value_error(self):
        with self.assertRaises(ValueError):
            normalize_username("   \t\n  ")

    def test_error_message_content(self):
        with self.assertRaisesRegex(ValueError, "username is empty"):
            normalize_username("")


if __name__ == "__main__":
    unittest.main()
