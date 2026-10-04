"""Independent host packet gates; never a replacement for stock WiFi replay."""
from pathlib import Path
from contextlib import contextmanager
import hashlib
import hmac
import json
import base64
import runpy
import struct
import tempfile
import unittest

WIRE = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts/test-crossink-secure-wifi.py'))

# Public synthetic vectors independently emitted by the C QEMU-AES CCM
# foundation and cryptography AESCCM. Original vector file SHA256:
# aac86031742c7c2caa13b6c1efb568e8e7c7d9b1a1bddd1e8b0335a692a460f6.
# These fixed ciphertexts prevent a shared encoder/decoder mistake from passing.
CCMP_GOLDENS = json.loads("[{\"key\":\"000102030405060708090a0b0c0d0e0f\",\"header\":\"0841778802000000000102000000000202000000000330b6\",\"extiv\":\"0300002000000000\",\"pn\":3,\"tid\":0,\"key_id\":0,\"aad\":\"08410200000000010200000000020200000000030000\",\"nonce\":\"00020000000002000000000003\",\"plain\":\"aaaa030000000800dc01264b7095badf04294e7398bde2072c51769bc0e50a2f54799ec3e80d32577ca1c6eb10355a7fa4c9ee13385d82\",\"ciphertext_mic\":\"e2c6cc10e7c83b595d76cf77282cb8498a9017fb017e9e859f1badea9d1391c876928f24bcdcc71255abaa11e35353baa0d652111cf14e922cdcf70d466f4b\",\"mpdu_without_fcs\":\"0841778802000000000102000000000202000000000330b60300002000000000e2c6cc10e7c83b595d76cf77282cb8498a9017fb017e9e859f1badea9d1391c876928f24bcdcc71255abaa11e35353baa0d652111cf14e922cdcf70d466f4b\"},{\"key\":\"000102030405060708090a0b0c0d0e0f\",\"header\":\"0842778802000000000102000000000202000000000330b6\",\"extiv\":\"0300002000000000\",\"pn\":3,\"tid\":0,\"key_id\":0,\"aad\":\"08420200000000010200000000020200000000030000\",\"nonce\":\"00020000000002000000000003\",\"plain\":\"aaaa0300000008008cb1d6fb20456a8fb4d9fe23486d92b7dc01264b7095badf04294e7398bde2072c51769bc0e50a2f54799ec3e80d32\",\"ciphertext_mic\":\"e2c6cc10e7c83b590dc63fc778fc68193a60a7abd1aeee356f4bfd3a2d63213826c25f94cc6c1742055b1a61338303ea506622c1cca1fec5a0bf09966fcf49\",\"mpdu_without_fcs\":\"0842778802000000000102000000000202000000000330b60300002000000000e2c6cc10e7c83b590dc63fc778fc68193a60a7abd1aeee356f4bfd3a2d63213826c25f94cc6c1742055b1a61338303ea506622c1cca1fec5a0bf09966fcf49\"},{\"key\":\"000102030405060708090a0b0c0d0e0f\",\"header\":\"8841778802000000000102000000000202000000000330b60000\",\"extiv\":\"0300002000000000\",\"pn\":3,\"tid\":0,\"key_id\":0,\"aad\":\"884102000000000102000000000202000000000300000000\",\"nonce\":\"00020000000002000000000003\",\"plain\":\"aaaa030000000800ec11365b80a5caef14395e83a8cdf2173c6186abd0f51a3f6489aed3f81d42678cb1d6fb20456a8fb4d9fe23486d92\",\"ciphertext_mic\":\"e2c6cc10e7c83b596d66df67d81cc8799a80070b310e8e958f2b5dda8d0381d84662bf34acccb722a5bbba01d323634ab0c642216cc15ed628760f099e6fe4\",\"mpdu_without_fcs\":\"8841778802000000000102000000000202000000000330b600000300002000000000e2c6cc10e7c83b596d66df67d81cc8799a80070b310e8e958f2b5dda8d0381d84662bf34acccb722a5bbba01d323634ab0c642216cc15ed628760f099e6fe4\"},{\"key\":\"000102030405060708090a0b0c0d0e0f\",\"header\":\"8842778802000000000102000000000202000000000330b60000\",\"extiv\":\"0300002000000000\",\"pn\":3,\"tid\":0,\"key_id\":0,\"aad\":\"884202000000000102000000000202000000000300000000\",\"nonce\":\"00020000000002000000000003\",\"plain\":\"aaaa0300000008009cc1e60b30557a9fc4e90e33587da2c7ec11365b80a5caef14395e83a8cdf2173c6186abd0f51a3f6489aed3f81d42\",\"ciphertext_mic\":\"e2c6cc10e7c83b591db60f3768ec78094a5057bbc1bede455f5bed2add53510836d24f64fc1c0752156bea51239313fa609612d1dcb18e766db03cad2a3ff8\",\"mpdu_without_fcs\":\"8842778802000000000102000000000202000000000330b600000300002000000000e2c6cc10e7c83b591db60f3768ec78094a5057bbc1bede455f5bed2add53510836d24f64fc1c0752156bea51239313fa609612d1dcb18e766db03cad2a3ff8\"},{\"key\":\"000102030405060708090a0b0c0d0e0f\",\"header\":\"8841778802000000000102000000000202000000000330b60700\",\"extiv\":\"0300002000000000\",\"pn\":3,\"tid\":7,\"key_id\":0,\"aad\":\"884102000000000102000000000202000000000300000700\",\"nonce\":\"07020000000002000000000003\",\"plain\":\"aaaa030000000800fc21466b90b5daff24496e93b8dd02274c7196bbe0052a4f7499bee3082d52779cc1e60b30557a9fc4e90e33587da2\",\"ciphertext_mic\":\"a63a2a25db13a626250c3f3a6cab9a43db75c99fff68516e849d6b80b6f609f2643ef651d5dca1f8544a968dcc66df0ac66a99b1ce8d5bfc208494e8175df1\",\"mpdu_without_fcs\":\"8841778802000000000102000000000202000000000330b607000300002000000000a63a2a25db13a626250c3f3a6cab9a43db75c99fff68516e849d6b80b6f609f2643ef651d5dca1f8544a968dcc66df0ac66a99b1ce8d5bfc208494e8175df1\"},{\"key\":\"000102030405060708090a0b0c0d0e0f\",\"header\":\"8842778802000000000102000000000202000000000330b60700\",\"extiv\":\"0300002000000000\",\"pn\":3,\"tid\":7,\"key_id\":0,\"aad\":\"884202000000000102000000000202000000000300000700\",\"nonce\":\"07020000000002000000000003\",\"plain\":\"aaaa030000000800acd1f61b40658aafd4f91e43688db2d7fc21466b90b5daff24496e93b8dd02274c7196bbe0052a4f7499bee3082d52\",\"ciphertext_mic\":\"a63a2a25db13a62675fc8f4abc7bca132bc5b94f2f38e19e34cdbb50c646f94234ee2621652cf1a884fae63d1c368fda761a29619eddab4f2f13979b82b875\",\"mpdu_without_fcs\":\"8842778802000000000102000000000202000000000330b607000300002000000000a63a2a25db13a62675fc8f4abc7bca132bc5b94f2f38e19e34cdbb50c646f94234ee2621652cf1a884fae63d1c368fda761a29619eddab4f2f13979b82b875\"}]")


