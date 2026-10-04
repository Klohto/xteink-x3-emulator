#!/usr/bin/env python3
"""Bounded raw 802.11 AP experiments with unchanged stock CrossInk.

The initial workflow is hidden/open. The peer is a genuine packet endpoint,
not a firmware callback or an ESP-IDF replacement. WPA/CCMP remain unverified.
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
    # RFC 6070 iteration4096 vector and RFC 3394 section4.1.
    derived = hashlib.pbkdf2_hmac('sha1', b'password', b'salt', 4096, 20)
    if derived.hex() != '4b007901b765489abead49d926f721d065a429c1':
        raise WireError('PBKDF2 provider fails RFC 6070 vector')
    wrapped = aes_key_wrap(bytes(range(16)), bytes.fromhex('00112233445566778899aabbccddeeff'))
    if wrapped.hex() != '1fa68b0a8112b447aef34bd8fb5a7b829d3e862371d2cfe5':
        raise WireError('AES key wrap provider fails RFC 3394 vector')
    return {'cryptography_version': cryptography.__version__, 'package_init_sha256': sha(Path(cryptography.__file__).read_bytes()),
            'rfc6070_vector_verified': True, 'rfc3394_vector_verified': True,
            'synthetic_public_psk': SYNTHETIC_PSK.decode(), 'password_is_user_credential': False}


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
    def __init__(self, output, *, hidden=True, channel=1, wpa2=False):
        self.output, self.hidden, self.channel = Path(output), hidden, channel
        self.wpa2 = wpa2
        self.handshakes = {}
        self.verifications = []
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
        self.send(self.header(0x0208, station) + LLC + struct.pack('!H', ethertype) + payload, purpose, **fields)

    def start_handshake(self, station):
        nonce = hashlib.sha256(b'X3EMU public original authenticator nonce' + station).digest()
        self.handshakes[station] = {'anonce': nonce, 'state': 'M1', 'last_tx_host_ns': 0, 'attempts': 0}
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
            if session['state'] != 'M3' or key['replay'] != 2 or key['data'] or key['nonce'] != bytes(32):
                raise WireError('EAPOL message4 has invalid replay/nonce/data/state')
            expected = hmac.new(session['ptk'][:16], key['unsigned'], 'sha1').digest()[:16]
            if not hmac.compare_digest(expected, key['mic']):
                raise WireError('EAPOL message4 MIC does not authenticate negotiated KCK')
            session.update(state='M4', message4_mic_verified=True)
            self.verifications.append({'purpose': 'wpa2-message-4-authenticated',
                                       'genuine_guest_eapol_sha256': sha(packet), 'mic_verified': True})
        else:
            raise WireError('Unexpected EAPOL key flags in bounded WPA2 authenticator')

    def dhcp(self, station, packet):
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
        if control & 0x4003:
            raise WireError('open fixture received Protected or unknown-version MPDU')
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
            return
        if category != 2 or subtype in (4, 12):
            return
        offset = 26 if subtype & 8 else 24
        if control & 0x8000:
            offset += 4
        if station not in self.associated or control & 0x0300 != 0x0100 or frame[4:10] != AP_MAC:
            raise WireError('data frame lacks association or station-to-DS addressing')
        if len(frame) < offset + 8 or frame[offset:offset + 6] != LLC:
            raise WireError('guest data frame lacks LLC/SNAP')
        ethertype = int.from_bytes(frame[offset + 6:offset + 8], 'big')
        payload = frame[offset + 8:]
        if ethertype == 0x888e and self.wpa2:
            if payload[:2] == b'\x01\x01' or payload[:2] == b'\x02\x01':
                return  # Actual EAPOL-Start; M1 already started after association.
            self.handshake(station, payload)
        elif self.wpa2:
            raise WireError('Secure fixture refuses unprotected post-association network data')
        elif ethertype == 0x0806:
            if len(payload) < 28 or payload[:8] != bytes.fromhex('0001080006040001'):
                return
            if payload[24:28] == AP_IP:
                reply = bytes.fromhex('0001080006040002') + AP_MAC + AP_IP + payload[8:18]
                self.data(station, ethertype, reply, 'arp-reply')
        elif ethertype == 0x0800:
            if len(payload) >= 20 and payload[9] == 17:
                self.dhcp(station, payload)

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
                    'eapol_verifications': list(self.verifications),
                    'handshakes': [{'station': mac.hex(':'), 'state': session['state'], 'attempts': session['attempts'],
                                    'message2_mic_verified': session.get('message2_mic_verified', False),
                                    'message4_mic_verified': session.get('message4_mic_verified', False),
                                    'ptk_sha256': sha(session['ptk']) if 'ptk' in session else None}
                                   for mac, session in self.handshakes.items()],
                    'wpa_modelled': False, 'ccmp_modelled': False, 'speed_selection_allowed': False}

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
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error('output must be new or empty')
    result = run_hidden(args)
    print(json.dumps({key: result.get(key) for key in ('workflow', 'functional_pass', 'strict_pass', 'error')}))
    return 0 if result['functional_pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
