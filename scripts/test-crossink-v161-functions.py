#!/usr/bin/env python3
"""Real v1.6.1 reader functions on closed, genuine OTA-written firmware.

Uses fresh cards and two fresh CPUs per function. The host never writes guest
settings, progress, bookmarks or clippings. Original failures remain failures.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import runpy
import struct
import sys

PROJECT = Path(os.environ.get('X3EMU_V161_FUNCTIONS_PROJECT', Path(__file__).resolve().parents[1])).resolve()
sys.path.insert(0, str(PROJECT))
from x3emu.backend import file_sha256
from x3emu.sdcard import create_fat16_card, make_test_epub

OTA = runpy.run_path(str(PROJECT / 'scripts/test-crossink-online-ota.py'))
FOOTNOTES = runpy.run_path(str(PROJECT / 'scripts/test-crossink-v161-footnotes.py'))
OLD = runpy.run_path(str(PROJECT / 'scripts/test-crossink-functions.py'))
Error = OTA['OnlineOtaError']
EXTERNAL_ERRORS = (FOOTNOTES['FootnoteError'], OLD['SmokeError'])
SOURCE_COMMIT = OTA['TARGET_SOURCE']
SOURCE_HASHES = {'lib/Epub/Epub/Section.cpp': '7760b7b7e516767f58c909b9c2e7490ce0d1de83896922bd2cb4752b6f66f4fd',
 'src/BookmarkStore.cpp': 'bccb7636c133361e9362edecaccf3a9e2d5bbcc11ee60748163ad5bad99e820f',
 'src/BookmarkStore.h': 'ce92a45272515674255bb21ca10a7b1f1522ce4965feb6aa9887bd77b93a3c3e',
 'src/ClippingStore.cpp': 'e0534f47908d486c52388caccf095818a05fa254a6fd672f9d0d01e825070ad0',
 'src/ClippingStore.h': 'cb7981dc0f667eb84d9706473bbd4a07bdd40be826ccbb6e629fedabe65734dc',
 'src/CrossPointSettings.h': 'cb54c07310df751890cf4e4ec5069ba90cab1db390ad25240ae820afbdc5731f',
 'src/MappedInputManager.cpp': '52600d9835f62d940153352dd4c8864badd7a795a97b207c8ae99af67d955ef8',
 'src/SettingsList.h': 'd3a28a3baf728f121217ae149e749f2484e33468a9e3302a927eedecff44b163',
 'src/activities/reader/ClipSelectionActivity.cpp': '504779e09615e5015ea0daf833ca5a5d9dd84ca528ef882ee8e22d710702958e',
 'src/activities/reader/EpubReaderActivity.cpp': '2cae0773ceb5ba8e5d7a51e5582d21b71a319c408204e6bca67f2160adf966be',
 'src/activities/reader/EpubReaderActivity.h': '7ba827d7d5d3747dd23bfc905f7f45e1c43c1f3beb23f133af16131a7002e201',
 'src/activities/reader/EpubReaderBookmarkListActivity.cpp': '1ad3deb5bf5263a439df42e87d5530ad4ecc4ca098bb5e20e15d07a716684a10',
 'src/activities/reader/EpubReaderClippingListActivity.cpp': '2ece0c9861a703e2fc58ddc4be61bf26005ab314da83af050a8bbac515e9c8bf',
 'src/activities/reader/EpubReaderDrawerActivity.cpp': 'df2a90c8db22c8c3bc11bf07b8ab65ca94c7b26520b2eee193505fd005bbd3ef',
 'src/activities/reader/EpubReaderMenuModel.h': '41e4035743e2f776c56aff50dc5588ab101b000f8f159fd7638ad26b8189163f',
 'src/activities/reader/ReaderProgressSaveDebouncer.h': '4c9f51773e7feb90605fda5aa1e8d139e3c1d2c248e12f03d3f366dc242432aa',
 'src/activities/reader/ReaderUtils.h': 'fbeb9d10c860156b36c3ac517e3ec72e11b9dbc8fe781cc08224ba62b1ce54fb',
 'src/activities/settings/FontSelectionActivity.cpp': '7cd478b669df17872b1f4475c2418d06f16c0579628bdce13baf3d6c7187867c',
 'src/clippings/ClippingsManager.cpp': 'e355d073853bb2be6828116ab769eb23440e5603c4a6e8c9eaadcc7d9296766f'}
ALL_OVERRIDE_BITS = (1 << 19) - 1  # 18 source READER_SETTING_FIELDS plus SD font family.


def run_guarded(work, *args):
    # Separate runpy snapshots create distinct exception classes. Translate only
    # known observer failures so execute_cpu retains its original clean receipt.
    try:
        return work(*args)
    except EXTERNAL_ERRORS as error:
        raise Error(type(error).__name__ + ': ' + str(error)) from error


def decode_v10_reader_settings(data: bytes) -> dict:
    """Source's exact v10 snapshot followed by its bounded u32 override mask."""
    if len(data) != 157 or data[0] != 10:
        raise Error('reader settings are not the exact v10/157-byte target schema')
    if data[1] & ~0x1f:
        raise Error('reader settings contain unknown source flags')
    mask = struct.unpack_from('<I', data, 153)[0]
    if mask & ~ALL_OVERRIDE_BITS:
        raise Error('reader settings contain unknown v10 override fields')
    keys = ('font_family', 'font_point_size', 'line_height_percent', 'word_spacing', 'orientation',
            'margin_vertical', 'margin_horizontal', 'publisher_page_numbers', 'paragraph_alignment',
            'embedded_style', 'hyphenation', 'text_antialiasing', 'image_rendering',
            'extra_paragraph_spacing', 'force_paragraph_indents', 'focus_reading', 'guide_reading',
            'render_mode', 'indexing_method')
    result = dict(zip(keys, data[5:24]))
    result.update(version=10, flags=data[1], auto_page_turn_seconds=struct.unpack_from('<H', data, 2)[0],
        render_mode_override=data[4], override_mask=mask,
        sd_font_family=data[24:88].split(b'\0', 1)[0].decode('utf-8'),
        dictionary_font_family=data[88:152].split(b'\0', 1)[0].decode('utf-8'),
        dictionary_font_point_size=data[152], sha256=hashlib.sha256(data).hexdigest())
    return result


