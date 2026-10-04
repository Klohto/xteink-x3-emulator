"""Independent fixture/oracle checks; these do not claim stock guest coverage."""
from io import BytesIO
import json
from pathlib import Path
import runpy
import tempfile
import unittest
import uuid
from zipfile import ZipFile, ZIP_STORED
import xml.etree.ElementTree as ET
import zlib

H = runpy.run_path(str(Path(__file__).resolve().parent.parent / 'scripts/test-crossink-end-book.py'))

class EndBookTests(unittest.TestCase):
    def test_original_books_are_valid_distinct_and_have_no_seeded_guest_state(self):
        files = H['fixture_files']()
        self.assertEqual(files, H['fixture_files']())
        self.assertEqual(files['/test.epub'], H['make_test_epub']())
        self.assertTrue(all(not path.startswith('/.crosspoint') for path in files))
        ns = {'p': 'http://www.idpf.org/2007/opf', 'd': 'http://purl.org/dc/elements/1.1/'}
        titles, identifiers, first_pages = [], [], []
        for marker in ('Cedar', 'River', 'Mountain', 'Desert'):
            raw = H['sibling_book'](marker)
            with ZipFile(BytesIO(raw)) as archive:
                self.assertIsNone(archive.testzip())
                self.assertEqual(archive.infolist()[0].filename, 'mimetype')
                self.assertEqual(archive.infolist()[0].compress_type, ZIP_STORED)
                self.assertEqual(archive.read('mimetype'), b'application/epub+zip')
                container = ET.fromstring(archive.read('META-INF/container.xml'))
                rootfile = next(container.iter('{urn:oasis:names:tc:opendocument:xmlns:container}rootfile'))
                package = ET.fromstring(archive.read(rootfile.attrib['full-path']))
                manifest = {item.attrib['id']: item.attrib['href'] for item in package.findall('p:manifest/p:item', ns)}
                spine = package.findall('p:spine/p:itemref', ns)
                self.assertEqual(len(spine), 6)
                for item in spine:
                    body = ET.fromstring(archive.read('OEBPS/' + manifest[item.attrib['idref']]))
                    paragraphs = body.findall('{http://www.w3.org/1999/xhtml}body/{http://www.w3.org/1999/xhtml}p')
                    self.assertEqual(len(paragraphs), 24)
                    self.assertTrue(all(marker in p.text for p in paragraphs))
                titles.append(package.find('p:metadata/d:title', ns).text)
                identifier = package.find('p:metadata/d:identifier', ns).text
                self.assertEqual(str(uuid.UUID(identifier.removeprefix('urn:uuid:'))), identifier.removeprefix('urn:uuid:'))
                identifiers.append(identifier)
                first_pages.append(archive.read('OEBPS/chapter1.xhtml'))
                for name in ('OEBPS/nav.xhtml', 'OEBPS/toc.ncx', 'OEBPS/style.css'):
                    self.assertIn(name, archive.namelist())
        self.assertEqual(len(set(titles)), 4)
        self.assertEqual(len(set(identifiers)), 4)
        self.assertEqual(len(set(first_pages)), 4)

    def test_menu_oracle_requires_natural_order_home_and_exclusions(self):
        self.assertTrue(H['menu_matches']('End of Book\nContinue With\ntest2 Cedar\ntest10 River\ntest20 Mountain\nHome\nOpen Up Down'))
        self.assertTrue(H['menu_matches']('TEST 2 CEDAR\nTEST 10 RIVER\nTEST 20 MOUNTAIN\nHOME'))
        self.assertTrue(H['menu_matches']('jest2 Cedar\ntesti0 River\ntest20 Mountain\nHome'))
        for text in ('test10 River test2 Cedar test20 Mountain Home',
                     'test2 Cedar test10 River Home',
                     'test2 Cedar test10 River test20 Mountain',
                     'Home test2 Cedar test10 River test20 Mountain',
                     'test2 Cedar test10 River test20 Mountain test30 Desert Home',
                     'test2 Cedar test10 River test20 Mountain test3 Gallery Home',
                     'test2 Cedar test10 River test20 Mountain test4 Folder Home'):
            self.assertFalse(H['menu_matches'](text), text)

    def test_body_oracle_excludes_footer_but_detects_last_page_changes(self):
        pixels = bytearray([255]) * (792 * 528)
        frame = b'P5\n792 528\n255\n' + pixels
        footer = bytearray(pixels)
        footer[(527 - 20) * 792 + 760] = 0
        self.assertEqual(H['body_delta'](frame, b'P5\n792 528\n255\n' + footer), 0)
        footer[(527 - 20) * 792 + 400] = 0
        self.assertEqual(H['body_delta'](frame, b'P5\n792 528\n255\n' + footer), 1)

    def test_source_guards_reject_changed_captured_and_pinned_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relative = Path('src/a.cpp')
            (root / relative).parent.mkdir()
            (root / relative).write_bytes(b'original')
            self.assertEqual(H['pinned_source'](root, {str(relative): H['sha'](b'original')})[0]['bytes'], 8)
            H['assert_sources'](root, {relative: b'original'})
            (root / relative).write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'source pin mismatch'):
                H['pinned_source'](root, {str(relative): H['sha'](b'original')})
            with self.assertRaisesRegex(ValueError, 'execution source changed'):
                H['assert_sources'](root, {relative: b'original'})

    def test_closed_trace_rejects_truncation_sequence_crc_exit_and_counter_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pixels = bytes([255]) * (792 * 528)
            crc = zlib.crc32(pixels)
            rows = [{'seq': 1, 'event': 'command', 'value': 0}, {'seq': 2, 'event': 'frame-complete', 'value': crc}]
            raw = b''.join((json.dumps(row) + '\n').encode() for row in rows)
            (root / 'panel.jsonl').write_bytes(raw)
            (root / 'panel.pbm').write_bytes(b'P5\n# refresh=1\n792 528\n255\n' + pixels)
            panel = {'trace-events-attempted': 2, 'trace-events-flushed': 2,
                     'trace-bytes-flushed': len(raw), 'trace-fd-size': len(raw),
                     'trace-fd-position': len(raw), 'trace-path-size': len(raw),
                     'trace-file-linked': True, 'trace-fd-inode': 8, 'trace-path-inode': 8,
                     'refresh-count': 1, 'dump-frames-written': 1, 'framebuffer-crc': crc}
            for key in ('output-errors', 'trace-write-errors', 'trace-flush-errors', 'trace-close-errors',
                        'dump-open-errors', 'dump-write-errors', 'dump-flush-errors', 'dump-close-errors', 'protocol-errors'):
                panel[key] = 0
            manifest = {'status': 'stopped', 'exit_code': 0, 'backend': {'sha256': H['BACKEND_SHA']},
                        'final_state': {'panel': panel, 'wifi': {'bad-dma': 0}}}
            def write(value):
                (root / 'run.json').write_text(json.dumps(value))
            write(manifest)
            self.assertTrue(H['closed_trace_proof'](root)['functional_pass'])
            probe = type('Probe', (), {'log_text': lambda self, name: raw.decode()})()
            self.assertEqual(H['SMOKE']['Experiment'].frame_events(probe), [rows[1]])
            for changes in ({'exit_code': 1}, {'status': 'running'}):
                write({**manifest, **changes})
                self.assertFalse(H['closed_trace_proof'](root)['functional_pass'])
            write(manifest)
            for key, value in (('trace-events-attempted', 3), ('framebuffer-crc', crc ^ 1),
                               ('trace-path-inode', 9), ('dump-close-errors', 1)):
                write({**manifest, 'final_state': {'panel': {**panel, key: value}, 'wifi': {'bad-dma': 0}}})
                self.assertFalse(H['closed_trace_proof'](root)['functional_pass'])
            write(manifest)
            (root / 'panel.jsonl').write_bytes(raw.splitlines(keepends=True)[0])
            self.assertFalse(H['closed_trace_proof'](root)['functional_pass'])
            rows[1]['seq'] = 3
            changed = b''.join((json.dumps(row) + '\n').encode() for row in rows)
            self.assertEqual(len(changed), len(raw))
            (root / 'panel.jsonl').write_bytes(changed)
            self.assertFalse(H['closed_trace_proof'](root)['functional_pass'])

if __name__ == '__main__':
    unittest.main()
