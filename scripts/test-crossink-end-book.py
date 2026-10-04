#!/usr/bin/env python3
"""Actual stock-X3 nonempty end-of-book menu and persisted reader proof."""
from __future__ import annotations

import argparse
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys
from zipfile import ZipFile, ZipInfo
import zlib

ROOT = Path(__file__).resolve().parent.parent
DEPENDENCIES = list((ROOT / 'x3emu').glob('*.py')) + list((ROOT / 'boards').glob('*.toml'))
DEPENDENCIES += [ROOT / 'scripts' / name for name in
                 ('smoke-crossink.py', 'test-crossink-functions.py', Path(__file__).name)]
CAPTURED = {p.relative_to(ROOT): p.read_bytes() for p in DEPENDENCIES}
sys.path.insert(0, str(ROOT))
from x3emu.backend import DEFAULT_BACKEND, file_sha256
from x3emu.fixtures import make_png
from x3emu.sdcard import make_test_epub

SHARED = runpy.run_path(str(ROOT / 'scripts/test-crossink-functions.py'))
SMOKE = SHARED['SMOKE']
NAME = 'end-book-options'
BOOK = '/test.epub'
TARGET = '/test10 River.epub'
STATE = '/.crosspoint/state.json'
LABELS = ('test2 Cedar', 'test10 River', 'test20 Mountain')
EXCLUDED = ('test30 Desert', 'test3 Gallery', 'test4 Folder', '0 Earlier')
BACKEND_SHA = '56514829d86aa1bc20007bb76e129e36583897a3216e0fe15d2a3f5942f1acfa'
SOURCE_PINS = {
    'src/util/NextBookFinder.cpp': 'ad9052ca801fdb1cf0636c354e65741e92964ce7286c53cd43e6aff694b310eb',
    'src/activities/reader/EndOfBookOptions.cpp': '6eb7959c7a15397d179ac1312b7b8f9723c0e0faaa7ece3c24d3967ae26138d0',
    'src/activities/reader/EndOfBookOptions.h': '69276dda0e297e160f24102dd6b19e133391e9e514814f5e63fd1182e732526a',
    'lib/FsHelpers/NaturalSort.cpp': '8353316bee1e741c1c71625d4156700dde887614949e8abbf72efc5a63f158a3',
    'src/activities/reader/EpubReaderActivity.cpp': 'c4b13517aed22a2baf2e2eb1b1919e1515ce0f88f1d0945f05980f4228a05399',
    'src/activities/reader/EpubReaderPercentSelectionActivity.cpp': 'e218606ae24efd0ba448844ac91ab14ed04cdbbfb3d9c0f5db229c61764e696c',
    'src/activities/reader/ReaderActivity.cpp': '1d54b96bc99278d0d20818a1876ec5dd7bd4a3b63f3046a198f4efa19820781a',
    'src/activities/reader/ReaderUtils.h': 'd82ee89df6e8041a34d757896f329bd8dc3303eb681b3803602b897d3a5ff26c',
    'src/CrossPointState.cpp': 'ab1316298ec506518fa6278f88f868253ab028dc1b2a9f3efe3933dd686f968c',
    'src/CrossPointSettings.h': 'e4776b7ee73e09aaf0bc59929954c5745aff0e461381153814f7170e68d3501e',
    'src/activities/home/FileBrowserActivity.cpp': 'e4d7d9e885645c4bfe47a71cc50e4f80f3c2dc010660735e0ec496548c1b1dc9',
}
SDK_PINS = {
    'libs/ui/FreeInkUI/include/FreeInkApp.h': '134836e28c47bd383072d2989546ef6d159d746509259abc01edf216610fcb42',
    'libs/ui/FreeInkUI/include/components/lists/list.h': '16795bbdefcfca6408e12a343faede35015d78e865917cbe0158584c616034a9',
    'libs/ui/FreeInkUI/include/FreeInkUIInputManager.h': 'b71ed271b6a634186fb75c23819c74f27c52f177a95832bf2f54a6f9fbe66151',
}

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def assert_sources(root, captured):
    for relative, expected in captured.items():
        if (root / relative).read_bytes() != expected:
            raise ValueError('execution source changed: ' + str(relative))

def pinned_source(root, pins):
    rows = []
    for path, expected in pins.items():
        raw = (root / path).read_bytes()
        if sha(raw) != expected:
            raise ValueError('source pin mismatch: ' + path)
        rows.append({'path': path, 'sha256': expected, 'bytes': len(raw)})
    return rows

