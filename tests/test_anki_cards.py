import importlib.util
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("anki_cards", SCRIPTS / "anki_cards.py")
assert SPEC and SPEC.loader
anki_cards = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = anki_cards
SPEC.loader.exec_module(anki_cards)
Cue = anki_cards.Cue
Lookup = anki_cards.Lookup
contains_term = anki_cards.contains_term
find_sentence = anki_cards.find_sentence
instance_key = anki_cards.instance_key
sentence_spans = anki_cards.sentence_spans


class AnkiCardsTests(unittest.TestCase):
    def test_english_sentence_spans_join_cues(self):
        cues = [Cue(1, 0, 1, "That is a"), Cue(2, 1, 3, "real worrier.")]
        spans = sentence_spans(cues, "en-US")
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0].text, "That is a real worrier.")

    def test_cjk_matching_does_not_need_word_boundaries(self):
        self.assertTrue(contains_term("我正在学习中文。", "学习", "zh-CN"))
        cues = [Cue(1, 0, 2, "今日は良い天気ですね。")]
        self.assertIsNotNone(find_sentence(Lookup("天気"), sentence_spans(cues, "ja-JP"), "ja-JP"))

    def test_same_term_different_context_has_different_key(self):
        first = instance_key("en-US", "worrier", "source", 1, "A worrier.")
        second = instance_key("en-US", "worrier", "source", 4, "Another worrier.")
        self.assertNotEqual(first, second)
        self.assertEqual(first, instance_key("en-US", "worrier", "source", 1, "A worrier."))


if __name__ == "__main__":
    unittest.main()