def require_passed_ota_receipt(receipt):
    cpus = receipt.get('cpus', [])
    if (receipt.get('status') != 'passed' or receipt.get('functional_pass') is not True
            or len(cpus) != 3 or receipt.get('online_installation_exercised') is not True
            or receipt.get('guest_firmware_modified') is not False or receipt.get('tls_trust_modified') is not False
            or any(c.get('status') != 'passed' or c.get('functional_pass') is not True
                   or c.get('error') or c.get('closure_error') or not c.get('checks')
                   or any(value is not True for value in c['checks'].values()) for c in cpus)):
        raise Error('requires original passed genuine three-CPU OTA receipt and every child postcondition')


def section(replay, *, previous_sha=None):
    def ready():
        try:
            decoded = OTA['decode_v161_section'](replay.read(replay.helpers['cache_path']() + '/sections/0.bin'))
            return decoded if previous_sha is None or decoded['sha256'] != previous_sha else False
        except (FileNotFoundError, Error):
            return False
    value = replay.experiment.wait('actual finalized v83 section' + (' after changed font' if previous_sha else ''), ready)
    replay.check('actual_finalized_v83_layout', True, value)
    return value


def reader_frame(replay, label, page, current_section):
    frame = replay.capture_text(label, [f'{page + 1}/{current_section["page_count"]}'])
    observed = FOOTNOTES['rendered_progress'](replay, label, frame, current_section)
    replay.check(label + '_actual_visible_position', observed['spine_index'] == 0 and observed['page_number'] == page,
                 {'position': observed, 'capture': frame, 'disk_progress_is_not_a_live_counter': True})
    return frame


def pixels(replay, frame):
    return OTA['captured_pixels'](replay, frame)


def equal_pixels(replay, name, first, second):
    changes = replay.helpers['changed_pixels'](first, second)
    expected = replay.helpers['read_pgm'](first)[2]
    actual = replay.helpers['read_pgm'](second)[2]
    replay.check(name, changes == 0, {'changed_pixels': changes,
        'expected_pixel_sha256': hashlib.sha256(expected).hexdigest(),
        'actual_pixel_sha256': hashlib.sha256(actual).hexdigest(), 'comparison': 'all native luminance pixels'})