def sibling_book(marker):
    """Original deterministic EPUB3, valid navigation/spines, unique visible prose."""
    title = marker + ' Journey'
    output = BytesIO()
    with ZipFile(BytesIO(make_test_epub())) as source, ZipFile(output, 'w') as target:
        for item in source.infolist():
            raw = source.read(item.filename)
            raw = raw.replace(b'CrossInk Emulator Test Book', title.encode())
            identifier = '8062f89b-71cb-488c-a43f-' + sha(marker.encode())[:12]
            raw = raw.replace(b'8062f89b-71cb-488c-a43f-ec615a6c13a3', identifier.encode())
            match = re.fullmatch(r'OEBPS/chapter([1-6])\.xhtml', item.filename)
            if match:
                chapter = int(match[1])
                paragraphs = ''.join(
                    f'<p>{marker} journey paragraph {number} in chapter {chapter}. '
                    f'The {marker.lower()} story follows a winding path beside the trees. '
                    'Each reader notices a different stone, a quiet garden and a distant train. '
                    'The window opens onto the river while the clock marks another afternoon.</p>'
                    for number in range(1, 25))
                raw = (f'<?xml version="1.0" encoding="UTF-8"?>'
                       f'<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en">'
                       f'<head><title>{title}</title><link rel="stylesheet" type="text/css" href="style.css"/></head>'
                       f'<body><h1>{marker} chapter {chapter}</h1>{paragraphs}</body></html>').encode()
            info = ZipInfo(item.filename, item.date_time)
            info.compress_type = item.compress_type
            target.writestr(info, raw)
    return output.getvalue()

def fixture_files():
    return {BOOK: make_test_epub(), '/0 Earlier.epub': sibling_book('Earlier'),
            '/test2 Cedar.epub': sibling_book('Cedar'), TARGET: sibling_book('River'),
            '/test20 Mountain.epub': sibling_book('Mountain'), '/test30 Desert.epub': sibling_book('Desert'),
            '/test3 Gallery.png': make_png(40, 40),
            '/test4 Folder.epub/child.txt': b'Original directory control, never an end-menu book.\n'}

def menu_matches(text):
    compact = re.sub(r'[^a-z0-9]', '', text.lower())
    # Source displayName uses the filename, and these unique word suffixes
    # identify exactly one input path each. Numeric prefix OCR is unreliable
    # on the stock dithered selection; the actual Open effect independently
    # verifies /test10 River.epub after one Right from the first row.
    names = [item.split()[-1].lower() for item in LABELS]
    positions = [compact.find(item) for item in names]
    return (all(value >= 0 for value in positions) and positions == sorted(positions)
            and compact.find('home', positions[-1] + len(names[-1])) >= 0
            and not any(item.split()[-1].lower() in compact for item in EXCLUDED))

def body_delta(first, second):
    aw, ah, a = SMOKE['read_pgm'](first)
    bw, bh, b = SMOKE['read_pgm'](second)
    if (aw, ah) != (792, 528) or (bw, bh) != (aw, ah):
        raise ValueError('unexpected native portrait panel geometry')
    # Original portrait reader body only; clock/battery/progress footer excluded.
    return sum(a[(527 - x) * aw + y] != b[(527 - x) * bw + y]
               for y in range(60, 738) for x in range(10, 518))

def read_json(replay, path):
    return json.loads(replay.read_file(path))

def progress(replay, path):
    return SMOKE['decode_progress'](replay.read_file(SHARED['cache_path'](path) + '/progress.bin'))

def persist_file(replay, label, path):
    raw = replay.read_file(path)
    destination = replay.experiment.output / (label + '.bin')
    destination.write_bytes(raw)
    replay.receipt.setdefault('written_media', {})[label] = {
        'guest_path': path, 'path': destination.name, 'sha256': sha(raw), 'bytes': len(raw),
        'boot_index': replay.boot_index}
    replay.save()
    return raw

def ocr(replay, frame, label):
    from PIL import Image
    image = Image.open(replay.experiment.output / replay.receipt['frames'][frame]['path'])
    stream = BytesIO()
    image.rotate(270, expand=True).save(stream, format='PNG')
    executable = shutil.which('tesseract')
    if not executable:
        raise ValueError('Tesseract is required to verify actual end-menu labels')
    result = subprocess.run([executable, 'stdin', 'stdout', '--psm', '6'], input=stream.getvalue(),
                            capture_output=True, timeout=45, check=True)
    text = result.stdout.decode('utf-8', errors='replace')
    name = label + '-ocr.txt'
    (replay.experiment.output / name).write_text(text)
    replay.receipt.setdefault('ocr_observations', {})[label] = {
        'frame': frame, 'path': name, 'text_sha256': sha(text.encode()), 'text': text,
        'rotation': 270, 'executable_sha256': file_sha256(executable)}
    replay.save()
    return text