@contextmanager
def stopped_ap():
    class Channel:
        def __init__(self): self.packets = []
        def sendall(self, data): self.packets.append(data)
        def close(self): pass
    with tempfile.TemporaryDirectory() as directory:
        ap = WIRE['RawOpenAP'](Path(directory) / 'wire', wpa2=True)
        ap.finished.set()
        ap.thread.join(2)
        ap.connection = Channel()
        try:
            yield ap
        finally:
            ap.close()


def protected_data(header, payload, pn=3, key=bytes(range(16))):
    header = header[:1] + bytes((header[1] & ~0x40,)) + header[2:]
    return WIRE['CCMPPairwisePeer'](key).encrypt(header, payload, pn)


def install_test_session(ap, station, key=bytes(range(16))):
    ap.associated.add(station)
    ap.handshakes[station] = {'state': 'M4', 'message4_mic_verified': True,
        'message2_mic_verified': True, 'ptk': bytes(32) + key + bytes(16),
        'ccmp': WIRE['CCMPPairwisePeer'](key), 'tx_pn': 0, 'attempts': 1}


class CCMPPeerTests(unittest.TestCase):
    def test_provider_passes_fixed_rfc3610_ciphertext_and_tag(self):
        self.assertTrue(WIRE['crypto_preflight']()['rfc3610_vector_verified'])

    def test_six_independent_c_vectors_match_both_wire_directions_and_qos(self):
        for vector in CCMP_GOLDENS:
            with self.subTest(header=vector['header']):
                key, header, plain = (bytes.fromhex(vector[name]) for name in ('key', 'header', 'plain'))
                expected = bytes.fromhex(vector['mpdu_without_fcs'])
                parameters = WIRE['ccmp_parameters'](header, bytes.fromhex(vector['extiv']))
                self.assertEqual(parameters['aad'].hex(), vector['aad'])
                self.assertEqual(parameters['nonce'].hex(), vector['nonce'])
                self.assertEqual(protected_data(header, plain, vector['pn'], key), expected)
                self.assertEqual(WIRE['CCMPPairwisePeer'](key).authenticate(expected)['payload'], plain)

    def test_primary_hostap_masks_only_mutable_flags_and_sequence_number(self):
        frame = bytearray.fromhex(CCMP_GOLDENS[0]['mpdu_without_fcs'])
        frame[1] ^= 0x38  # Retry, Power Management, More Data.
        frame[22] ^= 0xf0
        frame[23] ^= 0xff
        frame[2:4] = bytes.fromhex('aa55')  # Duration is outside CCMP AAD.
        got = WIRE['CCMPPairwisePeer'](bytes(range(16))).authenticate(bytes(frame))
        self.assertEqual(got['payload'].hex(), CCMP_GOLDENS[0]['plain'])
        self.assertEqual((got['priority'], got['replay_tid']), (0, 16))

    def test_bad_mic_ciphertext_or_authenticated_header_never_advances_pn(self):
        original = bytes.fromhex(CCMP_GOLDENS[4]['mpdu_without_fcs'])
        for offset in (4, 10, 16, 24, 34, len(original) - 1):
            peer = WIRE['CCMPPairwisePeer'](bytes(range(16)))
            corrupt = original[:offset] + bytes((original[offset] ^ 1,)) + original[offset + 1:]
            with self.subTest(offset=offset), self.assertRaises(WIRE['WireError']):
                peer.authenticate(corrupt)
            self.assertEqual(peer.received_pn, {})
            self.assertEqual(peer.authenticate(original)['pn'], 3)

    def test_extiv_reserved_bits_zero_pn_and_nonpairwise_key_id_are_refused(self):
        original = bytes.fromhex(CCMP_GOLDENS[0]['mpdu_without_fcs'])
        for position, value in ((26, 1), (27, 0), (27, 0x21), (27, 0x60), (24, 0)):
            peer = WIRE['CCMPPairwisePeer'](bytes(range(16)))
            corrupt = original[:position] + bytes((value,)) + original[position + 1:]
            with self.subTest(position=position, value=value), self.assertRaises(WIRE['WireError']):
                peer.authenticate(corrupt)
            self.assertEqual(peer.received_pn, {})

    def test_unsupported_mac_profiles_fail_without_replay_or_crypto_side_effects(self):
        base = bytes.fromhex(CCMP_GOLDENS[0]['header'])
        qos = bytes.fromhex(CCMP_GOLDENS[4]['header'])
        headers = [bytes((base[0] | 1,)) + base[1:], bytes((0,)) + base[1:],
            base[:1] + bytes((0x40,)) + base[2:], base[:1] + bytes((0x43,)) + base[2:],
            bytes((0x48,)) + base[1:], base[:1] + bytes((base[1] | 0x80,)) + base[2:],
            base[:1] + bytes((base[1] | 4,)) + base[2:], base[:22] + bytes((1, 0)),
            qos[:24] + bytes((qos[24] | 0x80, 0)), base[:-1], base + bytes(1)]
        for header in headers:
            with self.subTest(header=header.hex()), self.assertRaises(WIRE['WireError']):
                WIRE['ccmp_header'](header, protected=True)
        for length in (40, 41):
            short = qos + bytes(length - len(qos))
            with self.subTest(length=length), self.assertRaisesRegex(WIRE['WireError'], 'complete ExtIV and MIC'):
                WIRE['CCMPPairwisePeer'](bytes(range(16))).authenticate(short)

    def test_packet_number_is_nonzero_typed_uint48_and_never_wraps(self):
        header = bytes.fromhex(CCMP_GOLDENS[0]['header'])
        for value in (0, -1, 1 << 48, True, 1.0, '1'):
            with self.subTest(value=value), self.assertRaises(WIRE['WireError']):
                protected_data(header, b'payload', value)
        for value in (1, (1 << 48) - 1):
            wire = protected_data(header, b'payload', value)
            self.assertEqual(WIRE['CCMPPairwisePeer'](bytes(range(16))).authenticate(wire)['pn'], value)

    def test_replayed_and_decreasing_pn_refused_after_authentication(self):
        header = bytes.fromhex(CCMP_GOLDENS[0]['header'])
        peer = WIRE['CCMPPairwisePeer'](bytes(range(16)))
        peer.authenticate(protected_data(header, b'payload', 4))
        state = dict(peer.received_pn)
        for pn in (4, 3, 1):
            with self.subTest(pn=pn), self.assertRaisesRegex(WIRE['WireError'], 'replayed'):
                peer.authenticate(protected_data(header, b'payload', pn))
            self.assertEqual(peer.received_pn, state)

    def test_replay_domains_separate_nonqos_qos_tid_transmitter_and_key(self):
        peer = WIRE['CCMPPairwisePeer'](bytes(range(16)))
        for number in (0, 2, 4):
            peer.authenticate(bytes.fromhex(CCMP_GOLDENS[number]['mpdu_without_fcs']))
        self.assertEqual({domain[2] for domain in peer.received_pn}, {16, 0, 7})
        header = bytearray.fromhex(CCMP_GOLDENS[0]['header'])
        header[15] ^= 1
        peer.authenticate(protected_data(bytes(header), b'payload'))
        self.assertEqual(len(peer.received_pn), 4)
        another = WIRE['CCMPPairwisePeer'](bytes(reversed(range(16))))
        another.authenticate(protected_data(bytes(header), b'payload', key=bytes(reversed(range(16)))))
        self.assertEqual(len(another.received_pn), 1)

    def test_duplicate_authenticated_m4_preserves_existing_pn_and_key_state(self):
        station = bytes.fromhex('025833454401')
        with stopped_ap() as ap:
            install_test_session(ap, station)
            session = ap.handshakes[station]
            session['tx_pn'] = 99
            session['ccmp'].received_pn[(station, session['ptk'][32:48], 16)] = 48
            context = session['ccmp']
            packet = WIRE['eapol_key'](0x030a, 2, bytes(32), kck=session['ptk'][:16])
            ap.handshake(station, packet)
            self.assertIs(session['ccmp'], context)
            self.assertEqual(session['tx_pn'], 99)
            self.assertEqual(list(context.received_pn.values()), [48])

    def test_secure_ap_refuses_plain_network_data_after_m4(self):
        station = bytes.fromhex('025833454401')
        with stopped_ap() as ap:
            install_test_session(ap, station)
            header = struct.pack('<HH6s6s6sH', 0x0108, 0, WIRE['AP_MAC'], station, bytes((255,)) * 6, 0)
            with self.assertRaisesRegex(WIRE['WireError'], 'unprotected'):
                ap.receive(header + WIRE['LLC'] + bytes.fromhex('0800') + bytes(28))
            self.assertEqual(ap.connection.packets, [])
            self.assertFalse(any(ap.secure_counters.values()))

    def test_authenticated_dhcp_only_emits_encrypted_offer_and_ack_with_independent_checksums(self):
        from cryptography.hazmat.primitives.ciphers.aead import AESCCM
        station = bytes.fromhex('025833454401')
        with stopped_ap() as ap:
            install_test_session(ap, station)
            header = struct.pack('<HH6s6s6sH', 0x0108, 0, WIRE['AP_MAC'], station, bytes((255,)) * 6, 0)
            fixed = bytearray(236)
            fixed[:3] = b'\x01\x01\x06'
            fixed[4:8] = bytes.fromhex('deadbeef')
            fixed[28:34] = station
            for pn, kind in ((3, 1), (4, 3)):
                options = bytes((53, 1, kind))
                if kind == 3:
                    options += bytes((50, 4)) + WIRE['CLIENT_IP'] + bytes((54, 4)) + WIRE['AP_IP']
                packet = WIRE['udp_packet'](bytes(fixed) + WIRE['DHCP_COOKIE'] + options + b'\xff',
                    68, 67, bytes(4), bytes((255,)) * 4, 1)
                ap.receive(protected_data(header, WIRE['LLC'] + bytes.fromhex('0800') + packet, pn))
                response = ap.connection.packets[-1][8:]
                self.assertEqual(response[:2], bytes.fromhex('0842'))
                self.assertEqual(int.from_bytes(response[24:26] + response[28:32], 'little'), pn - 2)
                # Independent AAD/nonce assembly and AESCCM, never the peer decoder.
                aad = response[:2] + response[4:22] + bytes(2)
                nonce = bytes(1) + response[10:16] + bytes(5) + bytes((pn - 2,))
                plain = AESCCM(bytes(range(16)), tag_length=8).decrypt(nonce, response[32:], aad)
                ip, udp = plain[8:28], plain[28:]
                self.assertEqual(independent_sum(ip), 0)
                self.assertEqual(independent_sum(ip[12:20] + b'\0\x11' + struct.pack('!H', len(udp)) + udp), 0)
                bootp = udp[8:]
                self.assertEqual(bootp[4:8], fixed[4:8])
                self.assertEqual(bootp[16:20], WIRE['CLIENT_IP'])
                self.assertEqual(bootp[240:243], bytes((53, 1, 2 if kind == 1 else 5)))
            self.assertEqual(ap.secure_counters['authenticated_dhcp_discover'], 1)
            self.assertEqual(ap.secure_counters['authenticated_dhcp_request'], 1)
            self.assertEqual(ap.secure_counters['encrypted_dhcp_offer'], 1)
            self.assertEqual(ap.secure_counters['encrypted_dhcp_ack'], 1)

    def test_premature_protected_frames_plain_dma_placeholder_and_wrong_receiver_never_get_response(self):
        station = bytes.fromhex('025833454401')
        header = struct.pack('<HH6s6s6sH', 0x0108, 0, WIRE['AP_MAC'], station, bytes((255,)) * 6, 0)
        body = WIRE['LLC'] + bytes.fromhex('0800') + bytes(28)
        with stopped_ap() as ap:
            ap.associated.add(station)
            with self.assertRaisesRegex(WIRE['WireError'], 'message4'):
                ap.receive(protected_data(header, body))
            install_test_session(ap, station)
            clear_header = header[:1] + bytes((header[1] | 0x40,)) + header[2:]
            with self.assertRaisesRegex(WIRE['WireError'], 'MIC'):
                ap.receive(clear_header + WIRE['ccmp_extiv'](3) + body + bytes(8))
            wrong = bytearray(header)
            wrong[9] ^= 1
            with self.assertRaisesRegex(WIRE['WireError'], 'addressing'):
                ap.receive(protected_data(bytes(wrong), body))
            self.assertEqual(ap.connection.packets, [])
            self.assertFalse(any(ap.secure_counters.values()))