def dwell_feedback(replay, label):
    start = replay.experiment.clock(replay.qmp)
    replay.experiment.wait('source1000ms transient feedback expiry',
                           lambda: replay.experiment.clock(replay.qmp) >= start + 1_200_000_000)
    replay.report.setdefault('virtual_dwells', []).append({'purpose': label, 'start_ns': start,
        'minimum_duration_ns': 1_200_000_000, 'end_ns': replay.experiment.clock(replay.qmp)})
    replay.save()


def home_open(replay, client, *, virgin, page):
    status = replay.experiment.wait('actual unchanged v1.6.1 USB Home identity',
                                    lambda: OTA['running_version'](replay, client, '1.6.1'))
    replay.check('actual_running_stock_v161', bool(status), status)
    replay.capture('stock-v161-home')
    if virgin:
        replay.tap('confirm', 'browse-files', 'Fresh card Home: open genuine Browse Files')
    before = replay.experiment.refresh_count(replay.qmp)
    replay.experiment.press(replay.qmp, 'confirm', purpose='Open genuine /test.epub' if virgin else 'Continue guest-saved /test.epub')
    replay.experiment.wait('actual guest openEpubPath/test.epub', replay.experiment.book_is_open)
    current = section(replay)
    replay.capture('book-opened-refreshed', before)
    frame = reader_frame(replay, 'reader-opened', page, current)
    if virgin:
        replay.check('no_seeded_bookmark_or_clipping_or_per_book_settings',
            replay.absent(OLD['bookmark_path']()) and replay.absent(OLD['clipping_path']())
            and replay.absent(replay.helpers['cache_path']() + '/reader_settings.bin'))
    return current, frame


def location(replay, *, retained_after_clipping=False):
    # The non-touch SAVE_CLIPPING result sets reopenDrawer=true, retaining
    # Location even while its selector later returns directly to the reader.
    # A new/cancelled drawer instead starts at the source's initial More tab.
    replay.tap('confirm', 'open-reader-location' if retained_after_clipping else 'open-reader-more',
               'Source SAVE_CLIPPING retained Location tab' if retained_after_clipping
               else 'X3 reader: open source initial More tab')
    if not retained_after_clipping:
        replay.tap('confirm', 'location-tab', 'Source tab order: More to Location')
    replay.capture_text('actual-location-tab', ('Create Clipping',))


def row(replay, count, label):
    for i in range(count):
        replay.tap('right', f'{label}-{i}', 'Physical front Right is source menu Down: select next actual row')


def saved_position(replay, page):
    value = FOOTNOTES['progress'](replay)
    replay.check('actual_exit_saved_position', value['spine_index'] == 0 and value['page_number'] == page,
                 value)
    replay.report['saved_progress'] = value
    replay.save()
    return value


def bookmarks_warm(replay, client):
    current, _ = home_open(replay, client, virgin=True, page=0)
    replay.tap('down', 'page1', 'Read actual page1 before creating the bookmark')
    reader_frame(replay, 'page1-before-bookmark', 1, current)
    location(replay); row(replay, 1, 'bookmark-toggle')
    replay.tap('confirm', 'bookmark-created', 'Location first row: add genuine bookmark; source reopens drawer')
    saved = replay.read(OLD['bookmark_path']()); store = OLD['decode_bookmarks'](saved)
    replay.check('genuine_v5_bookmark_created', store['count'] == 1 and store['book_path'] == '/test.epub'
        and store['entries'][0]['spine_index'] == 0
        and abs(store['entries'][0]['progress'] - 1/current['page_count']) < 1e-6, store)
    replay.tap('back', 'bookmark-drawer-closed', 'Source reopened drawer has header focus: Back returns to reader')
    dwell_feedback(replay, 'bookmark transient feedback')
    reference = reader_frame(replay, 'ordinary-bookmarked-page1', 1, current)
    replay.tap('down', 'page-away', 'Leave the bookmark and read actual page2')
    reader_frame(replay, 'actual-page2-away', 2, current)
    location(replay); row(replay, 2, 'view-bookmarks')
    replay.tap('confirm', 'bookmark-list', 'Location second row: View Bookmarks')
    replay.tap('confirm', 'bookmark-jump', 'Select the one genuine stored bookmark')
    returned = reader_frame(replay, 'bookmark-restored-page1', 1, current)
    equal_pixels(replay, 'bookmark_jump_restores_entire_page', pixels(replay, reference), pixels(replay, returned))
    replay.check('bookmark_jump_preserves_actual_store', replay.read(OLD['bookmark_path']()) == saved)
    replay.report['saved_reference_frame'] = returned
    replay.report['saved_bookmark_sha256'] = hashlib.sha256(saved).hexdigest()
    replay.tap('back', 'exit-bookmarked-reader', 'Exit and flush the actual bookmarked page1')
    saved_position(replay, 1)