def wait_book(replay, path, label):
    cache = SHARED['cache_path'](path)
    replay.experiment.wait('guest opens ' + path, lambda: read_json(replay, STATE).get('openEpubPath') == path)
    replay.experiment.wait('guest completes real section for ' + path, lambda:
                           SHARED['SMOKE']['decode_section_cache'](replay.read_file(cache + '/sections/0.bin'))['page_count'] >= 2)
    replay.capture(label, 0)
    replay.check(label + '_actual_path', read_json(replay, STATE).get('openEpubPath') == path)
    return plain_page(replay, label)

def plain_page(replay, label):
    """Require the actual post-indexing/modal-free paint in original prose fixtures."""
    pending = []
    while True:
        width, height, pixels = SMOKE['read_pgm'](replay.experiment.frames[label])
        if (width, height) != (792, 528):
            raise ValueError('unexpected native X3 dimensions')
        maximum = 0
        for y in range(100, 730):
            length = 0
            for x in range(28, 500):
                length = length + 1 if pixels[(527 - x) * width + y] < 192 else 0
                maximum = max(maximum, length)
        if maximum < 100:
            replay.receipt.setdefault('plain_reader_observations', {})[label] = {
                'maximum_body_ink_run': maximum, 'prior_modal_frames': pending}
            replay.save()
            return label
        pending.append({'frame': label, 'maximum_body_ink_run': maximum})
        after = replay.receipt['frames'][label]['frame_count']
        label = label + '-after-modal'
        replay.capture(label, after)

def menu(replay, label):
    frame = replay.tap('down', label, 'Next page from the actual completed last page enters the suggestion menu')
    text = ocr(replay, frame, label)
    replay.check(label + '_ordered_three_books_home_and_filters', menu_matches(text), {
        'text': text, 'ocr_scope': 'Unique original filename suffix words, ordered Cedar/River/Mountain, then Home. Full numeric path additionally checked through actual selected-book Open.',
        'expected_paths': ['/test2 Cedar.epub', TARGET, '/test20 Mountain.epub'],
        'excluded_word_suffixes': [item.split()[-1] for item in EXCLUDED]})
    return frame

