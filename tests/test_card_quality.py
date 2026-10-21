from html.parser import HTMLParser
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location('quality_anki_cards', SCRIPTS / 'anki_cards.py')
cards = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = cards
SPEC.loader.exec_module(cards)
import local_dictionary


class ExpandedText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.text = []

    def handle_starttag(self, tag, attrs):
        if tag == 'details':
            self.depth += 1

    def handle_endtag(self, tag):
        if tag == 'details':
            self.depth -= 1

    def handle_data(self, data):
        if not self.depth:
            self.text.append(data)


class CardQualityTests(unittest.TestCase):
    def run_text(self, folder, text, lookups, dry=True, extra=()):
        root = Path(folder)
        source = root / 'source.txt'
        if not source.exists():
            source.write_text(text, encoding='utf-8')
        lookup_path = root / 'lookups.json'
        lookup_path.write_text(json.dumps(lookups, ensure_ascii=False), encoding='utf-8')
        argv = ['--text', str(source), '--lookups', str(lookup_path), '--target-language', 'en-US', '--output', str(root / 'output')]
        if dry:
            argv.append('--dry-run')
        argv.extend(extra)
        with patch.object(cards, 'dictionary_entry', return_value=None):
            return cards.build(cards.parser().parse_args(argv))

    def test_supplied_sentence_must_exist_in_source(self):
        with tempfile.TemporaryDirectory() as folder:
            manifest = self.run_text(folder, 'We sat by the bank.', [{'term': 'bank', 'sentence': 'The bank approved a loan.', 'definition': '银行'}])
            self.assertFalse(manifest[0]['matched'])
            self.assertFalse(manifest[0]['ready_for_import'])

    def test_missing_term_is_not_a_successful_text_match(self):
        with tempfile.TemporaryDirectory() as folder:
            manifest = self.run_text(folder, 'We walked home.', [{'term': 'bank', 'definition': '河岸'}])
            self.assertFalse(manifest[0]['matched'])

    def test_rolling_captions_do_not_replace_a_missing_supplied_sentence(self):
        cues = [cards.Cue(1, 0, 3, 'The bank is closed.')]
        lookup = cards.Lookup('bank', sentence='The bank approved a loan.', definition='银行')
        self.assertIsNone(cards.rolling_sentence_context(lookup, cues))

    def test_exact_caption_sentence_retains_its_timed_window(self):
        cues = [cards.Cue(1, 2, 4, 'We sat'), cards.Cue(2, 4, 7, 'We sat by the bank.')]
        lookup = cards.Lookup('bank', sentence='We sat by the bank.', definition='河岸')
        context = cards.rolling_sentence_context(lookup, cues)
        self.assertIsNotNone(context)
        self.assertEqual(context.text, lookup.sentence)
        self.assertEqual((context.start, context.end), (2, 7))

    def test_selects_one_sentence_and_keeps_instance_keys(self):
        with tempfile.TemporaryDirectory() as folder:
            text = 'We walked home. We sat by the bank.'
            lookups = [{'term': 'bank', 'definition': '河岸'}]
            first = self.run_text(folder, text, lookups)
            second = self.run_text(folder, text, lookups)
            self.assertEqual(first[0]['sentence'], 'We sat by the bank.')
            self.assertTrue(first[0]['ready_for_import'])
            self.assertEqual(first[0]['instance_key'], second[0]['instance_key'])

    def test_dictionary_only_draft_does_not_contact_anki(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(cards, 'AnkiConnect', side_effect=AssertionError('Draft must not contact Anki')):
            result = self.run_text(folder, 'We sat by the bank.', [{'term': 'bank'}], dry=False)
            self.assertTrue(result[0]['matched'])
            self.assertFalse(result[0]['ready_for_import'])
            self.assertIn('contextual_meaning_missing', result[0]['quality_issues'])

    def test_main_answer_has_one_contextual_sense_and_preserves_dictionary_reference(self):
        lookup = cards.Lookup('bank', sentence='We sat by the bank.', definition='河岸')
        dictionary = SimpleNamespace(definition='A financial institution.', translation='银行；河岸', phonetic='baenk', pos='noun')
        rendered = cards.dictionary_html(lookup, dictionary)
        visible = ExpandedText()
        visible.feed(rendered)
        self.assertIn('河岸', ''.join(visible.text))
        self.assertNotIn('financial institution', ''.join(visible.text))
        self.assertIn('financial institution', rendered)
        self.assertEqual(cards.dictionary_definition(lookup, dictionary), '河岸')

    def test_no_arbitrary_prefix_matching(self):
        self.assertFalse(cards.contains_term('She is an internationalist.', 'internationalization', 'en-US'))
        self.assertFalse(cards.contains_term('He is running.', 'run', 'en-US'))
        self.assertTrue(cards.contains_term('A cost effective solution.', 'cost-effective', 'en-US'))
        self.assertTrue(cards.contains_term('今日は銀行へ行く。', '銀行', 'ja-JP'))

    def test_dictionary_line_breaks_render_readably_without_changing_data(self):
        dictionary = SimpleNamespace(translation='银行\\n河岸', definition='one sense\\nanother sense', phonetic='', pos='')
        rendered = cards.dictionary_html(cards.Lookup('bank', definition='河岸'), dictionary)
        self.assertIn('银行<br>河岸', rendered)
        self.assertNotIn('\\n', rendered)
        self.assertEqual(dictionary.translation, '银行\\n河岸')

    def test_explicit_surface_handles_inflection_and_keeps_canonical_term(self):
        cases = [('figure out', 'figured it out', 'She figured it out after reading the log.', 'en-US'),
                 ('取り戻す', '取り戻している', '少しずつ自信を取り戻している。', 'ja-JP'),
                 ('manger', 'mangé', 'Elle a mangé une pomme.', 'fr-FR')]
        for term, surface, sentence, language in cases:
            with self.subTest(term=term), tempfile.TemporaryDirectory() as folder:
                result = self.run_text(folder, sentence, [{'term': term, 'surface': surface, 'sentence': sentence, 'definition': 'contextual sense'}], extra=['--target-language', language])[0]
                self.assertTrue(result['ready_for_import'])
                self.assertEqual(result['term'], term)
                self.assertEqual(result['sentence'], sentence)
                self.assertIn('<b>' + surface + '</b>', cards.sentence_field(sentence, result['surface'], language))

    def test_surface_must_still_occur_in_the_real_sentence(self):
        with tempfile.TemporaryDirectory() as folder:
            result = self.run_text(folder, 'She read the log.', [{'term': 'figure out', 'surface': 'figured it out', 'sentence': 'She read the log.', 'definition': '弄明白'}])[0]
            self.assertFalse(result['matched'])

    def test_file_touch_and_move_do_not_create_a_new_instance(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = self.run_text(root, 'We sat by the bank.', [{'term': 'bank', 'definition': '河岸'}])[0]
            source = root / 'source.txt'
            stat = source.stat()
            os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
            touched = self.run_text(root, '', [{'term': 'bank', 'definition': '河岸'}])[0]
            moved = root / 'moved'
            moved.mkdir()
            copied = self.run_text(moved, 'We sat by the bank.', [{'term': 'bank', 'definition': '河岸'}])[0]
            self.assertEqual(first['instance_key'], touched['instance_key'])
            self.assertEqual(first['instance_key'], copied['instance_key'])

    def test_logical_source_identity_survives_local_content_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            lookups = [{'term': 'bank', 'sentence': 'We sat by the bank.', 'definition': '河岸'}]
            first = self.run_text(root, 'We sat by the bank.', lookups, extra=['--source', 'synthetic://article-1'])[0]
            (root / 'source.txt').write_text('Introduction. We sat by the bank.', encoding='utf-8')
            second = self.run_text(root, '', lookups, extra=['--source', 'synthetic://article-1'])[0]
            other = self.run_text(root, '', lookups, extra=['--source', 'synthetic://article-2'])[0]
            self.assertEqual(first['instance_key'], second['instance_key'])
            self.assertNotEqual(first['instance_key'], other['instance_key'])

    def test_nonexistent_media_is_never_reported_available(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, 'Media file does not exist'):
                self.run_text(folder, 'We sat by the bank.', [{'term': 'bank', 'definition': '河岸'}], extra=['--video', str(Path(folder) / 'missing.mp4')])

    def test_requested_media_keeps_text_candidate_a_draft(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(cards, 'AnkiConnect', side_effect=AssertionError('Missing requested media must not import')):
            result = self.run_text(folder, 'We sat by the bank.', [{'term': 'bank', 'definition': '河岸'}], dry=False, extra=['--require-media', 'context_audio', '--require-media', 'visual'])[0]
            self.assertTrue(result['matched'])
            self.assertFalse(result['ready_for_import'])
            self.assertIn('required_media_missing:visual', result['quality_issues'])

    def test_provided_term_audio_satisfies_text_card_requirement(self):
        with tempfile.TemporaryDirectory() as folder:
            audio = Path(folder) / 'provided.wav'
            audio.write_bytes(b'RIFF-test-fixture')
            result = self.run_text(folder, 'We sat by the bank.', [{'term': 'bank', 'definition': '河岸', 'word_audio': str(audio)}], extra=['--require-media', 'term_audio'])[0]
            self.assertTrue(result['ready_for_import'])
            self.assertEqual(result['term_audio'], str(audio))

    def test_unavailable_tts_does_not_reuse_a_stale_audio_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'video.mp4').write_bytes(b'synthetic-media-fixture')
            (root / 'source.srt').write_text('1\n00:00:02,000 --> 00:00:05,000\nWe sat by the bank.\n', encoding='utf-8')
            (root / 'lookups.json').write_text(json.dumps([{'term': 'bank', 'definition': '河岸'}]), encoding='utf-8')
            (root / 'output').mkdir()
            (root / 'output/001_bank-term.mp3').write_bytes(b'stale-other-language-audio')
            argv = ['--video', str(root / 'video.mp4'), '--subtitles', str(root / 'source.srt'), '--lookups', str(root / 'lookups.json'), '--target-language', 'en-US', '--output', str(root / 'output'), '--prepare-only', '--media-processor', sys.executable]
            def extract(executable, source, start, end, output, kind):
                output.write_bytes(b'extracted-test-fixture')
            with patch.object(cards, 'dictionary_entry', return_value=None), patch.object(cards, 'default_tts', return_value=None), patch.object(cards, 'run_media', side_effect=extract):
                result = cards.build(cards.parser().parse_args(argv))[0]
            self.assertNotIn('term_audio', result)
            self.assertEqual(result['resource_status']['term_audio'], 'missing')

    def test_legacy_note_is_reused_after_file_metadata_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            lookups = [{'term': 'bank', 'sentence': 'We sat by the bank.', 'definition': '河岸'}]
            first = self.run_text(folder, 'We sat by the bank.', lookups)[0]
            old_key = first['legacy_instance_key']
            source = Path(folder) / 'source.txt'
            stat = source.stat()
            os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
            calls = []

            class LegacyAnki:
                def __init__(self, url): pass
                def ensure_model(self, model, profile): pass
                def call(self, action, **params):
                    calls.append(action)
                    if action == 'findNotes': return [42]
                    if action == 'notesInfo':
                        return [{'noteId': 42, 'modelName': 'Anki Card Maker Context', 'fields': {key: {'value': value} for key, value in {'InstanceKey': old_key, 'Term': 'bank', 'Source': str(source), 'Context': 'We sat by the <b>bank</b>.'}.items()}}]
                    if action == 'addNote': raise AssertionError('Legacy note must be reused')

            with patch.object(cards, 'AnkiConnect', LegacyAnki):
                result = self.run_text(folder, '', lookups, dry=False)[0]
            self.assertEqual(result['note_id'], 42)
            self.assertEqual(result['instance_key'], old_key)
            self.assertIn('updateNoteFields', calls)

    def test_cjk_sentences_are_separated_without_whitespace(self):
        spans = cards.sentence_spans([cards.Cue(1, 0, 0, '今日は晴れです。銀行へ行きます。')], 'ja-JP')
        context = cards.find_sentence(cards.Lookup('銀行'), spans, 'ja-JP')
        self.assertEqual(context.text, '銀行へ行きます。')

    def test_unsupported_dictionary_language_does_not_use_english_homograph(self):
        with patch.object(local_dictionary, 'lookup', side_effect=AssertionError('No unrelated provider may be queried')):
            self.assertIsNone(local_dictionary.lookup_provider('manger', 'fr-FR'))
            self.assertIsNone(local_dictionary.lookup_provider('Gift', 'de-DE'))
        self.assertEqual(local_dictionary.provider_for_language('en-GB').name, 'ecdict.sqlite')
        self.assertEqual(local_dictionary.provider_for_language('ja-JP').name, 'jmdict.sqlite')

    def test_missing_dictionary_does_not_hide_verified_contextual_meaning(self):
        with tempfile.TemporaryDirectory() as folder:
            result = self.run_text(folder, 'Elle a mangé une pomme.', [{'term': 'manger', 'surface': 'mangé', 'definition': '吃'}], extra=['--target-language', 'fr-FR'])[0]
            self.assertTrue(result['ready_for_import'])
            self.assertEqual(result['resource_status']['dictionary'], 'missing')
            self.assertEqual(result['resource_status']['contextual_meaning'], 'provided')

    def test_existing_instance_updates_fields_without_recreating_note(self):
        with tempfile.TemporaryDirectory() as folder:
            lookups = [{'term': 'bank', 'sentence': 'We sat by the bank.', 'definition': '河岸'}]
            first = self.run_text(folder, 'We sat by the bank.', lookups)
            key = first[0]['instance_key']
            calls = []

            class ExistingAnki:
                def __init__(self, url):
                    pass

                def ensure_model(self, model, profile):
                    pass

                def call(self, action, **params):
                    calls.append((action, params))
                    if action == 'findNotes':
                        return [42]
                    if action == 'notesInfo':
                        return [{'noteId': 42, 'modelName': 'Anki Card Maker Context', 'fields': {'InstanceKey': {'value': key}}}]
                    if action == 'addNote':
                        raise AssertionError('Existing instance must not be recreated')
                    return None

            with patch.object(cards, 'AnkiConnect', ExistingAnki):
                result = self.run_text(folder, 'We sat by the bank.', lookups, dry=False)
            updates = [params for action, params in calls if action == 'updateNoteFields']
            self.assertEqual(result[0]['note_id'], 42)
            self.assertEqual(updates[0]['note']['id'], 42)
            self.assertEqual(updates[0]['note']['fields']['InstanceKey'], key)
            self.assertEqual({action for action, _ in calls}, {'version', 'createDeck', 'findNotes', 'notesInfo', 'updateNoteFields'})

    def test_legacy_upgrade_preserves_key_on_later_reruns(self):
        with tempfile.TemporaryDirectory() as folder:
            lookups = [{'term': 'bank', 'sentence': 'We sat by the bank.', 'definition': '河岸'}]
            first = self.run_text(folder, 'We sat by the bank.', lookups)[0]
            source = Path(folder) / 'source.txt'
            old_key = first['legacy_instance_key']
            note = {'noteId': 42, 'modelName': 'Anki Card Maker Context', 'fields': {key: {'value': value} for key, value in {'InstanceKey': old_key, 'Term': 'bank', 'Source': str(source), 'Context': 'We sat by the <b>bank</b>.'}.items()}}

            class MutableAnki:
                def __init__(self, url): pass
                def ensure_model(self, model, profile): pass
                def call(self, action, **params):
                    if action == 'findNotes': return [42]
                    if action == 'notesInfo': return [note]
                    if action == 'updateNoteFields':
                        note['fields'] = {key: {'value': value} for key, value in params['note']['fields'].items()}
                    if action == 'addNote': raise AssertionError('Upgrade must retain old identity on every rerun')

            for _ in range(2):
                stat = source.stat()
                os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
                with patch.object(cards, 'AnkiConnect', MutableAnki):
                    result = self.run_text(folder, '', lookups, dry=False)[0]
                self.assertEqual((result['note_id'], result['instance_key']), (42, old_key))

    def test_same_sentence_at_a_new_timestamp_is_a_distinct_modern_instance(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            lookups = root / 'lookups.json'
            lookups.write_text(json.dumps([{'term': 'bank', 'sentence': 'We sat by the bank.', 'definition': '河岸'}]), encoding='utf-8')
            srt = root / 'source.srt'
            srt.write_text('1\n00:00:08,000 --> 00:00:11,000\nWe sat by the bank.\n', encoding='utf-8')
            note = {'noteId': 42, 'modelName': 'Anki Card Maker Context', 'fields': {key: {'value': value} for key, value in {'InstanceKey': 'other-time-key', 'Term': 'bank', 'Source': 'synthetic://repeated', 'Context': 'We sat by the <b>bank</b>.', 'ResourceStatus': json.dumps({'identity_version': 2, 'canonical_instance_key': 'other-time-key'})}.items()}}
            calls = []

            class RepeatAnki:
                def __init__(self, url): pass
                def ensure_model(self, model, profile): pass
                def call(self, action, **params):
                    calls.append(action)
                    if action == 'findNotes': return [42]
                    if action == 'notesInfo': return [note]
                    if action == 'addNote': return 43

            argv = ['--subtitles', str(srt), '--lookups', str(lookups), '--source', 'synthetic://repeated', '--target-language', 'en-US', '--output', str(root / 'out')]
            with patch.object(cards, 'AnkiConnect', RepeatAnki), patch.object(cards, 'dictionary_entry', return_value=None):
                result = cards.build(cards.parser().parse_args(argv))[0]
            self.assertEqual(result['note_id'], 43)
            self.assertNotIn('updateNoteFields', calls)


if __name__ == '__main__':
    unittest.main()