def bookmarks_cold(replay, client, warm, warm_dir):
    current, frame = home_open(replay, client, virgin=False, page=1)
    saved = replay.read(OLD['bookmark_path']())
    replay.check('bookmark_store_survives_fresh_cpu', hashlib.sha256(saved).hexdigest() == warm['saved_bookmark_sha256'])
    expected = (warm_dir / warm['saved_reference_frame']['path']).read_bytes()
    equal_pixels(replay, 'cold_bookmarked_page_restores_entire_frame', expected, pixels(replay, frame))
    location(replay); row(replay, 1, 'remove-bookmark')
    replay.tap('confirm', 'bookmark-removed', 'Actual first Location row toggles off current bookmark and reopens drawer')
    replay.check('removing_last_bookmark_deletes_v5_store', replay.absent(OLD['bookmark_path']()))
    replay.tap('back', 'removed-drawer-closed', 'Close reopened header-focused Location drawer')
    dwell_feedback(replay, 'bookmark removed feedback')
    reader_frame(replay, 'ordinary-page-after-removal', 1, current)
    replay.tap('back', 'exit-after-removal', 'Exit the real reader after bookmark removal')
    saved_position(replay, 1)


def clippings_warm(replay, client):
    current, original = home_open(replay, client, virgin=True, page=0)
    location(replay); row(replay, 2, 'save-clipping')
    replay.tap('confirm', 'clipping-selector', 'Empty Location rows: Bookmark Toggle, Save Clipping; open actual selector')
    start = replay.tap('confirm', 'selection-start', 'Mark genuine middle-page word as range start')
    extended = replay.tap('right', 'selection-extended', 'Actual word navigator: extend exactly one word')
    replay.check('actual_selection_changes_native_highlight', replay.helpers['changed_pixels'](pixels(replay,start), pixels(replay,extended)) > 0)
    replay.tap('confirm', 'clipping-saved-feedback', 'Finish actual range and save genuine clipping plus export')
    dwell_feedback(replay, 'source1000ms clipping saved toast')
    saved = replay.read(OLD['clipping_path']()); store = OLD['decode_clippings'](saved)
    item = store['entries'][0] if store['count'] == 1 else {}
    replay.check('genuine_v4_clipping_created', store['count'] == 1 and store['book_path'] == '/test.epub'
        and item.get('spine_index') == 0 and item.get('start_page') == item.get('end_page') == 0
        and item.get('page_count') == current['page_count'] and item.get('word_count') == 2
        and bool(item.get('text','').strip()), store)
    exported = replay.read('/My Clippings.txt')
    replay.check('actual_clipping_text_exported', item['text'].encode() in exported and b'Your Highlight on Page' in exported,
                 {'export_sha256':hashlib.sha256(exported).hexdigest(),'selected_text':item['text']})
    reference = reader_frame(replay, 'ordinary-saved-clipping-page0', 0, current)
    replay.tap('down', 'leave-clipping', 'Read actual page1 away from the saved clipping')
    reader_frame(replay, 'actual-page1-away-from-clipping', 1, current)
    location(replay, retained_after_clipping=True)
    replay.capture_text('actual-location-clipping-row', ('Create Clipping', 'View Clippings'))
    row(replay, 3, 'view-clippings')
    replay.tap('confirm', 'clipping-list', 'Location third row after Bookmark Toggle and Save Clipping: View Clippings')
    replay.tap('confirm', 'clipping-detail', 'Read the one genuine stored clipping detail')
    replay.tap('confirm', 'clipping-jump', 'Detail Confirm jumps to the actual clipping anchor')
    returned = reader_frame(replay, 'clipping-returned-page0', 0, current)
    equal_pixels(replay, 'clipping_jump_restores_entire_highlighted_page', pixels(replay,reference), pixels(replay,returned))
    replay.check('clipping_jump_preserves_store_and_export', replay.read(OLD['clipping_path']()) == saved and replay.read('/My Clippings.txt') == exported)
    replay.report.update(saved_reference_frame=returned, saved_clipping_sha256=hashlib.sha256(saved).hexdigest(),
                         saved_export_sha256=hashlib.sha256(exported).hexdigest())
    replay.tap('back', 'exit-clipped-reader', 'Exit and flush actual clipping anchor page0')
    saved_position(replay, 0)