class SecureLifecycleObserverTests(unittest.TestCase):
    def test_visible_list_guard_accepts_new_exact_wire_identity_and_source_scan_without_glyph_ocr(self):
        station = bytes.fromhex('025833454401')
        with stopped_ap() as ap:
            ap.hidden = False
            probe = struct.pack('<HH6s6s6sH', 0x0040, 0, bytes((255,)) * 6, station, bytes((255,)) * 6, 0)
            ap.receive(probe + bytes((0, 0)))
            serial = 'older log\n[2] WIFI: WiFi scan usable networks=1 hidden=0 duplicates=0\n'
            observe = WIRE['visible_scan_identity']
            identity = observe(ap.records, serial, 0, len('older log\n'), station)
            self.assertTrue(identity['verified'])
            self.assertEqual(identity['new_actual_factory_mac_probes'], 1)
            self.assertEqual(identity['new_exact_visible_advertisements'], 1)
            # Stable list headings are verified from original panel pixels in
            # the real cohort; the row's X3EMU/XGEMU OCR spelling is irrelevant.
            self.assertFalse(observe(ap.records, serial, len(ap.records), 0, station)['verified'])
            self.assertFalse(observe(ap.records, serial, 0, len(serial), station)['verified'])
            self.assertFalse(observe(ap.records, serial.replace('networks=1', 'networks=2'), 0, 0, station)['verified'])
            self.assertFalse(observe(ap.records, serial, 0, 0, bytes.fromhex('025833454402'))['verified'])

    def test_visible_list_guard_refuses_changed_wire_ssid_bssid_rsn_and_channel_despite_metadata(self):
        station = bytes.fromhex('025833454401')
        with stopped_ap() as ap:
            ap.hidden = False
            probe = struct.pack('<HH6s6s6sH', 0x0040, 0, bytes((255,)) * 6, station, bytes((255,)) * 6, 0)
            ap.receive(probe + bytes((0, 0)))
            records = ap.records
            response = base64.b64decode(records[-1]['raw_base64'])
            for offset in (10, 16, 38, response.index(WIRE['RSN_IE']) + 7,
                           response.index(bytes((3, 1, 1)), 36) + 2):
                changed = response[:offset] + bytes((response[offset] ^ 1,)) + response[offset + 1:]
                altered = records[:-1] + [{**records[-1], 'raw_base64': base64.b64encode(changed).decode()}]
                with self.subTest(offset=offset), self.assertRaises(WIRE['WireError']):
                    WIRE['visible_scan_identity'](altered,
                        'WiFi scan usable networks=1 hidden=0 duplicates=0', 0, 0, station)

    def test_native_scoped_capabilities_require_typed_complete_positive_traffic_and_zero_errors(self):
        flags, traffic, errors = (WIRE[name] for name in
            ('CCMP_SCOPE_FLAGS', 'CCMP_TRAFFIC_COUNTERS', 'CCMP_ERROR_COUNTERS'))
        valid = {**flags, **dict.fromkeys(traffic, 3), **dict.fromkeys(errors, 0)}
        self.assertTrue(WIRE['assess_native_ccmp'](valid, require_traffic=True))
        idle = {**valid, **dict.fromkeys(traffic, 0)}
        self.assertTrue(WIRE['assess_native_ccmp'](idle, require_traffic=False))
        invalid = [(name, None) for name in valid]
        invalid += [(name, int(expected)) for name, expected in flags.items()]
        invalid += [(name, not expected) for name, expected in flags.items()]
        invalid += [(name, value) for name in traffic for value in (0, -1, True, '1')]
        invalid += [(name, value) for name in errors for value in (1, -1, False, '0')]
        for name, value in invalid:
            altered = dict(valid)
            if value is None:
                del altered[name]
            else:
                altered[name] = value
            with self.subTest(name=name, value=value), self.assertRaises(WIRE['WireError']):
                WIRE['assess_native_ccmp'](altered, require_traffic=True)

    def test_native_ccmp_observer_uses_only_readonly_qom_get_and_refuses_unavailable_group(self):
        values = {**WIRE['CCMP_SCOPE_FLAGS'], **dict.fromkeys(WIRE['CCMP_TRAFFIC_COUNTERS'], 1),
                  **dict.fromkeys(WIRE['CCMP_ERROR_COUNTERS'], 0)}
        class QMP:
            def __init__(self, missing=None): self.commands, self.missing = [], missing
            def execute(self, method, arguments):
                self.commands.append((method, arguments))
                if arguments['property'] == self.missing:
                    raise RuntimeError('Property not found')
                return values[arguments['property']]
        qmp = QMP()
        self.assertEqual(WIRE['observe_native_ccmp'](qmp, require_traffic=True), values)
        self.assertEqual(len(qmp.commands), len(values))
        self.assertTrue(all(method == 'qom-get' and arguments['path'] == '/machine/wifi'
                            for method, arguments in qmp.commands))
        with self.assertRaisesRegex(WIRE['WireError'], 'read-only scoped CCMP'):
            WIRE['observe_native_ccmp'](QMP(missing='ccmp-station-rx-scope-modelled'))

    def test_new_connection_requires_new_attempt_correct_flags_ip_callback_and_completion(self):
        old = '[1] WIFI: Connecting to ssid=X3EMU auto=0 saved=0 encrypted=1 passProvided=1\n' \
              '[2] WIFI: STA event: got IP 192.168.44.2\n' \
              '[3] WIFI: Connected to ssid=X3EMU ip=192.168.44.2\n'
        fresh = '[10] WIFI: Connecting to ssid=X3EMU auto=1 saved=1 encrypted=1 passProvided=1\n' \
                '[11] WIFI: STA event: got IP 192.168.44.2\n' \
                '[12] WIFI: Connected to ssid=X3EMU ip=192.168.44.2\n'
        observe = WIRE['fresh_secure_connection']
        expected = (1, 1, 1, 1)
        self.assertFalse(observe(old, len(old), expected)['verified'])
        self.assertTrue(observe(old + fresh, len(old), expected)['verified'])
        for invalid in (fresh.replace('saved=1', 'saved=0'), fresh.replace('192.168.44.2', '10.0.2.15'),
                        fresh.replace('[11] WIFI: STA event: got IP 192.168.44.2\n', ''),
                        fresh.splitlines(True)[0] + fresh.splitlines(True)[2] + fresh.splitlines(True)[1]):
            with self.subTest(invalid=invalid):
                self.assertFalse(observe(old + invalid, len(old), expected)['verified'])

    def test_credential_observer_refuses_nonvalidated_oversize_wrong_identity_and_checksum(self):
        # Private actual guest store is never manufactured by this observer.
        # These host-only negative payloads exercise refusal before comparison.
        mac = bytes.fromhex('025833454401')
        encode = lambda value: base64.b64encode(bytes(byte ^ mac[i % 6] for i, byte in enumerate(value))).decode()
        for encoded in ('not-base64!', '', encode(b'legacy'), encode(b'CPV1' + bytes(4) + b'public-test'),
                        encode(b'CPV1' + bytes(4) + bytes(65))):
            with self.subTest(encoded_length=len(encoded)), self.assertRaises(WIRE['WireError']):
                WIRE['decode_validated_credential'](encoded, mac)
        with self.assertRaises(WIRE['WireError']):
            WIRE['decode_validated_credential']('', b'wrong')


