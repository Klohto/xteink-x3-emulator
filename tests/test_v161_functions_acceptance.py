"""Protocol refusal tests; no native or guest execution."""
import copy
import hashlib
import os
from pathlib import Path
import runpy
import struct
import unittest

PROJECT = Path(os.environ.get('X3EMU_V161_FUNCTIONS_PROJECT', Path(__file__).resolve().parents[1])).resolve()
SCRIPT = Path(os.environ.get('X3EMU_V161_FUNCTIONS_SCRIPT', PROJECT/'scripts/test-crossink-v161-functions.py'))
G = runpy.run_path(str(SCRIPT))


def source_v10_bytes():
    # Independent source writer: version/flags/u16 interval/render mode,
    # 19 scalar bytes, two64-byte names, dictionary point size, u32 mask.
    return (struct.pack('<BBHB',10,1,37,2)
        + bytes([1,16,108,3,0,6,7,0,0,1,1,1,0,0,0,0,0,2,0])
        + b'\0'*128 + bytes([0]) + struct.pack('<I',3))


class TargetV10ReaderSettingsTests(unittest.TestCase):
    def test_source_v10_fields_and_overrides(self):
        d=G['decode_v10_reader_settings'](source_v10_bytes())
        self.assertEqual((d['version'],d['font_family'],d['font_point_size'],d['line_height_percent']), (10,1,16,108))
        self.assertEqual((d['auto_page_turn_seconds'],d['margin_vertical'],d['margin_horizontal'],d['override_mask']), (37,6,7,3))
        self.assertEqual((d['render_mode_override'],d['render_mode'],d['indexing_method']), (2,2,0))
        self.assertEqual(d['sd_font_family'],'')

    def test_v9_is_not_relabelled_v10(self):
        old=bytes([9])+source_v10_bytes()[1:153]
        with self.assertRaises(G['Error']):G['decode_v10_reader_settings'](old)

    def test_missing_partial_and_trailing_mask_refused(self):
        raw=source_v10_bytes()
        for data in [b'',raw[:-4],raw[:-1],raw+b'\0']:
            with self.subTest(size=len(data)),self.assertRaises(G['Error']):G['decode_v10_reader_settings'](data)

    def test_unknown_override_field_refused(self):
        raw=source_v10_bytes()[:-4]+struct.pack('<I',1<<19)
        with self.assertRaises(G['Error']):G['decode_v10_reader_settings'](raw)

    def test_last_known_sd_family_override_allowed(self):
        raw=source_v10_bytes()[:-4]+struct.pack('<I',1<<18)
        self.assertEqual(G['decode_v10_reader_settings'](raw)['override_mask'],1<<18)

    def test_unknown_flags_refused(self):
        raw=source_v10_bytes();raw=raw[:1]+bytes([0x21])+raw[2:]
        with self.assertRaises(G['Error']):G['decode_v10_reader_settings'](raw)

    def test_actual_source_snapshot_pins(self):
        # Meaningful schema controls, not the old absent ReaderOptions source.
        pins=G['SOURCE_HASHES']
        self.assertEqual(pins['src/activities/reader/EpubReaderActivity.cpp'], '2cae0773ceb5ba8e5d7a51e5582d21b71a319c408204e6bca67f2160adf966be')
        self.assertEqual(pins['src/ClippingStore.cpp'], 'e0534f47908d486c52388caccf095818a05fa254a6fd672f9d0d01e825070ad0')
        self.assertNotIn('src/activities/reader/ReaderOptionsActivity.cpp',pins)


class NativePgmComparisonTests(unittest.TestCase):
    def test_complete_pgm_input_compares_pixels_despite_different_capture_headers(self):
        body=bytes([0,80,160,255])
        first=b'P5\n# refresh=17\n2 2\n255\n'+body
        second=b'P5\n# refresh=4\n2 2\n255\n'+body
        class Observer:
            helpers=G['OLD']['SMOKE']
            def check(self,name,condition,evidence):
                self.observed=(name,condition,evidence)
        observer=Observer();G['equal_pixels'](observer,'real-byte-format',first,second)
        name,condition,evidence=observer.observed
        self.assertTrue(condition);self.assertEqual(evidence['changed_pixels'],0)
        self.assertEqual(evidence['expected_pixel_sha256'],hashlib.sha256(body).hexdigest())
        self.assertEqual(evidence['actual_pixel_sha256'],hashlib.sha256(body).hexdigest())


class OriginalOtaPassRefusalTests(unittest.TestCase):
    def receipt(self):
        return {'status':'passed','functional_pass':True,'online_installation_exercised':True,
            'guest_firmware_modified':False,'tls_trust_modified':False,
            'cpus':[{'status':'passed','functional_pass':True,'checks':{'actual_guest_postcondition':True}} for _ in range(3)]}

    def test_external_observer_error_reaches_native_closure_as_failure(self):
        original=G['FOOTNOTES']['FootnoteError']('original bound counter differs')
        def work():raise original
        with self.assertRaises(G['Error']) as caught:G['run_guarded'](work)
        self.assertIs(caught.exception.__cause__,original)
        self.assertIn('original bound counter differs',str(caught.exception))

    def test_child_failure_and_original_master_failure_refused(self):
        original=self.receipt();G['require_passed_ota_receipt'](original)
        for change in ('root','child','check','closure','trust','count'):
            d=copy.deepcopy(original)
            if change=='root':d['status']='failed'
            elif change=='child':d['cpus'][2]['functional_pass']=False
            elif change=='check':d['cpus'][2]['checks']['actual_guest_postcondition']=False
            elif change=='closure':d['cpus'][2]['closure_error']='original trace failure'
            elif change=='trust':d['tls_trust_modified']=True
            elif change=='count':d['cpus'].pop()
            with self.subTest(change=change),self.assertRaises(G['Error']):G['require_passed_ota_receipt'](d)

if __name__=='__main__':unittest.main()