def clippings_cold(replay, client, warm, warm_dir):
    _, frame = home_open(replay,client,virgin=False,page=0)
    replay.check('clipping_store_and_export_survive_fresh_cpu',
        hashlib.sha256(replay.read(OLD['clipping_path']())).hexdigest() == warm['saved_clipping_sha256']
        and hashlib.sha256(replay.read('/My Clippings.txt')).hexdigest() == warm['saved_export_sha256'])
    expected = (warm_dir / warm['saved_reference_frame']['path']).read_bytes()
    equal_pixels(replay,'cold_clipping_restores_entire_highlighted_frame',expected,pixels(replay,frame))
    replay.tap('back','exit-cold-clipping','Exit unchanged fresh-CPU clipping reader')
    saved_position(replay,0)


def fonts_warm(replay, client):
    current, original = home_open(replay,client,virgin=True,page=0)
    replay.tap('confirm','reader-more-tab','X3 source initial More tab')
    for label in ('Location','Settings','Font'):
        replay.tap('confirm','font-tab-via-'+label.lower(),'Source header Confirm cycles More, Location, Settings, Font')
    row(replay,1,'reader-font-row');replay.tap('confirm','reader-font-pane','Open actual Reader Font pane')
    replay.tap('confirm','family-picker','First pane row is Font Family')
    row(replay,1,'next-built-in-family')
    replay.tap('confirm','family-preview','Preview next builtin Bitter family before Select')
    replay.tap('confirm','family-selected','Confirm again selects preview and returns Reader Font pane')
    row(replay,1,'font-size-row');replay.tap('confirm','font-size-picker','Second Reader Font row opens point sizes')
    row(replay,1,'next-point-size')
    replay.tap('confirm','font-size-preview','Preview the next source point size')
    replay.tap('confirm','font-size-selected','Select the already previewed point size')
    replay.tap('back','font-root','Close Reader Font pane to header-focused Font root')
    replay.tap('back','changed-font-reader','Close actual drawer and commit changed per-book draft')
    changed = section(replay,previous_sha=current['sha256'])
    saved = replay.read(replay.helpers['cache_path']()+'/reader_settings.bin');settings=decode_v10_reader_settings(saved)
    replay.check('genuine_v10_family_and_size_overrides_saved', settings['flags'] & 1 and settings['font_family']==1
        and settings['font_point_size']>14 and settings['override_mask'] & 3 == 3 and not settings['sd_font_family'],settings)
    reference=reader_frame(replay,'actual-new-font-page0',0,changed)
    replay.check('new_builtin_font_changes_actual_rendered_pixels',replay.helpers['changed_pixels'](pixels(replay,original),pixels(replay,reference))>1000)
    replay.report.update(saved_reference_frame=reference,saved_font_settings_sha256=hashlib.sha256(saved).hexdigest())
    replay.tap('back','exit-new-font-reader','Exit and flush actual reflowed page0')
    replay.check('v10_font_settings_preserved_on_exit',replay.read(replay.helpers['cache_path']()+'/reader_settings.bin')==saved)
    saved_position(replay,0)