def independent_sum(data):
    data += bytes(len(data) & 1)
    total = sum(int.from_bytes(data[i:i + 2], 'big') for i in range(0, len(data), 2))
    total = (total & 65535) + (total >> 16)
    total = (total & 65535) + (total >> 16)
    return (~total) & 65535


class RawAPWireTests(unittest.TestCase):
    def test_scan_policy_source_mismatch_prevents_guest_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in WIRE['ARDUINO_HASHES']:
                (root / name).write_bytes(b'bool show_hidden = true;')
            with self.assertRaisesRegex(WIRE['WireError'], 'Arduino 3.3.7 scan source mismatch'):
                WIRE['verified_scan_source'](root)

    def test_internet_checksum_matches_independent_rfc1071_example(self):
        self.assertEqual(WIRE['checksum'](bytes.fromhex('0001f203f4f5f6f7')), 0x220d)
        self.assertEqual(WIRE['checksum'](bytes.fromhex('45000073000040004011b861c0a80001c0a800c7')), 0)

    def test_udp_fixture_packet_has_independently_valid_lengths_and_checksums(self):
        packet = WIRE['udp_packet'](b'original odd payload!', 67, 68, bytes((192, 168, 44, 1)), bytes((255,)) * 4, 12)
        self.assertEqual(independent_sum(packet[:20]), 0)
        self.assertEqual(int.from_bytes(packet[2:4], 'big'), len(packet))
        udp = packet[20:]
        pseudo = packet[12:20] + bytes((0, 17)) + struct.pack('!H', len(udp))
        self.assertEqual(independent_sum(pseudo + udp), 0)
        corrupt = packet[:-1] + bytes((packet[-1] ^ 1,))
        with self.assertRaisesRegex(WIRE['WireError'], 'UDP checksum'):
            WIRE['parse_udp'](corrupt)

    def test_truncated_information_elements_and_duplicate_dhcp_options_are_rejected(self):
        with self.assertRaisesRegex(WIRE['WireError'], 'information element'):
            WIRE['elements'](bytes((0, 5)) + b'X3')
        with self.assertRaisesRegex(WIRE['WireError'], 'duplicate DHCP'):
            WIRE['dhcp_options'](bytes((53, 1, 1, 53, 1, 3, 255)))

    def test_hidden_beacons_reveal_ssid_only_for_a_genuine_matching_directed_probe(self):
        class Channel:
            def __init__(self): self.packets = []
            def sendall(self, data): self.packets.append(data)
            def close(self): pass
        with tempfile.TemporaryDirectory() as directory:
            ap = WIRE['RawOpenAP'](Path(directory) / 'wire')
            ap.finished.set()
            ap.thread.join(2)
            channel = Channel()
            ap.connection = channel
            try:
                ap.advertisement(bytes((255,)) * 6)
                beacon = channel.packets[-1][8:]
                self.assertEqual(beacon[:2], bytes.fromhex('8000'))
                self.assertEqual(beacon[36:38], bytes((0, 0)))
                sta = bytes.fromhex('025833454401')
                header = struct.pack('<HH6s6s6sH', 0x40, 0, bytes((255,)) * 6, sta, bytes((255,)) * 6, 0)
                before = len(channel.packets)
                self.assertFalse(ap.beacons_enabled)
                ap.receive(header + bytes((0, 0)))
                self.assertEqual(len(channel.packets), before)
                self.assertTrue(ap.beacons_enabled)
                self.assertEqual(ap.beacon_trigger_record, 2)
                ap.receive(header + bytes((0, 5)) + b'X3EMU')
                self.assertEqual(len(channel.packets), before + 1)
                response = channel.packets[-1][8:]
                self.assertEqual(response[:2], bytes.fromhex('5000'))
                self.assertEqual(response[4:10], sta)
                self.assertEqual(response[36:43], bytes((0, 5)) + b'X3EMU')
                magic, channel_number, length = struct.unpack('<4sHH', channel.packets[-1][:8])
                self.assertEqual((magic, channel_number, length), (b'X3W1', 1, len(response)))
            finally:
                ap.close()


if __name__ == '__main__':
    unittest.main()