def workflow(replay):
    replay.receipt.update({'configuration_input_scope': 'Virgin settings/state/progress; original SD books only. All positions arise from actual UI.',
                           'source_proof': SOURCE_PROOF, 'sdk_source_proof': SDK_PROOF,
                           'host_execution_source_manifest_sha256': EXECUTION_MANIFEST_SHA,
                           'all_functions_verified': False, 'actual_crypto_verified': False})
    replay.capture('empty-home', 0)
    browser = replay.tap('confirm', 'initial-browser', 'Virgin Home selected Browse Files opens the real card')
    # Directories first, then 0 Earlier, then the original test.epub.
    replay.tap('down', 'browser-earlier-control', 'Pass the original directory control')
    replay.tap('down', 'browser-original', 'Pass the original earlier book to /test.epub')
    replay.tap('confirm', 'original-opening', 'Open the untouched original book through its real browser row')
    initial = wait_book(replay, BOOK, 'original-ready')
    replay.check('original_current_book_exact', replay.read_file(BOOK) == make_test_epub())
    replay.reader_menu()
    replay.tap('down', 'chapter-row', 'Main reader menu first row: Select Chapter')
    replay.tap('down', 'percent-row', 'Main reader menu second row: Go to Percent')
    replay.tap('confirm', 'percent-picker', 'Open the actual percent picker')
    for index in range(10):
        replay.tap('down', 'percent-increase-' + str(index), 'Increase the UI percentage ten points')
    replay.tap('confirm', 'original-last-opening', 'UI 100 percent selects the last visible page in the final spine')
    cache = SHARED['cache_path'](BOOK)
    replay.experiment.wait('actual last chapter cache', lambda:
                           SMOKE['decode_section_cache'](replay.read_file(cache + '/sections/5.bin'))['page_count'] >= 2)
    replay.tap('down', 'completion-prompt', 'Forward reaches the actual completion confirmation')
    replay.tap('down', 'completion-confirm', 'Select Confirm after default Cancel')
    last = plain_page(replay, replay.tap('confirm', 'completion-accepted', 'Accept completion, retaining the actual last reading page'))
    replay.check('actual_completion_saved', SHARED['decode_book_stats'](replay.read_file(cache + '/stats_v5.bin'))['completed'])
    first_menu = menu(replay, 'first-suggestions')
    returned = replay.tap('back', 'short-back-last-page', 'Short Back from the menu returns to the final visible page')
    replay.check('short_back_restores_last_page_body_exactly', body_delta(replay.experiment.frames[last], replay.experiment.frames[returned]) == 0)
    menu(replay, 'suggestions-before-long-back')
    long_browser = replay.tap('back', 'long-back-browser', 'Hold Back past the stock one-second browser shortcut', hold_ms=1200)
    text = ocr(replay, long_browser, 'long-back-browser')
    replay.check('long_back_reaches_actual_browser', all(word in text.lower() for word in ('gallery', 'cedar', 'river')))
    original_progress = persist_file(replay, 'original-last-progress-after-long-back', cache + '/progress.bin')
    decoded = SMOKE['decode_progress'](original_progress)
    count = SMOKE['decode_section_cache'](replay.read_file(cache + '/sections/5.bin'))['page_count']
    replay.check('long_back_persists_real_final_page', decoded['spine_index'] == 5 and decoded['page_number'] == count - 1, decoded)
    replay.tap('confirm', 'browser-reopens-original', 'Browser highlights the current book; Confirm reopens its real saved position')
    reopened = wait_book(replay, BOOK, 'original-reopened-after-browser')
    replay.check('browser_reopen_restores_last_page_body', body_delta(replay.experiment.frames[last], replay.experiment.frames[reopened]) == 0)
    menu(replay, 'suggestions-before-open')
    replay.tap('right', 'second-suggestion', 'Physical front Right selects the naturally second book, test10 River')
    replay.tap('confirm', 'open-second-suggestion', 'Confirm Open launches the actual selected sibling EPUB')
    target_first = wait_book(replay, TARGET, 'river-first-page')
    words = SHARED['decode_text_page_words'](replay.read_file(SHARED['cache_path'](TARGET) + '/sections/0.bin'))
    replay.check('selected_sibling_has_original_river_words', 'River' in words, words[:24])
    replay.check('selected_sibling_raster_differs_from_original', body_delta(replay.experiment.frames[initial], replay.experiment.frames[target_first]) > 1000)
    target_next = plain_page(replay, replay.tap('down', 'river-second-page', 'Read forward in the actually opened sibling'))
    replay.check('selected_sibling_page_turn_changes_body', body_delta(replay.experiment.frames[target_first], replay.experiment.frames[target_next]) > 1000)
    replay.tap('back', 'river-home', 'Exit the sibling reader to flush its actual page-one progress')
    target_cache = SHARED['cache_path'](TARGET)
    target_progress = persist_file(replay, 'river-progress-before-cold', target_cache + '/progress.bin')
    value = SMOKE['decode_progress'](target_progress)
    replay.check('selected_sibling_saved_page_one', value['spine_index'] == 0 and value['page_number'] == 1, value)
    replay.restart()
    replay.check('selected_sibling_progress_survives_new_cpu', replay.read_file(target_cache + '/progress.bin') == target_progress)
    replay.tap('confirm', 'river-cold-continue', 'Fresh CPU Home Continue reopens the actual selected sibling')
    target_cold = wait_book(replay, TARGET, 'river-cold-ready')
    replay.check('selected_sibling_cold_body_exact', body_delta(replay.experiment.frames[target_next], replay.experiment.frames[target_cold]) == 0)
    replay.tap('back', 'river-cold-browser', 'Held Back returns to the root browser with the current sibling selected', hold_ms=1200)
    for index in range(3):
        replay.tap('up', 'return-original-row-' + str(index), 'Pass Gallery and Cedar to the original test.epub')
    replay.tap('confirm', 'original-from-sibling-browser', 'Open the real original book through its browser row')
    base_again = wait_book(replay, BOOK, 'original-last-after-sibling')
    replay.check('original_last_page_preserved_while_reading_sibling', body_delta(replay.experiment.frames[last], replay.experiment.frames[base_again]) == 0)
    menu(replay, 'suggestions-before-home')
    replay.tap('left', 'home-wrapped-selection', 'Physical front Left wraps first suggestion to Home')
    home = replay.tap('confirm', 'home-menu-activated', 'Confirm the actual Home row')
    text = ocr(replay, home, 'home-menu-activated')
    replay.check('menu_home_reaches_actual_home', 'browse files' in text.lower() and 'settings' in text.lower())
    final_progress = persist_file(replay, 'original-last-progress-after-home', cache + '/progress.bin')
    replay.check('menu_home_preserves_original_last_progress', final_progress == original_progress)
    replay.restart()
    replay.check('menu_home_progress_survives_new_cpu', replay.read_file(cache + '/progress.bin') == final_progress)
    replay.tap('confirm', 'original-cold-continue', 'Fresh CPU Home Continue reopens the original final page')
    cold_base = wait_book(replay, BOOK, 'original-cold-last-ready')
    replay.check('original_last_page_cold_body_exact', body_delta(replay.experiment.frames[last], replay.experiment.frames[cold_base]) == 0)
    replay.tap('back', 'final-home', 'Exit the final reader normally and close media writes')
    replay.check('all_original_input_files_unchanged', all(replay.read_file(path) == raw for path, raw in fixture_files().items()))
    replay.check('every_captured_native_trace_complete', all(frame.get('trace_complete') for frame in replay.receipt['frames'].values()))

