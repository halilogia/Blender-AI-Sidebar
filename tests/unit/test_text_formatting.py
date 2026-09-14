"""Tests for UI-only assistant text normalization."""

import unittest

from ui.text_formatting import clean_assistant_text


class TestAssistantTextFormatting(unittest.TestCase):
    def test_decodes_html_entities_without_changing_normal_text(self):
        self.assertEqual(
            clean_assistant_text("Küp oluştu.&#x20;\nHazır."),
            "Küp oluştu.\nHazır.",
        )

    def test_normalizes_line_endings_and_trailing_spaces(self):
        self.assertEqual(clean_assistant_text("Bir  \r\nİki  \rÜç  "), "Bir\nİki\nÜç")

    def test_empty_text_is_empty(self):
        self.assertEqual(clean_assistant_text(""), "")


if __name__ == "__main__":
    unittest.main()
