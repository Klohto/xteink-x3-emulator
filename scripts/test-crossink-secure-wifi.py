#!/usr/bin/env python3
"""Bounded raw 802.11 AP experiments with unchanged stock CrossInk.

The peer is an external management/DHCP endpoint. WPA2/CCMP success requires
an authenticated guest handshake and encrypted network packets on the wire.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
from pathlib import Path
import re
import runpy
import select
import signal
import socket
import struct
import subprocess
import sys
import threading
import time

PROJECT = Path(__file__).resolve().parent.parent
SSID = b'X3EMU'
AP_MAC = bytes.fromhex('025833455401')
AP_IP = bytes((192, 168, 44, 1))
CLIENT_IP = bytes((192, 168, 44, 2))
BROADCAST = b'\xff' * 6
LLC = b'\xaa\xaa\x03\x00\x00\x00'
DHCP_COOKIE = bytes.fromhex('63825363')
RATES = bytes.fromhex('010882848b960c121824')
SYNTHETIC_PSK = b'11111111'
RSN_IE = bytes.fromhex('30140100000fac040100000fac040100000fac020000')
ARDUINO_HASHES = {
    'WiFiScan.h': 'c9add8e31a1bd2cb8ed53251431b70995ea29fb63a61bf00b0fe5a461de7c6b5',
    'WiFiScan.cpp': 'ea1edfc20aeacd22806413a274484f3caa4c28106f67192f21c5c5b1766b1212',
}
ARDUINO_SOURCE_URL = 'https://github.com/espressif/arduino-esp32/blob/3.3.7/libraries/WiFi/src/'
SECURE_SOURCE_HASHES = {
    'src/WifiCredentialStore.h': 'a4b34ffb58c2c3b6b68e2ff47c0a069341a71475f52f2bc226b4ad47d6955f85',
    'lib/Serialization/ObfuscationUtils.cpp': 'ead7ae73e4e0f43f5c278e37ab7265a88175aee127bf0777c03c790b4cd6ea4e',
    'lib/Serialization/ObfuscationUtils.h': '88d2d46e0f103fdf9b921f3e724f8d72fefa9d9736318c3e6b2e6860f2282f29',
}
CCMP_REFERENCE_HASHES = {
    'hostap_ccmp.c': 'c8f67c3ed3c0270c614b6268d56000acb0c2a6a6c79b5ca9572064a04f5405c8',
    'rfc3610.txt': 'd6c857b1dea18d8259ee0ccc5468859990200e8580d0ad55dc75963d0c46588d',
}
CCMP_SCOPE_FLAGS = {
    'ccmp-ordinary-tx-scope-modelled': True,
    'ccmp-station-rx-scope-modelled': True,
    'ccmp-hardware-replay-modelled': False,
    'encryption-modelled': False,
    'timing-calibrated': False,
}
CCMP_TRAFFIC_COUNTERS = ('tx-ccmp-encrypted-frames', 'rx-ccmp-decrypted-frames')
CCMP_ERROR_COUNTERS = ('tx-ccmp-rejected-frames', 'rx-ccmp-rejected-frames', 'rx-ccmp-auth-failed-frames',
    'bad-dma', 'peer-dropped-frames', 'peer-malformed-inputs', 'peer-channel-drops',
    'peer-unmodelled-frames', 'peer-link-errors', 'tx-fcs-unverified-frames',
    'tx-length-errors', 'tx-buffer-prefix-errors')


class WireError(RuntimeError):
    pass


def sha(data):
    return hashlib.sha256(data).hexdigest()


def verified_scan_source(root):
    captured = {name: (root / name).read_bytes() for name in ARDUINO_HASHES}
    for name, data in captured.items():
        if sha(data) != ARDUINO_HASHES[name]:
            raise WireError('Arduino 3.3.7 scan source mismatch: ' + name)
    return captured


def wpa2_ptk(pmk, first_mac, second_mac, first_nonce, second_nonce):
    if len(pmk) != 32 or len(first_mac) != 6 or len(second_mac) != 6 \
            or len(first_nonce) != 32 or len(second_nonce) != 32:
        raise WireError('invalid WPA2 pairwise expansion inputs')
    context = b''.join(sorted((first_mac, second_mac))) + b''.join(sorted((first_nonce, second_nonce)))
    return b''.join(hmac.new(pmk, b'Pairwise key expansion\0' + context + bytes((i,)), 'sha1').digest()
                    for i in range(4))[:64]


def eapol_key(info, replay, nonce, *, data=b'', kck=None):
    if len(nonce) != 32:
        raise WireError('invalid EAPOL-Key nonce length')
    body = struct.pack('!BHHQ', 2, info, 16, replay) + nonce + bytes(16 + 8 + 8 + 16) \
           + struct.pack('!H', len(data)) + data
    packet = struct.pack('!BBH', 2, 3, len(body)) + body
    if kck is not None:
        packet = packet[:81] + hmac.new(kck, packet, 'sha1').digest()[:16] + packet[97:]
    return packet


def parse_eapol_key(packet):
    if len(packet) < 99 or packet[0] not in (1, 2) or packet[1] != 3 \
            or int.from_bytes(packet[2:4], 'big') != len(packet) - 4 or packet[4] != 2:
        raise WireError('invalid RSN EAPOL-Key header/length/descriptor')
    if int.from_bytes(packet[97:99], 'big') != len(packet) - 99:
        raise WireError('invalid EAPOL-Key data length')
    return {'info': int.from_bytes(packet[5:7], 'big'), 'key_length': int.from_bytes(packet[7:9], 'big'),
            'replay': int.from_bytes(packet[9:17], 'big'), 'nonce': packet[17:49],
            'mic': packet[81:97], 'data': packet[99:], 'unsigned': packet[:81] + bytes(16) + packet[97:]}


def verify_rsn(data):
    rsn = [value for tag, value in elements(data) if tag == 48]
    if len(rsn) != 1 or len(rsn[0]) < 20 or rsn[0][:18] != RSN_IE[2:20]:
        raise WireError('RSN negotiation differs from CCMP pairwise/group and PSK AKM')
    # This fixture does not implement PMF, FT, or alternate negotiated suites.
    capabilities = int.from_bytes(rsn[0][18:20], 'little')
    if capabilities & ((1 << 6) | (1 << 7)):
        raise WireError('RSN negotiation requests unsupported protected management')


def crypto_preflight():
    import cryptography
    from cryptography.hazmat.primitives.keywrap import aes_key_wrap
    from cryptography.hazmat.primitives.ciphers.aead import AESCCM
    if cryptography.__version__ != '46.0.0':
        raise WireError('secure fixture requires the verified cryptography 46.0.0 provider')
    # RFC 6070 iteration4096 vector and RFC 3394 section4.1.
    derived = hashlib.pbkdf2_hmac('sha1', b'password', b'salt', 4096, 20)
    if derived.hex() != '4b007901b765489abead49d926f721d065a429c1':
        raise WireError('PBKDF2 provider fails RFC 6070 vector')
    wrapped = aes_key_wrap(bytes(range(16)), bytes.fromhex('00112233445566778899aabbccddeeff'))
    if wrapped.hex() != '1fa68b0a8112b447aef34bd8fb5a7b829d3e862371d2cfe5':
        raise WireError('AES key wrap provider fails RFC 3394 vector')
    # RFC 3610 packet vector 1, eight-byte authentication tag, nonce length 13.
    result = AESCCM(bytes.fromhex('c0c1c2c3c4c5c6c7c8c9cacbcccdcecf'), tag_length=8).encrypt(
        bytes.fromhex('00000003020100a0a1a2a3a4a5'), bytes(range(8, 31)), bytes(range(8)))
    if result.hex() != '588c979a61c663d2f066d0c2c0f989806d5f6b61dac38417e8d12cfdf926e0':
        raise WireError('AES-CCM provider fails RFC 3610 vector')
    return {'cryptography_version': cryptography.__version__, 'package_init_sha256': sha(Path(cryptography.__file__).read_bytes()),
            'rfc6070_vector_verified': True, 'rfc3394_vector_verified': True,
            'rfc3610_vector_verified': True,
            'synthetic_public_psk': SYNTHETIC_PSK.decode(), 'password_is_user_credential': False}


def ccmp_header(header, *, protected):
    """Bounded 3-address Data/QoS profile; header and MPDU never include FCS.

    AAD/nonce follow hostap 2.11 wlantest/ccmp.c ccmp_aad_nonce. The fixture
    rejects profiles it cannot exercise rather than inferring their semantics.
    """
    if not isinstance(header, bytes) or len(header) < 24:
        raise WireError('CCMP MAC header is truncated')
    control = int.from_bytes(header[:2], 'little')
    subtype, direction = (control >> 4) & 15, (control >> 8) & 3
    if control & 3 or (control >> 2) & 3 != 2 or subtype not in (0, 8) or direction not in (1, 2):
        raise WireError('CCMP requires 3-address ToDS/FromDS Data or QoS Data')
    qos = subtype == 8
    length = 26 if qos else 24
    if len(header) != length or control & (0x0400 | 0x8000) or header[22] & 15:
        raise WireError('CCMP fragmentation, HT Control or wrong header length is unsupported')
    if bool(control & 0x4000) != protected:
        raise WireError('CCMP Protected flag differs from the requested operation')
    if qos and header[24] & 0x80:
        raise WireError('CCMP A-MSDU is unsupported')
    priority = header[24] & 15 if qos else 0
    masked = (control & ~(0x0070 | 0x0800 | 0x1000 | 0x2000)) | 0x4000
    aad = struct.pack('<H', masked) + header[4:22] + bytes((header[22] & 15, 0))
    if qos:
        aad += bytes((priority, 0))
    return {'length': length, 'direction': direction, 'priority': priority,
            'replay_tid': priority if qos else 16, 'aad': aad, 'transmitter': header[10:16]}


def ccmp_extiv(pn, key_id=0):
    if type(pn) is not int or not 1 <= pn < 1 << 48 or type(key_id) is not int or not 0 <= key_id <= 3:
        raise WireError('CCMP requires a nonzero 48-bit PN and two-bit key ID')
    encoded = pn.to_bytes(6, 'little')
    return encoded[:2] + bytes((0, 0x20 | key_id << 6)) + encoded[2:]


def ccmp_parameters(header, extiv):
    description = ccmp_header(header, protected=True)
    if len(extiv) != 8 or extiv[2] or extiv[3] & 0x3f != 0x20:
        raise WireError('CCMP ExtIV reserved bits or ExtIV flag are invalid')
    pn = int.from_bytes(extiv[:2] + extiv[4:8], 'little')
    if pn == 0:
        raise WireError('CCMP PN zero is invalid')
    return {**description, 'pn': pn, 'key_id': extiv[3] >> 6,
            'nonce': bytes((description['priority'],)) + description['transmitter'] + pn.to_bytes(6, 'big')}


class CCMPPairwisePeer:
    """Independent AESCCM endpoint. Replay state advances only after MIC success.

    Domains contain transmitter, temporal key and TID. Non-QoS uses replay
    index 16 but nonce priority zero. No key or plaintext is written to logs.
    """
    def __init__(self, temporal_key):
        if not isinstance(temporal_key, bytes) or len(temporal_key) != 16:
            raise WireError('CCMP pairwise temporal key must contain 16 bytes')
        from cryptography.hazmat.primitives.ciphers.aead import AESCCM
        self.temporal_key = temporal_key
        self.aes = AESCCM(temporal_key, tag_length=8)
        self.received_pn = {}

    def encrypt(self, header, payload, pn):
        info = ccmp_header(header, protected=False)
        if not isinstance(payload, bytes) or len(payload) > 2304 - info['length'] - 16:
            raise WireError('CCMP payload exceeds the bounded raw peer MPDU length')
        header = header[:1] + bytes((header[1] | 0x40,)) + header[2:]
        extiv = ccmp_extiv(pn)
        params = ccmp_parameters(header, extiv)
        return header + extiv + self.aes.encrypt(params['nonce'], payload, params['aad'])

    def authenticate(self, frame):
        if not isinstance(frame, bytes) or not 40 <= len(frame) <= 2304:
            raise WireError('CCMP frame length is outside bounded peer limits')
        length = 26 if frame[0] & 0x80 else 24
        if len(frame) < length + 16:
            raise WireError('CCMP frame lacks its complete ExtIV and MIC')
        params = ccmp_parameters(frame[:length], frame[length:length + 8])
        if params['key_id'] != 0:
            raise WireError('Pairwise CCMP fixture requires key ID zero')
        domain = (params['transmitter'], self.temporal_key, params['replay_tid'])
        if params['pn'] <= self.received_pn.get(domain, 0):
            raise WireError('CCMP packet number is replayed or decreases')
        from cryptography.exceptions import InvalidTag
        try:
            payload = self.aes.decrypt(params['nonce'], frame[length + 8:], params['aad'])
        except InvalidTag as error:
            raise WireError('CCMP MIC authentication failed') from error
        self.received_pn[domain] = params['pn']
        return {**params, 'payload': payload, 'header': frame[:length]}


def decode_validated_credential(encoded, factory_mac):
    """Read actual guest CPV1 output using its actual eFuse factory identity."""
    if not isinstance(factory_mac, bytes) or len(factory_mac) != 6 or not isinstance(encoded, str):
        raise WireError('invalid credential observer inputs')
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as error:
        raise WireError('credential contains invalid Base64') from error
    payload = bytes(value ^ factory_mac[i % 6] for i, value in enumerate(data))
    if not 8 <= len(payload) <= 72 or payload[:4] != b'CPV1':
        raise WireError('credential is not a bounded validated CPV1 payload')
    password = payload[8:]
    digest = 2166136261
    for value in factory_mac + password:
        digest = ((digest ^ value) * 16777619) & 0xffffffff
    if digest != int.from_bytes(payload[4:8], 'little'):
        raise WireError('credential CPV1 checksum does not match actual eFuse')
    return password


def fresh_secure_connection(serial, baseline, expected):
    tail = serial[baseline:]
    attempts = list(re.finditer(r'^\[(\d+)\].*Connecting to ssid=X3EMU '
        r'auto=(\d) saved=(\d) encrypted=(\d) passProvided=(\d)[^\n]*', tail, re.M))
    observation = {'baseline_serial_offset': baseline, 'expected_flags': list(expected),
                   'new_attempt_count': len(attempts), 'verified': False}
    if not attempts:
        return observation
    attempt = attempts[-1]
    flags = tuple(int(value) for value in attempt.groups()[1:])
    following = tail[attempt.end():]
    callbacks = list(re.finditer(r'^\[\d+\].*STA event: got IP 192\.168\.44\.2\b[^\n]*', following, re.M))
    completions = list(re.finditer(r'^\[\d+\].*Connected to ssid=X3EMU ip=192\.168\.44\.2\b[^\n]*', following, re.M))
    observation.update(attempt_line=attempt.group(0), actual_flags=list(flags),
        new_got_ip_count=len(callbacks), new_completion_count=len(completions))
    if callbacks and completions:
        observation.update(got_ip_line=callbacks[-1].group(0), completion_line=completions[-1].group(0),
            verified=flags == expected and callbacks[-1].end() <= completions[-1].start())
    return observation


def assess_native_ccmp(values, *, require_traffic):
    """Require precise scoped capabilities while preserving broad limits."""
    if not isinstance(values, dict):
        raise WireError('native CCMP observations are not a property mapping')
    for name, expected in CCMP_SCOPE_FLAGS.items():
        if type(values.get(name)) is not bool or values[name] is not expected:
            raise WireError('native CCMP scope/limit flag missing, mistyped or unexpected: ' + name)
    for name in CCMP_TRAFFIC_COUNTERS + CCMP_ERROR_COUNTERS:
        if type(values.get(name)) is not int or values[name] < 0:
            raise WireError('native CCMP/error counter missing, mistyped or negative: ' + name)
        if name in CCMP_ERROR_COUNTERS and values[name] != 0:
            raise WireError('native CCMP/raw packet error counter is nonzero: ' + name)
        if require_traffic and name in CCMP_TRAFFIC_COUNTERS and values[name] == 0:
            raise WireError('native CCMP encrypted/decrypted traffic counter is zero: ' + name)
    return True


def observe_native_ccmp(qmp, *, require_traffic=False):
    """Only read native counters; no register, key, RAM, or API mutation."""
    names = tuple(CCMP_SCOPE_FLAGS) + CCMP_TRAFFIC_COUNTERS + CCMP_ERROR_COUNTERS
    try:
        values = {name: qmp.execute('qom-get', {'path': '/machine/wifi', 'property': name}) for name in names}
    except Exception as error:
        raise WireError('native backend lacks the required read-only scoped CCMP observations: ' + str(error)) from error
    assess_native_ccmp(values, require_traffic=require_traffic)
    return values


def actual_m1_nonce_hashes(raw_ap):
    """Hash the actual transmitted M1 nonce; never report temporal keys."""
    result = set()
    for record in raw_ap.get('records', []):
        if record.get('purpose') != 'wpa2-message-1':
            continue
        frame = base64.b64decode(record['raw_base64'], validate=True)
        if frame[:2] != bytes.fromhex('0802') or frame[24:32] != LLC + bytes.fromhex('888e'):
            raise WireError('actual M1 wire record lacks clear FromDS EAPOL framing')
        parsed = parse_eapol_key(frame[32:])
        if parsed['info'] != 0x008a or parsed['replay'] != 1 or parsed['nonce'] == bytes(32):
            raise WireError('actual M1 wire record has unexpected flags/replay/nonce')
        result.add(sha(parsed['nonce']))
    return result


def visible_scan_identity(records, serial, record_baseline, serial_baseline, factory_mac):
    """Verify new real scan/packet identity without OCR of the font's digit 3."""
    if not isinstance(records, list) or not isinstance(serial, str) or type(record_baseline) is not int \
            or not 0 <= record_baseline <= len(records) or type(serial_baseline) is not int \
            or not 0 <= serial_baseline <= len(serial) or not isinstance(factory_mac, bytes) or len(factory_mac) != 6:
        raise WireError('visible scan identity observer has invalid input/baseline')
    advertisements = probes = 0
    for record in records[record_baseline:]:
        if record.get('purpose') not in ('visible-beacon', 'directed-probe-response', 'genuine-guest-dma-mpdu'):
            continue
        frame = base64.b64decode(record['raw_base64'], validate=True)
        if len(frame) < 24:
            raise WireError('visible scan identity wire frame lacks a MAC header')
        control = int.from_bytes(frame[:2], 'little')
        if control & 0x4003 or (control >> 2) & 3 != 0:
            continue
        subtype = (control >> 4) & 15
        if record['direction'] == 'guest-to-ap' and subtype == 4 and frame[10:16] == factory_mac:
            values = [value for tag, value in elements(frame[24:]) if tag == 0]
            if values in ([b''], [SSID]):
                probes += 1
        elif record['direction'] == 'ap-to-guest' and subtype in (5, 8) and record['channel'] == 1:
            if len(frame) < 36 or frame[10:16] != AP_MAC or frame[16:22] != AP_MAC:
                raise WireError('visible scan advertisement BSSID/transmitter differs from actual fixture AP')
            tags = elements(frame[36:])
            if [value for tag, value in tags if tag == 0] != [SSID] \
                    or [value for tag, value in tags if tag == 48] != [RSN_IE[2:]] \
                    or [value for tag, value in tags if tag == 3] != [b'\x01']:
                raise WireError('visible scan wire SSID/RSN/channel differs from actual fixture AP')
            advertisements += 1
    source_scan = re.search(r'WiFi scan usable networks=1 hidden=0 duplicates=0\b', serial[serial_baseline:]) is not None
    return {'verified': bool(advertisements and probes and source_scan),
        'new_exact_visible_advertisements': advertisements, 'new_actual_factory_mac_probes': probes,
        'source_scan_has_exactly_one_visible_ap': source_scan, 'wire_record_baseline': record_baseline,
        'serial_baseline': serial_baseline, 'ssid_sha256': sha(SSID), 'bssid': AP_MAC.hex(':'),
        'factory_mac': factory_mac.hex(':'), 'rsn_ie_sha256': sha(RSN_IE), 'channel': 1}