def closed_trace_proof(run):
    result = json.loads((run / 'run.json').read_bytes())
    panel = result['final_state']['panel']
    raw = (run / 'panel.jsonl').read_bytes()
    events = [json.loads(line) for line in raw.splitlines()]
    frames = [e for e in events if e['event'] == 'frame-complete']
    dump = (run / 'panel.pbm').read_bytes()
    width, height, pixels = SMOKE['read_pgm'](dump)
    dump_counter = re.search(rb'\brefresh=(\d+)\b', dump[:dump.find(b'\n255\n')])
    errors = ('output-errors', 'trace-write-errors', 'trace-flush-errors', 'trace-close-errors',
              'dump-open-errors', 'dump-write-errors', 'dump-flush-errors', 'dump-close-errors', 'protocol-errors')
    checks = {
        'stopped_exit_zero': result.get('status') == 'stopped' and result.get('exit_code') == 0,
        'full_contiguous_trace': bool(events) and [e['seq'] for e in events] == list(range(1, len(events) + 1)),
        'all_attempts_flushed': len(events) == panel['trace-events-attempted'] == panel['trace-events-flushed'],
        'all_trace_bytes_durable': len(raw) == panel['trace-bytes-flushed'] == panel['trace-fd-size'] == panel['trace-fd-position'] == panel['trace-path-size'],
        'trace_file_identity': panel['trace-file-linked'] is True and panel['trace-fd-inode'] == panel['trace-path-inode'],
        'every_refresh_recorded': len(frames) == panel['refresh-count'] == panel['dump-frames-written'],
        'final_dump_crc_exact': bool(frames) and zlib.crc32(pixels) == panel['framebuffer-crc'] == frames[-1]['value'],
        'native_dump_dimensions_and_count': (width, height) == (792, 528) and dump_counter is not None and int(dump_counter[1]) == panel['refresh-count'],
        'zero_panel_output_errors': all(panel[key] == 0 for key in errors),
        'immutable_rx114': result['backend']['sha256'] == BACKEND_SHA,
        'zero_bad_dma': result['final_state']['wifi']['bad-dma'] == 0,
    }
    return {'run': str(run), 'checks': checks, 'functional_pass': all(checks.values()),
            'refreshes': len(frames), 'events': len(events), 'panel_jsonl_sha256': sha(raw),
            'run_manifest_sha256': file_sha256(run / 'run.json'), 'panel_dump_sha256': file_sha256(run / 'panel.pbm')}

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('output', 'source', 'sdk-source', 'flash'):
        parser.add_argument('--' + flag, type=Path, required=True)
    parser.add_argument('--backend', type=Path, default=DEFAULT_BACKEND)
    parser.add_argument('--rom-dir', type=Path, required=True)
    parser.add_argument('--host-limit', type=float, default=900)
    parser.add_argument('--step-timeout', type=float, default=120)
    parser.add_argument('--frozen', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    for key in ('output', 'source', 'sdk_source', 'flash', 'backend', 'rom_dir'):
        setattr(args, key, getattr(args, key).expanduser().resolve())
    if args.host_limit <= 0 or args.step_timeout <= 0:
        parser.error('positive time limits required')
    assert_sources(ROOT, CAPTURED)
    global SOURCE_PROOF, SDK_PROOF, EXECUTION_MANIFEST_SHA
    SOURCE_PROOF = pinned_source(args.source, SOURCE_PINS)
    SDK_PROOF = pinned_source(args.sdk_source, SDK_PINS)
    if file_sha256(args.backend) != BACKEND_SHA:
        parser.error('this cohort requires the reviewed immutable RX114 backend')
    if not args.frozen:
        if args.output.exists() and any(args.output.iterdir()):
            parser.error('output must be new or empty')
        args.output.mkdir(parents=True, exist_ok=True)
        snapshot = args.output / 'execution-source'
        rows = []
        for relative, raw in CAPTURED.items():
            destination = snapshot / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
            rows.append({'path': str(relative), 'sha256': sha(raw), 'bytes': len(raw)})
        manifest = (json.dumps(sorted(rows, key=lambda x: x['path']), indent=2) + '\n').encode()
        (snapshot / 'execution-source-manifest.json').write_bytes(manifest)
        command = [sys.executable, str(snapshot / 'scripts' / Path(__file__).name), '--frozen']
        for key in ('output', 'source', 'sdk_source', 'flash', 'backend', 'rom_dir', 'host_limit', 'step_timeout'):
            command += ['--' + key.replace('_', '-'), str(getattr(args, key))]
        with (args.output / 'frozen-harness-launcher.log').open('wb') as log:
            child = subprocess.run(command, cwd=snapshot, stdout=log, stderr=log)
        print(json.dumps({'output': str(args.output), 'frozen_harness_exit_code': child.returncode}))
        return child.returncode
    EXECUTION_MANIFEST_SHA = file_sha256(ROOT / 'execution-source-manifest.json')
    expected = json.loads((ROOT / 'execution-source-manifest.json').read_bytes())
    if any(file_sha256(ROOT / row['path']) != row['sha256'] for row in expected):
        raise ValueError('frozen source manifest mismatch')
    SHARED['SOURCE_SHA256'].update(SOURCE_PINS)
    SHARED['SOURCE_FILES'][NAME] = tuple(SOURCE_PINS)
    def recorded(replay):
        try:
            workflow(replay)
        except (TypeError, KeyError, IndexError, subprocess.SubprocessError) as error:
            raise ValueError('Host verifier ' + type(error).__name__ + ': ' + str(error)) from error
    SHARED['WORKFLOWS'][NAME] = recorded
    receipt = SHARED['run_workflow'](NAME, args, args.output / NAME, fixture_files=fixture_files(), open_book=False)
    assert_sources(ROOT, CAPTURED)
    pinned_source(args.source, SOURCE_PINS)
    pinned_source(args.sdk_source, SDK_PINS)
    closed = []
    for manifest in receipt.get('run_manifests', ['run/run.json']):
        path = args.output / NAME / manifest
        if path.exists():
            closed.append(closed_trace_proof(path.parent))
    receipt['closed_run_proofs'] = closed
    receipt['checks']['all_three_cpu_runs_have_closed_complete_traces'] = len(closed) == 3 and all(row['functional_pass'] for row in closed)
    receipt['checks']['source_provenance_unchanged'] = True
    receipt['functional_pass'] = bool(receipt.get('completed') and not receipt.get('error') and not receipt.get('shutdown_error')
                                      and receipt['checks'] and all(receipt['checks'].values()))
    receipt['strict_pass'] = bool(receipt['functional_pass'] and receipt.get('model_diagnostics_clean') and receipt.get('panel_trace_complete'))
    receipt['status'] = 'passed' if receipt['strict_pass'] else 'failed'
    SHARED['write_json'](args.output / NAME / 'validation.json', receipt)
    report = {'schema_version': 1, 'workflow': NAME, 'functional_pass': receipt['functional_pass'],
              'strict_pass': receipt['strict_pass'], 'receipt': NAME + '/validation.json',
              'receipt_sha256': file_sha256(args.output / NAME / 'validation.json'),
              'host_execution_source_manifest_sha256': EXECUTION_MANIFEST_SHA,
              'all_functions_verified': False, 'actual_crypto_verified': False,
              'physical_output_validated': False, 'speed_selection_allowed': False,
              'error': receipt.get('error'), 'closed_run_proofs': closed}
    SHARED['write_json'](args.output / 'validation.json', report)
    print(json.dumps(report), flush=True)
    return 0 if report['functional_pass'] else 1

if __name__ == '__main__':
    raise SystemExit(main())