def fonts_cold(replay,client,warm,warm_dir):
    _,frame=home_open(replay,client,virgin=False,page=0)
    raw=replay.read(replay.helpers['cache_path']()+'/reader_settings.bin');settings=decode_v10_reader_settings(raw)
    replay.check('v10_font_settings_survive_fresh_cpu',hashlib.sha256(raw).hexdigest()==warm['saved_font_settings_sha256'],settings)
    expected=(warm_dir/warm['saved_reference_frame']['path']).read_bytes()
    equal_pixels(replay,'cold_font_restores_entire_native_frame',expected,pixels(replay,frame))
    replay.tap('back','exit-cold-font-reader','Exit real fresh-CPU font reader')
    saved_position(replay,0)


def closed_media(directory, child, backend, rom_dir):
    run=directory/'run';manifest_path=run/'run.json';manifest=json.loads(manifest_path.read_text())
    if child.get('rom_reset_observations') != [{'code': 1, 'name': 'POWERON'}]:
        raise Error('reader function CPU did not retain an independent POWERON-only ROM reset history')
    if manifest!=child.get('run_manifest') or manifest.get('status')!='stopped' or manifest.get('exit_code')!=0 or manifest.get('error'):
        raise Error('actual source child manifest is not its clean stopped receipt')
    if manifest['backend']['sha256']!=file_sha256(backend) or manifest['rom']['sha256']!=file_sha256(rom_dir/'esp32c3-rom.bin'):
        raise Error('closed child CPU differs from exact accepted backend/ROM')
    for name,digest in manifest['artifact_sha256'].items():
        path=PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts or file_sha256(run/name)!=digest:
            raise Error('closed native artifact hash differs: '+name)
    hashes={'flash.bin':file_sha256(run/'flash.bin'),'sd.img':file_sha256(run/'sd.img'),'efuse.bin':file_sha256(run/'efuse.bin')}
    if hashes!={'flash.bin':manifest['storage']['final_flash_sha256'],'sd.img':manifest['storage']['final_sd_sha256'],
                'efuse.bin':manifest['artifact_sha256']['efuse.bin']}:
        raise Error('closed actual written media differs from stopped manifest')
    if json.loads(manifest_path.read_text())!=manifest:
        raise Error('stopped manifest changed during closed carry validation')
    return {'run_manifest_sha256':file_sha256(manifest_path),'actual_final_media_sha256':hashes}