def checksum(data):
    if len(data) & 1:
        data += b'\0'
    value = sum(struct.unpack('!' + 'H' * (len(data) // 2), data))
    while value >> 16:
        value = (value & 0xffff) + (value >> 16)
    return (~value) & 0xffff


def elements(data):
    result, offset = [], 0
    while offset < len(data):
        if offset + 2 > len(data):
            raise WireError('truncated 802.11 information element header')
        identifier, length = data[offset:offset + 2]
        offset += 2
        if offset + length > len(data):
            raise WireError('truncated 802.11 information element value')
        result.append((identifier, data[offset:offset + length]))
        offset += length
    return result


def dhcp_options(data):
    result, offset = {}, 0
    while offset < len(data):
        tag = data[offset]
        offset += 1
        if tag == 255:
            return result
        if tag == 0:
            continue
        if offset == len(data):
            raise WireError('truncated DHCP option length')
        length = data[offset]
        offset += 1
        if offset + length > len(data) or tag in result:
            raise WireError('truncated or duplicate DHCP option')
        result[tag] = data[offset:offset + length]
        offset += length
    raise WireError('DHCP options lack their end marker')


def parse_udp(packet):
    if len(packet) < 28 or packet[0] >> 4 != 4:
        raise WireError('invalid IPv4 datagram')
    header_length = (packet[0] & 15) * 4
    total_length = int.from_bytes(packet[2:4], 'big')
    if header_length < 20 or total_length < header_length + 8 or total_length > len(packet):
        raise WireError('invalid IPv4 lengths')
    if checksum(packet[:header_length]) != 0:
        raise WireError('invalid IPv4 checksum')
    if int.from_bytes(packet[6:8], 'big') & 0x3fff or packet[9] != 17:
        raise WireError('fragmented or non-UDP IPv4 datagram')
    udp = packet[header_length:total_length]
    source, destination, length, supplied = struct.unpack_from('!4H', udp)
    if length != len(udp):
        raise WireError('invalid UDP length')
    pseudo = packet[12:20] + b'\0\x11' + struct.pack('!H', length)
    if supplied and checksum(pseudo + udp) != 0:
        raise WireError('invalid UDP checksum')
    return source, destination, udp[8:]


def udp_packet(payload, source, destination, source_ip, destination_ip, identifier):
    length = len(payload) + 8
    udp = struct.pack('!4H', source, destination, length, 0) + payload
    pseudo = source_ip + destination_ip + b'\0\x11' + struct.pack('!H', length)
    udp = udp[:6] + struct.pack('!H', checksum(pseudo + udp) or 0xffff) + udp[8:]
    header = struct.pack('!BBHHHBBH4s4s', 0x45, 0, 20 + length, identifier & 0xffff,
                         0, 64, 17, 0, source_ip, destination_ip)
    return header[:10] + struct.pack('!H', checksum(header)) + header[12:] + udp


class RawOpenAP:
    """Host raw-MPDU endpoint: management, ARP, and DHCP; no TCP/API emulation."""
    def __init__(self, output, *, hidden=True, channel=1, wpa2=False, nonce_generation=1):
        self.output, self.hidden, self.channel = Path(output), hidden, channel
        if type(nonce_generation) is not int or not 1 <= nonce_generation < 1 << 64:
            raise WireError('authenticator nonce generation must be a nonzero uint64')
        self.nonce_generation = nonce_generation
        self.wpa2 = wpa2
        self.handshakes = {}
        self.verifications = []
        self.secure_counters = {'authenticated_guest_frames': 0, 'authenticated_dhcp_discover': 0,
                                'authenticated_dhcp_request': 0, 'encrypted_dhcp_offer': 0,
                                'encrypted_dhcp_ack': 0, 'encrypted_arp_reply': 0}
        if wpa2:
            self.crypto_provider = crypto_preflight()
        self.output.mkdir(parents=True)
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(('127.0.0.1', 0))
        self.listener.listen(1)
        self.listener.settimeout(0.2)
        self.port = self.listener.getsockname()[1]
        self.connection = None
        self.finished = threading.Event()
        self.connected = threading.Event()
        self.lock = threading.RLock()
        self.log = (self.output / 'wire.jsonl').open('w')
        self.records = []
        self.authenticated, self.associated = set(), set()
        self.offers = {}
        self.sequence = self.ip_id = 0
        self.beacons_enabled = False
        self.beacon_trigger_record = None
        self.error = None
        self.thread = threading.Thread(target=self.run, name='raw-open-ap', daemon=True)
        self.thread.start()

    def record(self, direction, frame, purpose, **fields):
        with self.lock:
            control = int.from_bytes(frame[:2], 'little')
            item = {'seq': len(self.records) + 1, 'host_monotonic_ns': time.monotonic_ns(),
                    'direction': direction, 'purpose': purpose, 'channel': self.channel,
                    'bytes': len(frame), 'sha256': sha(frame), 'raw_base64': base64.b64encode(frame).decode(),
                    'type': (control >> 2) & 3, 'subtype': (control >> 4) & 15,
                    'protected': bool(control & 0x4000), 'addr1': frame[4:10].hex(':'),
                    'addr2': frame[10:16].hex(':'), 'addr3': frame[16:22].hex(':'), **fields}
            self.records.append(item)
            self.log.write(json.dumps(item) + '\n')
            self.log.flush()

    def header(self, control, destination, source=AP_MAC):
        sequence = self.sequence
        self.sequence = (self.sequence + 1) & 4095
        return struct.pack('<HH6s6s6sH', control, 0, destination, AP_MAC, source, sequence << 4)

    def send(self, frame, purpose, **fields):
        if not 24 <= len(frame) <= 2304:
            raise WireError('fixture MPDU exceeds native peer bounds')
        self.connection.sendall(struct.pack('<4sHH', b'X3W1', self.channel, len(frame)) + frame)
        self.record('ap-to-guest', frame, purpose, **fields)

    def advertisement(self, destination, *, probe=False):
        advertised = SSID if probe or not self.hidden else b''
        # Functional timestamp/host beacon scheduling are explicitly uncalibrated.
        fixed = struct.pack('<QHH', time.monotonic_ns() // 1000, 100, 0x11 if self.wpa2 else 1)
        body = fixed + bytes((0, len(advertised))) + advertised + RATES + bytes((3, 1, self.channel))
        if self.wpa2:
            body += RSN_IE
        if not probe:
            body += bytes((5, 4, 0, 1, 0, 0))
        self.send(self.header(0x0050 if probe else 0x0080, destination) + body,
                  'directed-probe-response' if probe else 'hidden-beacon' if self.hidden else 'visible-beacon',
                  ssid_hex=advertised.hex())

    def data(self, station, ethertype, payload, purpose, **fields):
        frame = self.header(0x0208, station) + LLC + struct.pack('!H', ethertype) + payload
        if self.wpa2 and ethertype != 0x888e:
            session = self.handshakes.get(station)
            if session is None or session['state'] != 'M4' or not session.get('message4_mic_verified'):
                raise WireError('Secure network response precedes an authenticated EAPOL message4')
            next_pn = session['tx_pn'] + 1
            frame = session['ccmp'].encrypt(frame[:24], frame[24:], next_pn)
            # A transport failure may consume this PN; it must never be reused.
            session['tx_pn'] = next_pn
            fields.update(ccmp_pn=session['tx_pn'], ccmp_key_id=0, negotiated_pairwise_key=True)
        self.send(frame, purpose, **fields)
        if self.wpa2 and purpose in ('dhcp-offer', 'dhcp-ack', 'arp-reply'):
            self.secure_counters['encrypted_' + purpose.replace('-', '_')] += 1

    def start_handshake(self, station):
        generation = self.nonce_generation
        self.nonce_generation += 1
        if generation >= 1 << 64:
            raise WireError('authenticator nonce generation exhausted')
        nonce = hashlib.sha256(b'X3EMU public original authenticator nonce' + station
                               + generation.to_bytes(8, 'big')).digest()
        self.handshakes[station] = {'anonce': nonce, 'state': 'M1', 'last_tx_host_ns': 0, 'attempts': 0}
        self.offers = {key: value for key, value in self.offers.items() if key[0] != station}
        self.send_handshake(station)

    def send_handshake(self, station):
        session = self.handshakes[station]
        if session['state'] == 'M1':
            packet = eapol_key(0x008a, 1, session['anonce'])
            purpose = 'wpa2-message-1'
        elif session['state'] == 'M3':
            packet = session['m3']
            purpose = 'wpa2-message-3'
        else:
            return
        session['last_tx_host_ns'] = time.monotonic_ns()
        session['attempts'] += 1
        self.data(station, 0x888e, packet, purpose, attempt=session['attempts'])

    def handshake(self, station, packet):
        from cryptography.hazmat.primitives.keywrap import aes_key_wrap
        session = self.handshakes.get(station)
        if session is None:
            raise WireError('EAPOL response lacks authentic preceding association/M1')
        key = parse_eapol_key(packet)
        if key['info'] == 0x010a:
            if key['replay'] != 1 or key['nonce'] == bytes(32) or session['state'] not in ('M1', 'M3'):
                raise WireError('EAPOL message2 has invalid replay/nonce/state')
            verify_rsn(key['data'])
            pmk = hashlib.pbkdf2_hmac('sha1', SYNTHETIC_PSK, SSID, 4096, 32)
            ptk = wpa2_ptk(pmk, AP_MAC, station, session['anonce'], key['nonce'])
            expected = hmac.new(ptk[:16], key['unsigned'], 'sha1').digest()[:16]
            if not hmac.compare_digest(expected, key['mic']):
                raise WireError('EAPOL message2 MIC does not authenticate actual synthetic PSK/PTK')
            if session['state'] == 'M3' and key['nonce'] != session['snonce']:
                raise WireError('Repeated EAPOL message2 changes negotiated SNonce')
            gtk = hashlib.sha256(b'X3EMU public original group key').digest()[:16]
            kde = bytes.fromhex('dd16000fac010100') + gtk
            key_data = RSN_IE + kde
            if len(key_data) & 7:
                key_data += b'\xdd' + bytes(7 - (len(key_data) & 7))
            encrypted = aes_key_wrap(ptk[16:32], key_data)
            session.update(state='M3', ptk=ptk, snonce=key['nonce'], gtk=gtk, message2_mic_verified=True,
                           m3=eapol_key(0x13ca, 2, session['anonce'], data=encrypted, kck=ptk[:16]))
            self.verifications.append({'purpose': 'wpa2-message-2-authenticated',
                                       'genuine_guest_eapol_sha256': sha(packet), 'ptk_sha256': sha(ptk), 'mic_verified': True})
            self.send_handshake(station)
        elif key['info'] == 0x030a:
            if session['state'] not in ('M3', 'M4') or key['replay'] != 2 or key['data'] or key['nonce'] != bytes(32):
                raise WireError('EAPOL message4 has invalid replay/nonce/data/state')
            expected = hmac.new(session['ptk'][:16], key['unsigned'], 'sha1').digest()[:16]
            if not hmac.compare_digest(expected, key['mic']):
                raise WireError('EAPOL message4 MIC does not authenticate negotiated KCK')
            # A repeated M4 must not reinstall a key or reset either PN domain.
            if session['state'] == 'M3':
                session.update(state='M4', message4_mic_verified=True,
                               ccmp=CCMPPairwisePeer(session['ptk'][32:48]), tx_pn=0)
            self.verifications.append({'purpose': 'wpa2-message-4-authenticated',
                                       'genuine_guest_eapol_sha256': sha(packet), 'mic_verified': True})
        else:
            raise WireError('Unexpected EAPOL key flags in bounded WPA2 authenticator')

    def dhcp(self, station, packet, *, authenticated=False):
        source, destination, request = parse_udp(packet)
        if (source, destination) != (68, 67):
            return
        if len(request) < 240 or request[:3] != b'\x01\x01\x06' or request[236:240] != DHCP_COOKIE:
            raise WireError('invalid DHCP client BOOTP header')
        if request[28:34] != station:
            raise WireError('DHCP chaddr differs from genuine transmitting station')
        options = dhcp_options(request[240:])
        kind = options.get(53)
        transaction = request[4:8]
        if kind == b'\x01':
            self.offers[(station, transaction)] = CLIENT_IP
            reply_type = 2
        elif kind == b'\x03':
            if self.offers.get((station, transaction)) != options.get(50) or options.get(54) != AP_IP:
                raise WireError('DHCP REQUEST does not select the actual preceding offer')
            reply_type = 5
        else:
            return
        if self.wpa2:
            if not authenticated:
                raise WireError('Secure DHCP request lacks CCMP authentication')
            self.secure_counters['authenticated_dhcp_discover' if reply_type == 2
                                 else 'authenticated_dhcp_request'] += 1
        fixed = struct.pack('!BBBBIHH4s4s4s4s16s64s128s', 2, 1, 6, 0,
                            int.from_bytes(transaction, 'big'), 0, int.from_bytes(request[10:12], 'big'),
                            bytes(4), CLIENT_IP, AP_IP, bytes(4), station + bytes(10), bytes(64), bytes(128))
        response = fixed + DHCP_COOKIE + bytes((53, 1, reply_type, 54, 4)) + AP_IP \
                   + bytes((51, 4)) + struct.pack('!I', 3600) + bytes((1, 4, 255, 255, 255, 0, 3, 4)) \
                   + AP_IP + bytes((6, 4)) + AP_IP + b'\xff'
        response += bytes(max(0, 300 - len(response)))
        self.ip_id += 1
        datagram = udp_packet(response, 67, 68, AP_IP, b'\xff' * 4, self.ip_id)
        self.data(station, 0x0800, datagram, 'dhcp-offer' if reply_type == 2 else 'dhcp-ack',
                  transaction_hex=transaction.hex(), client_ip='192.168.44.2',
                  client_request_sha256=sha(request))

    def receive(self, frame):
        if len(frame) < 24:
            raise WireError('guest MPDU lacks its MAC header')
        control = int.from_bytes(frame[:2], 'little')
        category, subtype = (control >> 2) & 3, (control >> 4) & 15
        station = frame[10:16]
        self.record('guest-to-ap', frame, 'genuine-guest-dma-mpdu')
        if control & 3:
            raise WireError('fixture received an unknown-version MPDU')
        protected = bool(control & 0x4000)
        if protected and (not self.wpa2 or category != 2):
            raise WireError('fixture received unsupported Protected MPDU')
        if category == 0:
            if subtype == 4:
                requested = next((value for tag, value in elements(frame[24:]) if tag == 0), None)
                if requested == b'' and not self.beacons_enabled:
                    self.beacons_enabled = True
                    self.beacon_trigger_record = len(self.records)
                if requested == SSID or (requested == b'' and not self.hidden):
                    self.advertisement(station, probe=True)
            elif subtype == 11:
                if frame[4:10] != AP_MAC or len(frame) < 30 or struct.unpack_from('<3H', frame, 24) != (0, 1, 0):
                    raise WireError('unsupported authentication request')
                self.authenticated.add(station)
                self.send(self.header(0x00b0, station) + struct.pack('<3H', 0, 2, 0), 'open-auth-response')
            elif subtype in (0, 2):
                offset = 28 if subtype == 0 else 34
                requested = next((value for tag, value in elements(frame[offset:]) if tag == 0), None)
                if station not in self.authenticated or frame[4:10] != AP_MAC or requested != SSID:
                    raise WireError('association lacks matching SSID or preceding open authentication')
                self.associated.add(station)
                if self.wpa2:
                    verify_rsn(frame[offset:])
                self.send(self.header(0x0010 if subtype == 0 else 0x0030, station)
                          + struct.pack('<3H', 0x11 if self.wpa2 else 1, 0, 0xc001) + RATES, 'association-response')
                if self.wpa2:
                    self.start_handshake(station)
            elif subtype in (10, 12):
                self.associated.discard(station)
                self.handshakes.pop(station, None)
            return
        if category != 2 or subtype in (4, 12):
            return
        if station not in self.associated or control & 0x0300 != 0x0100 or frame[4:10] != AP_MAC:
            raise WireError('data frame lacks association or station-to-DS addressing')
        offset = 26 if subtype & 8 else 24
        if protected:
            session = self.handshakes.get(station)
            if session is None or session['state'] != 'M4' or not session.get('message4_mic_verified'):
                raise WireError('Protected network data precedes an authenticated EAPOL message4')
            authenticated = session['ccmp'].authenticate(frame)
            body = authenticated['payload']
            self.secure_counters['authenticated_guest_frames'] += 1
            self.verifications.append({'purpose': 'ccmp-guest-data-authenticated',
                'genuine_guest_mpdu_sha256': sha(frame), 'plaintext_sha256': sha(body),
                'pn': authenticated['pn'], 'replay_tid': authenticated['replay_tid'],
                'nonce_priority': authenticated['priority'], 'key_id': authenticated['key_id'], 'mic_verified': True})
        else:
            if control & 0x8000:
                raise WireError('fixture does not implement HT Control data')
            body = frame[offset:]
        if len(body) < 8 or body[:6] != LLC:
            raise WireError('guest data frame lacks LLC/SNAP')
        ethertype = int.from_bytes(body[6:8], 'big')
        payload = body[8:]
        if ethertype == 0x888e and self.wpa2:
            if protected:
                raise WireError('bounded WPA2 fixture requires clear handshake EAPOL')
            if payload[:2] == b'\x01\x01' or payload[:2] == b'\x02\x01':
                return  # Actual EAPOL-Start; M1 already started after association.
            self.handshake(station, payload)
        elif self.wpa2 and not protected:
            raise WireError('Secure fixture refuses unprotected post-association network data')
        elif ethertype == 0x0806:
            if len(payload) < 28 or payload[:8] != bytes.fromhex('0001080006040001'):
                return
            if payload[24:28] == AP_IP:
                reply = bytes.fromhex('0001080006040002') + AP_MAC + AP_IP + payload[8:18]
                self.data(station, ethertype, reply, 'arp-reply')
        elif ethertype == 0x0800:
            if len(payload) >= 20 and payload[9] == 17:
                self.dhcp(station, payload, authenticated=protected)

    def run(self):
        buffer = bytearray()
        try:
            while not self.finished.is_set() and self.connection is None:
                try:
                    self.connection, _ = self.listener.accept()
                    self.connection.settimeout(2)
                    self.connected.set()
                except socket.timeout:
                    pass
            next_beacon = 0
            while not self.finished.is_set() and self.connection:
                for station, session in list(self.handshakes.items()):
                    if session['state'] in ('M1', 'M3') and time.monotonic_ns() >= session['last_tx_host_ns'] + 2_000_000_000:
                        if session['attempts'] >= 20:
                            raise WireError('Bounded authenticator exhausted authentic EAPOL retransmissions')
                        self.send_handshake(station)
                if self.beacons_enabled and time.monotonic() >= next_beacon:
                    self.advertisement(BROADCAST)
                    next_beacon = time.monotonic() + 0.1
                ready, _, _ = select.select([self.connection], [], [], 0.02)
                if not ready:
                    continue
                data = self.connection.recv(65536)
                if not data:
                    break
                buffer.extend(data)
                while len(buffer) >= 8:
                    magic, channel, length = struct.unpack_from('<4sHH', buffer)
                    if magic != b'X3W1' or channel != self.channel or not 24 <= length <= 2304:
                        raise WireError('invalid native raw peer framing/channel/length')
                    if len(buffer) < 8 + length:
                        break
                    frame = bytes(buffer[8:8 + length])
                    del buffer[:8 + length]
                    self.receive(frame)
            if buffer:
                raise WireError('raw peer stream closed with a partial packet')
        except Exception as error:
            if not self.finished.is_set():
                self.error = str(error) or type(error).__name__

    def snapshot(self):
        with self.lock:
            return {'records': list(self.records), 'error': self.error, 'hidden': self.hidden,
                    'beacon_schedule': 'Start after first genuine wildcard probe; 100ms host-time interval; uncalibrated',
                    'beacon_trigger_record': self.beacon_trigger_record, 'physical_rf_modelled': False,
                    'wpa2_authenticator_fixture': self.wpa2,
                    'crypto_provider': self.crypto_provider if self.wpa2 else None,
                    'ccmp_peer_profile': '3-address Data/QoS; pairwise key ID 0; 48-bit nonzero PN; per transmitter/key/TID replay',
                    'secure_counters': dict(self.secure_counters),
                    'eapol_verifications': list(self.verifications),
                    'handshakes': [{'station': mac.hex(':'), 'state': session['state'], 'attempts': session['attempts'],
                                    'message2_mic_verified': session.get('message2_mic_verified', False),
                                    'message4_mic_verified': session.get('message4_mic_verified', False),
                                    'ptk_sha256': sha(session['ptk']) if 'ptk' in session else None}
                                   for mac, session in self.handshakes.items()],
                    'wpa_modelled': False, 'ccmp_modelled': False,
                    'native_ccmp_verified': False, 'speed_selection_allowed': False}

    def close(self):
        self.finished.set()
        self.thread.join(3)
        if self.connection:
            self.connection.close()
        self.listener.close()
        self.log.close()
        if self.thread.is_alive():
            raise WireError('raw AP thread did not stop')


def frozen_helpers(output):
    root = output / 'host-code'
    paths = list((PROJECT / 'x3emu').glob('*.py')) + [PROJECT / 'scripts' / name for name in
             ('test-crossink-network.py', 'smoke-crossink.py', 'test-crossink-wifi-config.py', 'test-crossink-secure-wifi.py')]
    paths += list((PROJECT / 'boards').glob('*.toml'))
    captured = {path.relative_to(PROJECT): path.read_bytes() for path in paths}
    for relative, data in captured.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    helpers = runpy.run_path(str(root / 'scripts/test-crossink-wifi-config.py'))
    return root, helpers, [{'path': path.as_posix(), 'sha256': sha(data), 'bytes': len(data)} for path, data in captured.items()]


def run_hidden(args):
    from PIL import Image  # Fail before starting a guest if OCR dependencies are absent.
    del Image
    output = args.output.resolve()
    output.mkdir(parents=True)
    (output / 'frames').mkdir()
    host_root, helpers, dependencies = frozen_helpers(output)
    network, smoke = helpers['NETWORK'], helpers['SMOKE']
    source = helpers['verify_source_bytes'](args.source, helpers['SOURCE_HASHES'])
    sdk = helpers['verify_source_bytes'](args.sdk_source, helpers['SDK_HASHES'])
    arduino = verified_scan_source(args.arduino_source)
    if helpers['file_sha256'](args.flash) != helpers['FULL_FLASH_SHA256']:
        raise WireError('official full flash hash mismatch')
    card = output / 'fixture-card.img'
    book = helpers['make_test_epub']()
    helpers['create_fat16_card'](card, {'/test.epub': book})
    ap = RawOpenAP(output / 'wire', hidden=not args.visible)
    command = [sys.executable, '-m', 'x3emu', 'run', '--flash', str(args.flash.resolve()), '--sd', str(card),
               '--backend', str(args.backend.resolve()), '--rom-dir', str(args.rom_dir.resolve()),
               '--output', str(output / 'run'), '--wifi-peer', f'connect:{ap.port}', '--wifi-channel', '1',
               '--wifi-random-seed', '1', '--icount', '--icount-shift', '3', '--seconds', str(args.host_limit)]
    report = {'schema_version': 1, 'workflow': 'raw-hidden-open' if not args.visible else 'raw-visible-open',
              'host_dependency_files': dependencies, 'firmware_source_commit': helpers['SOURCE_COMMIT'],
              'source_files': helpers['snapshot_bytes'](output, 'pinned-source/crossink', source),
              'sdk_source_files': helpers['snapshot_bytes'](output, 'pinned-source/sdk', sdk),
              'arduino_scan_source': [{'path': name, 'sha256': sha(data), 'url': ARDUINO_SOURCE_URL + name}
                                     for name, data in arduino.items()],
              'launcher_command': command, 'checks': {}, 'frames': {}, 'functional_pass': False, 'strict_pass': False,
              'limits': {'wpa_verified': False, 'ccmp_verified': False, 'encrypted_association_verified': False,
                         'physical_rf_modelled': False, 'timing_calibrated': False}, 'speed_selection_allowed': False}
    helpers['snapshot_bytes'](output, 'pinned-source/arduino-esp32-3.3.7', arduino)
    process = qmp = replay = experiment = None
    try:
        with (output / 'launcher.log').open('wb') as log:
            process = subprocess.Popen(command, cwd=host_root, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        experiment = smoke['Experiment'](output, process, args.step_timeout, button_hold_ms=400)
        experiment.wait('native launcher QMP broker', lambda: (output / 'run/run.json').is_file()
                        and json.loads((output / 'run/run.json').read_text())['status'] == 'running')
        qmp = helpers['QMPClient'](output / 'run/qmp.sock')
        replay = network['Replay'](smoke, experiment, qmp, report, output)
        qmp.set_buttons(0)
        experiment.wait('actual ROM X3 cold startup', lambda: 'Hardware detect: X3' in experiment.log_text('serial.log'))
        replay.check('raw_peer_only_no_builtin_air', qmp.execute('qom-get', {'path': '/machine/wifi', 'property': 'peer-only'}) is True
                     and qmp.execute('qom-get', {'path': '/machine/wifi', 'property': 'air-enabled'}) is False)
        helpers['launch_join'](replay)
        helpers['wait_scan'](replay, 'actual-raw-scan')
        if not args.visible:
            serial = experiment.log_text('serial.log')
            replay.check('actual_scan_has_no_listed_ap_with_show_hidden_default_false',
                         re.search(r'WiFi scan usable networks=0 hidden=0', serial) is not None,
                         {'scan_lines': [line for line in serial.splitlines() if 'WiFi scan' in line],
                          'source_call': 'WiFi.scanNetworks(true): async=true, Arduino 3.3.7 show_hidden=false'})
            replay.capture_text('hidden-entry-selected', ('Hidden', 'Network'))
            replay.tap('confirm', 'manual-ssid-entry', 'Select sole Add Hidden Network row')
            helpers['enter_ssid'](replay)
        else:
            experiment.press(qmp, 'confirm', purpose='Select raw visible open AP')
        def connected():
            if ap.error:
                raise WireError(ap.error)
            return 'Connected to ssid=X3EMU ip=192.168.44.2' in experiment.log_text('serial.log')
        experiment.wait('authentic raw peer DHCP completion', connected)
        replay.capture('hidden-connected')
        records = ap.snapshot()['records']
        for purpose in ('directed-probe-response', 'open-auth-response', 'association-response', 'dhcp-offer', 'dhcp-ack'):
            replay.check('actual_wire_' + purpose.replace('-', '_'), any(item['purpose'] == purpose for item in records))
        replay.check('beacons_omit_ssid_on_wire', all(item['ssid_hex'] == '' for item in records if item['purpose'] == 'hidden-beacon')
                     and any(item['purpose'] == 'hidden-beacon' for item in records) if not args.visible else True)
        replay.check('actual_source_dhcp_log', connected())
        replay.check('original_epub_unchanged', replay.read('/test.epub') == book)
        report['completed'] = True
    except Exception as error:
        report['error'] = str(error) or type(error).__name__
        if qmp:
            try:
                qmp.execute('stop')
                report['failure_state'] = qmp.state()
                report['failure_registers'] = qmp.execute('human-monitor-command', {'command-line': 'info registers'})
            except Exception as snapshot_error:
                report['snapshot_error'] = str(snapshot_error)
    finally:
        if qmp:
            qmp.close()
        if process and process.poll() is None:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)
                report['shutdown_error'] = 'required termination'
        ap.close()
        report['raw_ap'] = ap.snapshot()
        report['checks']['raw_ap_no_protocol_error'] = ap.error is None
        if experiment:
            rom, serial = experiment.log_text('rom.log'), experiment.log_text('serial.log')
            report['checks']['rom_poweron_and_source_network_reboot'] = 'SPI_FAST_FLASH_BOOT' in rom \
                and re.findall(r'Reset diagnostic: reset=\d+\((\w+)\)', serial) == ['POWERON', 'SW']
            report['checks']['no_sd_error_or_guest_panic'] = smoke['FATAL_LOG'].search(rom + '\n' + serial) is None
            report['button_steps'] = experiment.steps
        path = output / 'run/run.json'
        if path.is_file():
            run = json.loads(path.read_text())
            report['run_manifest'] = run
            report['checks']['backend_stopped_cleanly'] = run['status'] == 'stopped' and run.get('exit_code') == 0
            report['stopped_panel_trace'] = helpers['stopped_trace_assessment'](output / 'run', run)
            report['model_diagnostics_clean'] = run.get('validity', {}).get('diagnostics_clean', False)
            report['panel_trace_complete'] = bool(report['frames']) and all(frame.get('trace_complete') for frame in report['frames'].values()) \
                                            and report['stopped_panel_trace']['complete']
        report['functional_pass'] = bool(report.get('completed') and not report.get('error') and not report.get('shutdown_error')
                                         and report['checks'] and all(report['checks'].values()))
        report['strict_pass'] = bool(report['functional_pass'] and report.get('model_diagnostics_clean') and report.get('panel_trace_complete'))
        network['write_json'](output / 'validation.json', report)
    return report


def type_physical_text(replay, helpers, text, label, *, masked=False):
    """Only source keyboard GPIO actions; no text injection or SD input store."""
    position = (0, 0)
    rows = helpers['ROWS']
    for index, char in enumerate(text):
        target = next((row, value.index(char.lower())) for row, value in enumerate(rows[:4])
                      if char.lower() in value)
        for step, button in enumerate(helpers['keyboard_path'](position, target)):
            replay.tap(button, f'{label}-{index}-move-{step}', 'Navigate source English keyboard')
        before = replay.experiment.refresh_count(replay.qmp)
        replay.experiment.press(replay.qmp, 'confirm', hold_ms=1200 if char.isupper() else 400,
            purpose='Type public fixture password character' if masked else 'Type source keyboard character ' + char)
        replay.capture(f'{label}-character-{index}', before)
        position = target
    for step, button in enumerate(helpers['keyboard_path'](position, (4, 3))):
        replay.tap(button, f'{label}-submit-{step}', 'Navigate source keyboard OK')
    replay.experiment.press(replay.qmp, 'confirm', purpose='Submit actual keyboard ' + label)


def secure_store_observation(replay, output):
    store = replay.read('/.crosspoint/wifi.json')
    efuse = (output / 'run/efuse.bin').read_bytes()
    if len(efuse) != 336:
        raise WireError('actual launcher eFuse block image has wrong length')
    factory_mac = efuse[24:30][::-1]
    value = json.loads(store)
    credentials = value.get('credentials')
    if not isinstance(credentials, list) or len(credentials) != 1 or credentials[0].get('ssid') != SSID.decode() \
            or value.get('lastConnectedSsid') != SSID.decode() or 'password' in credentials[0]:
        raise WireError('guest store does not contain exactly the expected obfuscated credential')
    password = decode_validated_credential(credentials[0].get('password_obf'), factory_mac)
    if password != SYNTHETIC_PSK:
        raise WireError('guest-written credential does not match physically typed public fixture password')
    return store, {'store_sha256': sha(store), 'store_bytes': len(store), 'efuse_sha256': sha(efuse),
        'factory_mac': factory_mac.hex(':'), 'decoded_password_sha256': sha(password),
        'validated_cpv1_checksum': True, 'plaintext_password_in_receipt': False}


def run_secure_phase(args, output, host_root, helpers, dependencies, *, name, flash, card,
                     book, efuse=None, expected_store=None):
    output.mkdir()
    (output / 'frames').mkdir()
    network, smoke = helpers['NETWORK'], helpers['SMOKE']
    ap = RawOpenAP(output / 'wire', wpa2=True, hidden=True,
                   nonce_generation=1 if name == 'physical-password-save' else 1001)
    command = [sys.executable, '-m', 'x3emu', 'run', '--flash', str(flash.resolve()), '--sd', str(card.resolve()),
        '--backend', str(args.backend.resolve()), '--rom-dir', str(args.rom_dir.resolve()),
        '--output', str(output / 'run'), '--wifi-peer', f'connect:{ap.port}', '--wifi-channel', '1',
        '--wifi-random-seed', '1', '--icount', '--icount-shift', '3', '--seconds', str(args.host_limit)]
    if efuse is not None:
        command += ['--efuse', str(efuse.resolve())]
    report = {'schema_version': 1, 'workflow': name, 'launcher_command': command,
        'host_dependency_files': dependencies, 'checks': {'backend_stopped_cleanly': False,
            'panel_trace_complete': False, 'native_ccmp_final_positive_and_zero_errors': False,
            'native_ccmp_readonly_observations_before_shutdown': False},
        'frames': {}, 'functional_pass': False,
        'strict_pass': False, 'secure_wifi_pass': False, 'all_crossink_functions_verified': False,
        'speed_selection_allowed': False, 'guest_memory_or_api_modified': False,
        'input': {'flash_sha256': helpers['file_sha256'](flash), 'sd_sha256': helpers['file_sha256'](card),
                  'efuse_sha256': helpers['file_sha256'](efuse) if efuse is not None else None},
        'limits': {'physical_rf_modelled': False, 'timing_calibrated': False,
                   'native_key_selection_assumed': False, 'external_runtime_dependencies_frozen': False}}
    process = qmp = replay = experiment = None
    try:
        for entry in dependencies:
            data = (host_root / entry['path']).read_bytes()
            if len(data) != entry['bytes'] or sha(data) != entry['sha256']:
                raise WireError('frozen host dependency changed before launch: ' + entry['path'])
        with (output / 'launcher.log').open('wb') as log:
            process = subprocess.Popen(command, cwd=host_root, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        experiment = smoke['Experiment'](output, process, args.step_timeout, button_hold_ms=400)
        experiment.wait('native launcher QMP broker', lambda: (output / 'run/run.json').is_file()
            and json.loads((output / 'run/run.json').read_text())['status'] == 'running')
        qmp = helpers['QMPClient'](output / 'run/qmp.sock')
        replay = network['Replay'](smoke, experiment, qmp, report, output)
        qmp.set_buttons(0)
        experiment.wait('actual fresh CPU ROM/X3 boot', lambda: 'Hardware detect: X3' in experiment.log_text('serial.log'))
        replay.check('raw_peer_only_no_builtin_air',
            qmp.execute('qom-get', {'path': '/machine/wifi', 'property': 'peer-only'}) is True
            and qmp.execute('qom-get', {'path': '/machine/wifi', 'property': 'air-enabled'}) is False)
        report['native_ccmp_initial'] = observe_native_ccmp(qmp)
        replay.check('native_ccmp_scope_limits_and_initial_zero_errors', True)
        replay.check('fresh_cpu_starts_without_inherited_ccmp_traffic', all(
            report['native_ccmp_initial'][name] == 0 for name in CCMP_TRAFFIC_COUNTERS))
        if expected_store is None:
            replay.check('initial_no_guest_wifi_store', replay.absent('/.crosspoint/wifi.json'))
        else:
            replay.check('fresh_cpu_uses_exact_guest_written_credential', replay.read('/.crosspoint/wifi.json') == expected_store,
                         {'expected_store_sha256': sha(expected_store)})
        baseline = len(experiment.log_text('serial.log'))
        helpers['launch_join'](replay)
        if expected_store is None:
            helpers['wait_scan'](replay, 'actual-hidden-encrypted-scan')
            replay.check('actual_scan_omits_hidden_ap', re.search(r'WiFi scan usable networks=0 hidden=0',
                         experiment.log_text('serial.log')) is not None)
            replay.capture_text('hidden-entry-selected', ('Hidden', 'Network'))
            replay.tap('confirm', 'ssid-entry', 'Open actual Add Hidden Network keyboard')
            type_physical_text(replay, helpers, SSID.decode(), 'ssid')
            replay.capture_text('password-entry', ('password',))
            type_physical_text(replay, helpers, SYNTHETIC_PSK.decode(), 'password', masked=True)
        expected_flags = (0, 0, 1, 1) if expected_store is None else (1, 1, 1, 1)
        def connected():
            if ap.error:
                raise WireError(ap.error)
            return fresh_secure_connection(experiment.log_text('serial.log'), baseline, expected_flags)['verified']
        experiment.wait('new genuine WPA2 handshake and encrypted DHCP completion', connected)
        replay.check('actual_new_secure_connection', connected(),
                     fresh_secure_connection(experiment.log_text('serial.log'), baseline, expected_flags))
        report['native_ccmp_connected'] = observe_native_ccmp(qmp, require_traffic=True)
        replay.check('actual_native_encryption_decryption_positive_and_zero_errors', True)
        if expected_store is None:
            replay.capture_text('save-password-prompt', ('Save', 'password'))
            replay.tap('confirm', 'password-saved', 'Accept source Save Password default Yes')
            def persisted():
                try:
                    return secure_store_observation(replay, output)
                except (FileNotFoundError, WireError, ValueError):
                    return False
            store, observation = experiment.wait('actual CPV1 guest-written password store', persisted)
            (output / 'guest-written-wifi.json').write_bytes(store)
            replay.check('physical_save_writes_actual_validated_password', True, observation)
        else:
            store, observation = secure_store_observation(replay, output)
            replay.check('saved_reconnect_preserves_exact_credential', store == expected_store, observation)
        replay.capture_text('secure-network-serving', ('192.168.44.2',))
        replay.check('original_epub_unchanged', replay.read('/test.epub') == book)
        if expected_store is not None:
            # A later environmental phase exposes the same authenticated AP to
            # scan. Only beacon/probe SSID advertisement changes; no guest state.
            visible_wire_baseline = len(ap.snapshot()['records'])
            ap.hidden = False
            report['visible_advertisement_phase'] = {'same_ssid': SSID.decode(), 'same_bssid': AP_MAC.hex(':'),
                'same_channel': 1, 'same_rsn_ie_hex': RSN_IE.hex(),
                'guest_memory_or_api_modified': False, 'association_responses_withheld': False}
            before = len(experiment.log_text('serial.log'))
            experiment.press(qmp, 'back', purpose='Exit actual running web server through stock silent reboot')
            experiment.wait('source return reboot', lambda: 'Hardware detect: X3' in experiment.log_text('serial.log')[before:])
            replay.capture('fresh-home-after-network')
            before = len(experiment.log_text('serial.log'))
            helpers['launch_join'](replay)
            experiment.wait('new saved auto-connect begins', lambda:
                'Connecting to ssid=X3EMU auto=1 saved=1' in experiment.log_text('serial.log')[before:])
            experiment.press(qmp, 'confirm', purpose='Source Confirm interrupts saved auto-connect to expose scan list')
            helpers['wait_scan'](replay, 'saved-visible-network-list')
            replay.capture_text('actual-visible-saved-row', ('WiFi', 'Networks', 'Forget'))
            identity = visible_scan_identity(ap.snapshot()['records'], experiment.log_text('serial.log'),
                visible_wire_baseline, before, (output / 'run/efuse.bin').read_bytes()[24:30][::-1])
            replay.check('new_visible_scan_exact_wire_and_source_identity', identity['verified'], identity)
            before_cancel = replay.read('/.crosspoint/wifi.json')
            replay.tap('left', 'forget-cancel-prompt', 'Saved row Left opens genuine Forget prompt')
            replay.capture_text('forget-cancel-panel', ('Forget', 'Cancel'))
            replay.tap('confirm', 'forget-cancelled', 'Accept source default Cancel')
            helpers['wait_scan'](replay, 'after-forget-cancel')
            after_cancel = replay.read('/.crosspoint/wifi.json')
            replay.check('forget_cancel_preserves_exact_credential', after_cancel == before_cancel,
                {'before_sha256': sha(before_cancel), 'after_sha256': sha(after_cancel)})
            replay.tap('left', 'forget-confirm-prompt', 'Reopen genuine Forget prompt')
            replay.tap('down', 'forget-selected', 'Select actual Forget instead of Cancel')
            experiment.press(qmp, 'confirm', purpose='Commit source saved-password removal')
            helpers['wait_scan'](replay, 'after-forget-confirm')
            deleted = replay.read('/.crosspoint/wifi.json')
            (output / 'guest-forgotten-wifi.json').write_bytes(deleted)
            parsed = json.loads(deleted)
            replay.check('forget_removes_secure_credential', parsed.get('credentials') == []
                         and parsed.get('lastConnectedSsid') == '', {'store_sha256': sha(deleted)})
            replay.tap('confirm', 'forgotten-network-selected', 'Select actual encrypted AP after forgetting')
            replay.capture_text('forgotten-network-password-required', ('password',))
            replay.tap('back', 'password-entry-cancelled', 'Cancel genuine password keyboard after Forget')
            replay.check('password_cancel_does_not_restore_credential', replay.read('/.crosspoint/wifi.json') == deleted)
        report['completed'] = True
    except Exception as error:
        report['error'] = str(error) or type(error).__name__
        if qmp:
            try:
                qmp.execute('stop')
                report['failure_state'] = qmp.state()
                report['failure_registers'] = qmp.execute('human-monitor-command', {'command-line': 'info registers'})
            except Exception as failure:
                report['snapshot_error'] = str(failure)
    finally:
        if qmp:
            try:
                report['native_ccmp_before_shutdown'] = observe_native_ccmp(qmp, require_traffic=True)
                report['checks']['native_ccmp_readonly_observations_before_shutdown'] = True
            except Exception as error:
                report['native_ccmp_before_shutdown_error'] = str(error)
            qmp.close()
        if process and process.poll() is None:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)
                report['shutdown_error'] = 'required termination'
        ap.close()
        raw = report['raw_ap'] = ap.snapshot()
        report['checks']['raw_ap_no_protocol_error'] = raw['error'] is None
        report['checks']['actual_m2_m4_mic_verified'] = any(
            session['state'] == 'M4' and session['message2_mic_verified'] and session['message4_mic_verified']
            for session in raw['handshakes']) or all(any(v['purpose'] == purpose and v['mic_verified']
            for v in raw['eapol_verifications']) for purpose in ('wpa2-message-2-authenticated', 'wpa2-message-4-authenticated'))
        for counter in ('authenticated_dhcp_discover', 'authenticated_dhcp_request', 'encrypted_dhcp_offer', 'encrypted_dhcp_ack'):
            report['checks']['actual_' + counter] = raw['secure_counters'][counter] > 0
        if experiment:
            rom, serial = experiment.log_text('rom.log'), experiment.log_text('serial.log')
            report['checks']['no_sd_error_or_guest_panic'] = smoke['FATAL_LOG'].search(rom + '\n' + serial) is None
            resets = re.findall(r'Reset diagnostic: reset=\d+\((\w+)\)', serial)
            report['reset_reasons'] = resets
            report['checks']['only_actual_poweron_and_source_software_resets'] = bool(
                resets and resets[0] == 'POWERON' and len(resets) >= 2 and all(value == 'SW' for value in resets[1:]))
            report['button_steps'] = experiment.steps
        path = output / 'run/run.json'
        if path.is_file():
            run = report['run_manifest'] = json.loads(path.read_text())
            report['checks']['backend_stopped_cleanly'] = run['status'] == 'stopped' and run.get('exit_code') == 0
            report['stopped_panel_trace'] = helpers['stopped_trace_assessment'](output / 'run', run)
            report['model_diagnostics_clean'] = run.get('validity', {}).get('diagnostics_clean', False)
            report['panel_trace_complete'] = bool(report['frames']) and all(
                frame.get('trace_complete') for frame in report['frames'].values()) and report['stopped_panel_trace']['complete']
            report['checks']['panel_trace_complete'] = report['panel_trace_complete']
            try:
                final_wifi = run.get('final_state', {}).get('wifi', {})
                assess_native_ccmp(final_wifi, require_traffic=True)
                report['native_ccmp_final'] = {name: final_wifi[name] for name in
                    tuple(CCMP_SCOPE_FLAGS) + CCMP_TRAFFIC_COUNTERS + CCMP_ERROR_COUNTERS}
                report['checks']['native_ccmp_final_positive_and_zero_errors'] = True
            except WireError as error:
                report['native_ccmp_final_error'] = str(error)
        report['functional_pass'] = bool(report.get('completed') and not report.get('error')
            and not report.get('shutdown_error') and report['checks'] and all(report['checks'].values()))
        report['secure_wifi_pass'] = report['functional_pass']
        report['strict_pass'] = bool(report['functional_pass'] and report.get('model_diagnostics_clean')
                                    and report.get('panel_trace_complete'))
        network['write_json'](output / 'validation.json', report)
    return report


def run_secure_lifecycle(args):
    from PIL import Image
    del Image
    if args.ccmp_reference_source is None:
        raise WireError('secure lifecycle requires exact primary CCMP reference sources')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    host_root, helpers, dependencies = frozen_helpers(output)
    hashes = {**helpers['SOURCE_HASHES'], **SECURE_SOURCE_HASHES}
    source = helpers['verify_source_bytes'](args.source, hashes)
    sdk = helpers['verify_source_bytes'](args.sdk_source, helpers['SDK_HASHES'])
    arduino = verified_scan_source(args.arduino_source)
    references = helpers['verify_source_bytes'](args.ccmp_reference_source, CCMP_REFERENCE_HASHES)
    if helpers['file_sha256'](args.flash) != helpers['FULL_FLASH_SHA256']:
        raise WireError('official full flash hash mismatch')
    provider = crypto_preflight()
    book = helpers['make_test_epub']()
    card = output / 'initial-fixture-card.img'
    helpers['create_fat16_card'](card, {'/test.epub': book})
    report = {'schema_version': 1, 'workflow': 'raw-wpa2-ccmp-lifecycle', 'host_dependency_files': dependencies,
        'firmware_source_commit': helpers['SOURCE_COMMIT'], 'sdk_source_commit': helpers['SDK_COMMIT'],
        'source_files': helpers['snapshot_bytes'](output, 'pinned-source/crossink', source),
        'sdk_source_files': helpers['snapshot_bytes'](output, 'pinned-source/sdk', sdk),
        'arduino_scan_source': helpers['snapshot_bytes'](output, 'pinned-source/arduino-esp32-3.3.7', arduino),
        'ccmp_primary_sources': helpers['snapshot_bytes'](output, 'pinned-source/ccmp', references),
        'primary_source_urls': {'hostap_ccmp.c':
            'https://git.w1.fi/cgit/hostap/plain/wlantest/ccmp.c?id=d945ddd368085f255e68328f2d3b020ceea359af',
            'rfc3610.txt': 'https://www.rfc-editor.org/rfc/rfc3610.txt'},
        'crypto_provider': provider, 'phases': {}, 'checks': {}, 'functional_pass': False,
        'strict_pass': False, 'secure_wifi_pass': False, 'all_crossink_functions_verified': False,
        'speed_selection_allowed': False, 'physical_rf_modelled': False, 'timing_calibrated': False}
    first_dir = output / 'physical-password-save'
    first = run_secure_phase(args, first_dir, host_root, helpers, dependencies, name='physical-password-save',
                             flash=args.flash, card=card, book=book)
    first_path = first_dir / 'validation.json'
    report['phases']['physical-password-save'] = {'path': str(first_path.relative_to(output)),
        'sha256': sha(first_path.read_bytes()), 'functional_pass': first['functional_pass'], 'strict_pass': first['strict_pass']}
    report['checks']['physical_password_handshake_encrypted_dhcp_guest_save'] = first['functional_pass']
    if first['functional_pass']:
        store = (first_dir / 'guest-written-wifi.json').read_bytes()
        second_dir = output / 'fresh-cpu-reconnect-forget'
        second = run_secure_phase(args, second_dir, host_root, helpers, dependencies, name='fresh-cpu-reconnect-forget',
            flash=first_dir / 'run/flash.bin', card=first_dir / 'run/sd.img', efuse=first_dir / 'run/efuse.bin',
            book=book, expected_store=store)
        second_path = second_dir / 'validation.json'
        report['phases']['fresh-cpu-reconnect-forget'] = {'path': str(second_path.relative_to(output)),
            'sha256': sha(second_path.read_bytes()), 'functional_pass': second['functional_pass'], 'strict_pass': second['strict_pass']}
        report['checks']['fresh_cpu_actual_guest_media_reconnect_and_physical_forget'] = second['functional_pass']
        report['checks']['fresh_cpu_exact_written_flash_sd_efuse'] = all(
            second['input'][kind + '_sha256'] == sha((first_dir / 'run' / filename).read_bytes())
            for kind, filename in (('flash', 'flash.bin'), ('sd', 'sd.img'), ('efuse', 'efuse.bin')))
        first_nonces, second_nonces = (actual_m1_nonce_hashes(phase['raw_ap']) for phase in (first, second))
        first_keys, second_keys = ({value['ptk_sha256'] for value in phase['raw_ap']['eapol_verifications']
            if value['purpose'] == 'wpa2-message-2-authenticated'} for phase in (first, second))
        report['checks']['fresh_cpu_new_actual_m1_nonce_and_negotiated_key'] = bool(
            first_nonces and second_nonces and first_nonces.isdisjoint(second_nonces)
            and first_keys and second_keys and first_keys.isdisjoint(second_keys))
        report['new_handshake_observation'] = {'first_m1_nonce_sha256': sorted(first_nonces),
            'second_m1_nonce_sha256': sorted(second_nonces), 'pairwise_keys_distinct': first_keys.isdisjoint(second_keys),
            'authenticator_nonce_source': 'declared public SHA256 fixture generations 1 and 1001; physical entropy unverified',
            'temporal_keys_in_receipt': False}
    report['functional_pass'] = bool(len(report['phases']) == 2 and all(report['checks'].values()))
    report['secure_wifi_pass'] = report['functional_pass']
    report['strict_pass'] = bool(report['functional_pass'] and all(phase['strict_pass'] for phase in report['phases'].values()))
    helpers['NETWORK']['write_json'](output / 'validation.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--backend', type=Path, required=True)
    parser.add_argument('--rom-dir', type=Path, required=True)
    parser.add_argument('--flash', type=Path, default=PROJECT / 'local/firmware/crossink-v1.6.0-x3-full-flash.bin')
    parser.add_argument('--source', type=Path, default=PROJECT.parent / 'crossink-harness-src')
    parser.add_argument('--sdk-source', type=Path, default=PROJECT.parent / 'freeink-sdk')
    parser.add_argument('--arduino-source', type=Path, required=True,
                        help='directory containing exact Arduino 3.3.7 WiFiScan.h and WiFiScan.cpp')
    parser.add_argument('--step-timeout', type=float, default=120)
    parser.add_argument('--host-limit', type=float, default=900)
    parser.add_argument('--visible', action='store_true', help='separate visible/open raw AP control')
    parser.add_argument('--workflow', choices=('hidden-open', 'secure-lifecycle'), default='hidden-open')
    parser.add_argument('--ccmp-reference-source', type=Path,
                        help='directory containing exact hostap_2_11 hostap_ccmp.c and RFC 3610 rfc3610.txt')
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error('output must be new or empty')
    if args.workflow == 'secure-lifecycle' and args.visible:
        parser.error('secure lifecycle begins with hidden SSID/password; --visible is for open control only')
    result = run_secure_lifecycle(args) if args.workflow == 'secure-lifecycle' else run_hidden(args)
    print(json.dumps({key: result.get(key) for key in ('workflow', 'functional_pass', 'strict_pass', 'error')}))
    return 0 if result['functional_pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