def main(argv=None):
    cli=argparse.ArgumentParser(description=__doc__)
    for name in ('ota-output','backend','rom-dir','source','output'):
        cli.add_argument('--'+name,type=Path,required=True)
    cli.add_argument('--workflows',default='bookmarks,clippings,fonts')
    cli.add_argument('--host-limit',type=float,default=1200)
    cli.add_argument('--step-timeout',type=float,default=180)
    args=cli.parse_args(argv)
    names=args.workflows.split(',')
    if not names or len(set(names))!=len(names) or any(n not in ('bookmarks','clippings','fonts') for n in names):
        cli.error('workflows must be distinct names from bookmarks,clippings,fonts')
    for key in ('ota_output','backend','rom_dir','source','output'):setattr(args,key,getattr(args,key).resolve())
    if any(not math.isfinite(v) or v<=0 for v in (args.host_limit,args.step_timeout)):cli.error('time limits must be positive finite')
    if args.output.exists():cli.error('output must be new; original failures are retained')
    args.output.mkdir(parents=True)
    report={'schema_version':1,'status':'running','functional_pass':False,'cpus':[], 'workflows':names,
        'source_snapshot_commit':SOURCE_COMMIT,'source_sha256':SOURCE_HASHES,'binary_build_commit_verified':False,
        'official_v161_application_sha256':OTA['TARGET_SHA256'],'guest_firmware_modified':False,
        'guest_progress_or_settings_or_saved_items_seeded':False,'all_functions_verified':False,
        'complete_machine_verified':False,'physical_timing_calibrated':False,'speed_selection_allowed':False}
    save=lambda:OTA['write_json'](args.output/'validation.json',report)
    save()
    try:
        for path,digest in SOURCE_HASHES.items():
            if file_sha256(args.source/path)!=digest:raise Error('exact target source pin differs: '+path)
        flash,efuse,provenance=FOOTNOTES['validate_ota_input'](args.ota_output,args.backend,args.rom_dir)
        require_passed_ota_receipt(json.loads((args.ota_output/'validation.json').read_text()))
        report['accepted_genuine_three_cpu_ota_input']=provenance
        smoke=runpy.run_path(str(PROJECT/'scripts/smoke-crossink.py'))
        network=runpy.run_path(str(PROJECT/'scripts/test-crossink-network.py'))
        book=make_test_epub();report['generated_book']={'sha256':hashlib.sha256(book).hexdigest(),'bytes':len(book)}
        report['host_source_sha256']=file_sha256(Path(__file__))
        functions={'bookmarks':(bookmarks_warm,bookmarks_cold),'clippings':(clippings_warm,clippings_cold),'fonts':(fonts_warm,fonts_cold)}
        for name in names:
            try:
                card=args.output/(name+'-virgin-card.img');create_fat16_card(card,{'/test.epub':book})
                if smoke['Fat16Card'](card).read_file('/test.epub')!=book:raise Error('virgin card book roundtrip failed')
                if any(e['name'].lower()!='test.epub' or e['directory'] for e in smoke['Fat16Card'](card).directory(0)):
                    raise Error('fresh card contains seeded files or directories')
                warm=args.output/(name+'-warm')
                first=OTA['execute_cpu'](args,warm,flash,card,efuse=efuse,work=lambda replay,client:run_guarded(functions[name][0],replay,client),smoke=smoke,network=network)
                report['cpus'].append(first);report.setdefault('cpu_stages',[]).append({'workflow':name,'phase':'warm','directory':warm.name});save()
                if not first['functional_pass']:raise Error('actual '+name+' warm workflow failed; original receipt retained')
                carry=closed_media(warm,first,args.backend,args.rom_dir)
                report.setdefault('closed_carry_bindings',{})[name]=carry;save()
                # Reread exact originals immediately before the fresh CPU launch.
                if closed_media(warm,first,args.backend,args.rom_dir)!=carry:raise Error('actual warm carry changed before launch')
                cold=args.output/(name+'-cold')
                work=lambda replay,client: run_guarded(functions[name][1],replay,client,first,warm)
                second=OTA['execute_cpu'](args,cold,warm/'run/flash.bin',warm/'run/sd.img',efuse=warm/'run/efuse.bin',work=work,smoke=smoke,network=network)
                report['cpus'].append(second);report['cpu_stages'].append({'workflow':name,'phase':'cold','directory':cold.name});save()
                if not second['functional_pass']:raise Error('actual '+name+' cold workflow failed; original receipt retained')
                closed_media(cold,second,args.backend,args.rom_dir)
                if {'flash.bin':second['initial_flash_sha256'],'sd.img':second['initial_sd_sha256'],'efuse.bin':second['initial_efuse_sha256']}!=carry['actual_final_media_sha256']:
                    raise Error('fresh CPU copied inputs differ from the actual closed warm media')
                for directory in (warm,cold):
                    if smoke['Fat16Card'](directory/'run/sd.img').read_file('/test.epub')!=book:raise Error('guest changed original generated book bytes')
            except (Error,OSError,ValueError,KeyError) as error:
                report.setdefault('workflow_errors',{})[name]=str(error);save()
        if file_sha256(flash)!=provenance['carried_flash_sha256'] or file_sha256(efuse)!=provenance['carried_efuse_sha256']:
            raise Error('original accepted OTA input media changed')
        report['functional_pass']=len(report['cpus'])==2*len(names) and all(c['functional_pass'] for c in report['cpus']) and not report.get('workflow_errors')
    except (Error,*EXTERNAL_ERRORS,OSError,ValueError,KeyError) as error:report['error']=str(error)
    report['status']='passed' if report['functional_pass'] else 'failed';save()
    print(json.dumps({'status':report['status'],'functional_pass':report['functional_pass'],'workflow_errors':report.get('workflow_errors'),
                      'error':report.get('error'),'receipt':str(args.output/'validation.json')},indent=2))
    return 0 if report['functional_pass'] else 1

if __name__=='__main__':raise SystemExit(main())
