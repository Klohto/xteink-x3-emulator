#!/usr/bin/env python3
"""Actual saved-network reconnect with genuine GTK1-encrypted DHCP broadcast.

The external AP varies the original DHCP reply's MAC destination and uses the
GTK carried by its actual MIC-verified, AES-unwrapped M3 KDE. No native keys,
guest memory, firmware functions, credentials or guest callbacks are written.
"""
from __future__ import annotations

import argparse
import base64
import collections
import hashlib
import hmac
import json
from pathlib import Path
import re
import runpy
import signal
import struct
import subprocess
import sys

PROJECT = Path(__file__).resolve().parent.parent
SECURE = runpy.run_path(str(PROJECT / 'scripts/test-crossink-secure-wifi.py'))
WireError = SECURE['WireError']
AP_MAC, SSID, LLC = (SECURE[name] for name in ('AP_MAC', 'SSID', 'LLC'))
BROADCAST = bytes((255,)) * 6
GROUP_FLAG = 'ccmp-station-group-rx-scope-modelled'
SAVED_PHASE_RECEIPT_SHA256 = '1b3d94404db9ff4847097ef5db83a39fa80f3a99eae94e8eb5746f0d0cf521e4'
GROUP_PRIMARY_HASHES = {
    'GROUP-CONTRACT.md': '125099eb7d64afbaf156d609b5f905b3986309a1d1d687b9d7d68f3770dbeaf7',
    'audit-receipt.json': '73553b24989f25e62224647c0453a0005b5f8feadda57beff95cf7c1f05fc351',
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def file_sha(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def required(condition, message):
    if not condition:
        raise WireError(message)


def verified_group_primary(root):
    captured = {name: (root / name).read_bytes() for name in GROUP_PRIMARY_HASHES}
    for name, data in captured.items():
        required(sha(data) == GROUP_PRIMARY_HASHES[name], 'group primary contract pin mismatch: ' + name)
    receipt = json.loads(captured['audit-receipt.json'])
    required(receipt.get('firmware_modified') is False and receipt.get('native_changes') is False
             and receipt.get('guest_execution') is False, 'primary audit is not an unchanged source audit')
    return captured


def verified_native_candidate(args):
    data=args.native_validation.read_bytes()
    required(sha(data)==args.native_validation_sha256,'group native validation receipt pin mismatch')
    receipt=json.loads(data)
    required(receipt.get('full_gate_pass') is True and type(receipt.get('full_gate_cases')) is int
             and receipt['full_gate_cases']>=121 and receipt.get('full_gate_suites')==12
             and receipt.get('skips')==0,'group native full gate is incomplete')
    required(file_sha(args.backend)==receipt['elf']['sha256'] and args.backend.stat().st_size==receipt['elf']['size_bytes'],
             'group native ELF differs from full-gate receipt')
    rom=args.rom_dir/'esp32c3-rom.bin'
    required(file_sha(rom)==receipt['official_rom']['sha256']
             =='0de1e65020e803bea0d7443dca149d61895e01fca3bb9c82d073234eebd73f99',
             'group native official ROM pin mismatch')
    gate=receipt['full_gate'];path=Path(gate['path'])
    required(file_sha(path)==gate['sha256'] and path.stat().st_size==gate['size_bytes'],
             'group native full gate artifact changed')
    summary=json.loads(path.read_text())
    required(summary['native_device_test_cases_passed']==receipt['full_gate_cases']
             and len(summary['native_device_test_suites'])==12,'group native full gate summary count mismatch')
    cases=0
    for suite in summary['native_device_test_suites']:
        tap=Path(suite['tap']);text=tap.read_text()
        required(file_sha(tap)==suite['tap_sha256'] and not re.search(r'^not ok\b|#\s*SKIP\b',text,re.M|re.I),
                 'group native TAP digest/failure/skip: '+suite['name'])
        passed=len(re.findall(r'^ok\s+\d+\b',text,re.M))
        required(passed==suite['cases_passed'] and re.search(r'^1\.\.'+str(passed)+r'\s*$',text,re.M) is not None,
                 'group native TAP plan/case count: '+suite['name'])
        cases+=passed
    required(cases==receipt['full_gate_cases'],'group native all TAP case totals mismatch')
    return {'native_validation_receipt_sha256':sha(data),'native_elf_sha256':receipt['elf']['sha256'],
        'native_patch_sha256':receipt['patch']['sha256'],'native_source_tree':receipt['source_tree'],
        'native_device_cases_passed':receipt['full_gate_cases'],'native_suites_passed':12,'native_skips':0,
        'official_rom_sha256':file_sha(rom),'full_gate_artifact_sha256':gate['sha256']}


def gtk1_from_actual_m3(packet, ptk, anonce):
    """Authenticate and unwrap the actual EAPOL M3; accept one exact GTK1 KDE."""
    from cryptography.hazmat.primitives.keywrap import aes_key_unwrap, InvalidUnwrap
    required(isinstance(ptk, bytes) and len(ptk) == 64 and isinstance(anonce, bytes)
             and len(anonce) == 32, 'invalid negotiated M3 key/nonce inputs')
    key = SECURE['parse_eapol_key'](packet)
    required(key['info'] == 0x13ca and key['replay'] == 2 and key['nonce'] == anonce,
             'actual M3 flags, replay or authenticator nonce changed')
    expected = hmac.new(ptk[:16], key['unsigned'], 'sha1').digest()[:16]
    required(hmac.compare_digest(expected, key['mic']), 'actual M3 MIC authentication failed')
    try:
        unwrapped = aes_key_unwrap(ptk[16:32], key['data'])
    except (ValueError, InvalidUnwrap) as error:
        raise WireError('actual M3 AES key unwrap failed') from error
    rsn, groups = [], []
    offset = 0
    while offset < len(unwrapped):
        # RFC3394-wrapped EAPOL key data uses dd00... padding after the KDE.
        if unwrapped[offset] == 0xdd and not any(unwrapped[offset+1:]):
            break
        required(offset + 2 <= len(unwrapped), 'M3 key data element is truncated')
        tag, length = unwrapped[offset:offset+2]
        offset += 2
        required(offset + length <= len(unwrapped), 'M3 key data element body is truncated')
        value = unwrapped[offset:offset+length]
        offset += length
        if tag == 48:
            rsn.append(value)
        elif tag == 221:
            required(value[:4] == bytes.fromhex('000fac01') and len(value) == 22,
                     'M3 KDE differs from the bounded CCMP GTK form')
            required(value[4] == 1 and value[5] == 0, 'M3 GTK is not exact KeyID1 with reserved bits clear')
            groups.append(value[6:])
        else:
            raise WireError('unexpected element in bounded M3 key data')
    required(rsn == [SECURE['RSN_IE'][2:]] and len(groups) == 1, 'M3 requires one exact RSN and one GTK1 KDE')
    return groups[0], {'key_id': 1, 'group_key_bytes': 16, 'm3_eapol_sha256': sha(packet),
                       'actual_m3_mic_verified': True, 'actual_m3_aes_unwrap_verified': True,
                       'exact_single_gtk1_kde_verified': True, 'key_bytes_in_receipt': False}


def group_header(header, *, protected, transmitter=AP_MAC, source=AP_MAC):
    parameters = SECURE['ccmp_header'](header, protected=protected)
    required(parameters['direction'] == 2 and header[4:10] == BROADCAST
             and header[10:16] == transmitter and header[16:22] == source,
             'GTK1 fixture requires exact FromDS broadcast destination and AP transmitter/BSSID')
    return parameters


class Group1Peer:
    """External GTK1 encoder/observer with authenticated per-TID PN accounting."""
    def __init__(self, gtk, *, transmitter=AP_MAC, source=AP_MAC):
        from cryptography.hazmat.primitives.ciphers.aead import AESCCM
        required(isinstance(gtk, bytes) and len(gtk) == 16, 'GTK1 requires exactly 16 bytes')
        required(all(isinstance(mac, bytes) and len(mac) == 6 and not mac[0]&1
                     for mac in (transmitter,source)), 'GTK1 fixture requires exact unicast transmitter/source identities')
        self.transmitter,self.source=transmitter,source
        self.aes = AESCCM(gtk, tag_length=8)
        self.received_pn = {}

    def encrypt(self, header, payload, pn):
        group_header(header, protected=False, transmitter=self.transmitter, source=self.source)
        required(isinstance(payload, bytes) and len(payload) <= 2304-len(header)-16,
                 'GTK1 payload exceeds bounded raw MPDU length')
        header = header[:1] + bytes((header[1] | 0x40,)) + header[2:]
        iv = SECURE['ccmp_extiv'](pn, 1)
        params = SECURE['ccmp_parameters'](header, iv)
        return header + iv + self.aes.encrypt(params['nonce'], payload, params['aad'])

    def authenticate(self, frame):
        required(isinstance(frame, bytes) and 40 <= len(frame) <= 2304,
                 'GTK1 MPDU has invalid length')
        length = 26 if (frame[0] >> 4) & 15 == 8 else 24
        required(len(frame) >= length+16, 'GTK1 MPDU lacks complete ExtIV and MIC')
        group_header(frame[:length], protected=True, transmitter=self.transmitter, source=self.source)
        params = SECURE['ccmp_parameters'](frame[:length], frame[length:length+8])
        required(params['key_id'] == 1, 'GTK1 observer refuses alternative key IDs')
        tid = params['replay_tid']
        required(params['pn'] > self.received_pn.get(tid, 0), 'GTK1 observer refuses replay/decreasing PN')
        from cryptography.exceptions import InvalidTag
        try:
            payload = self.aes.decrypt(params['nonce'], frame[length+8:], params['aad'])
        except InvalidTag as error:
            raise WireError('GTK1 MIC authentication failed') from error
        self.received_pn[tid] = params['pn']
        return {**params, 'payload': payload}


class RawGroupAP(SECURE['RawOpenAP']):
    """Same genuine AP handshake; DHCP OFFER/ACK use its transmitted M3 GTK1."""
    def __init__(self, output, *, nonce_generation=2001):
        self.group_sessions = {}
        self.group_verifications = []
        self.group_counters = {'encrypted_group_dhcp_offer': 0, 'encrypted_group_dhcp_ack': 0}
        super().__init__(output, hidden=True, wpa2=True, nonce_generation=nonce_generation)

    def start_handshake(self, station):
        self.group_sessions.pop(station, None)
        super().start_handshake(station)

    def handshake(self, station, packet):
        key = SECURE['parse_eapol_key'](packet)
        super().handshake(station, packet)
        if key['info'] == 0x010a:
            session = self.handshakes[station]
            actual = [record for record in self.records if record['purpose'] == 'wpa2-message-3']
            required(actual, 'GTK lacks its actual transmitted M3')
            frame = base64.b64decode(actual[-1]['raw_base64'], validate=True)
            required(frame[:2] == bytes.fromhex('0802') and frame[4:10] == station
                     and frame[24:32] == LLC + bytes.fromhex('888e') and frame[32:] == session['m3'],
                     'actual M3 wire bytes differ from negotiated session')
            gtk, observation = gtk1_from_actual_m3(frame[32:], session['ptk'], session['anonce'])
            previous = self.group_sessions.get(station)
            if previous is not None:
                required(hmac.compare_digest(previous['gtk'], gtk), 'repeated M2 changed GTK')
            else:
                self.group_sessions[station] = {'gtk': gtk, 'peer': Group1Peer(gtk), 'tx_pn': 0}
            self.group_verifications.append(observation)

    def data(self, station, ethertype, payload, purpose, **fields):
        if purpose not in ('dhcp-offer', 'dhcp-ack'):
            return super().data(station, ethertype, payload, purpose, **fields)
        session = self.handshakes.get(station)
        group = self.group_sessions.get(station)
        required(ethertype == 0x0800 and session is not None and session['state'] == 'M4'
                 and session.get('message4_mic_verified') is True and group is not None,
                 'group DHCP reply precedes authentic M4/actual transmitted GTK1')
        source, destination, _ = SECURE['parse_udp'](payload)
        required((source, destination) == (67, 68) and payload[16:20] == bytes((255,))*4,
                 'group DHCP response lacks UDP67/68 and IP broadcast destination')
        clear_header = self.header(0x0208, BROADCAST)
        pn = group['tx_pn']+1
        frame = group['peer'].encrypt(clear_header, LLC+struct.pack('!H',ethertype)+payload,pn)
        # Consume a PN even if the external transport fails. Never reset on M4.
        group['tx_pn'] = pn
        self.send(frame,purpose,ccmp_pn=pn,ccmp_key_id=1,negotiated_pairwise_key=False,
                  actual_transmitted_m3_gtk=True,group_destination=True,**fields)
        self.group_counters['encrypted_group_'+purpose.replace('-','_')] += 1
        self.secure_counters['encrypted_'+purpose.replace('-','_')] += 1

    def snapshot(self):
        result = super().snapshot()
        with self.lock:
            result.update(group_fixture={'key_id':1,'destination':'ff:ff:ff:ff:ff:ff',
                'gtk_source':'actual transmitted MIC-verified and AES-unwrapped M3 GTK KDE',
                'native_group_verified':False,'hardware_replay_modelled':False},
                group_verifications=list(self.group_verifications),group_counters=dict(self.group_counters))
        return result


def assess_group_native(values, *, require_traffic):
    SECURE['assess_native_ccmp'](values, require_traffic=require_traffic)
    required(type(values.get(GROUP_FLAG)) is bool and values[GROUP_FLAG] is True,
             'native bounded station group CCMP scope missing/mistyped/false')
    required(type(values.get('rx-group-policy-modelled')) is bool
             and values['rx-group-policy-modelled'] is False, 'broad group policy must remain unmodelled')
    return True


def observe_group_native(qmp, *, require_traffic=False):
    values = SECURE['observe_native_ccmp'](qmp, require_traffic=require_traffic)
    try:
        for name in (GROUP_FLAG,'rx-group-policy-modelled'):
            values[name] = qmp.execute('qom-get',{'path':'/machine/wifi','property':name})
    except Exception as error:
        raise WireError('native group observation unavailable: '+str(error)) from error
    assess_group_native(values, require_traffic=require_traffic)
    return values


def independent_wire_verification(records):
    """Separate literal AAD/nonce reconstruction and AESCCM for every raw MPDU."""
    from cryptography.hazmat.primitives.ciphers.aead import AESCCM
    sessions, counts, pns, dhcp = {}, collections.Counter(), {}, collections.Counter()
    pmk = hashlib.pbkdf2_hmac('sha1', SECURE['SYNTHETIC_PSK'], SSID,4096,32)
    for expected_seq,record in enumerate(records,1):
        frame = base64.b64decode(record['raw_base64'],validate=True)
        required(record['seq'] == expected_seq and len(frame) == record['bytes']
                 and sha(frame) == record['sha256'], 'independent wire record length/hash/sequence')
        fc = int.from_bytes(frame[:2],'little')
        required(type(record['protected']) is bool and record['protected'] is bool(fc & 0x4000),
                 'wire Protected metadata differs from actual bytes')
        if (fc >> 2)&3 != 2:
            continue
        subtype=(fc>>4)&15
        if subtype in (4,12) and not fc & 0x4000:
            required(fc&3==0 and not fc&(0x0400|0x8000) and frame[22]&15==0
                     and len(frame)==(26 if subtype==12 else 24)
                     and record['direction']=='guest-to-ap' and fc&0x0300==0x0100
                     and frame[4:10]==AP_MAC and frame[10:16] in sessions,
                     'ordinary clear power-save Null-Data header differs from bounded source traffic')
            counts['clear_null_data']+=1
            continue
        required(fc & 3 == 0 and (fc>>4)&15 in (0,8) and not fc & (0x0400|0x8000)
                 and frame[22]&15==0,'independent bounded nonfragmented Data/QoS header')
        length = 26 if (fc >> 4)&15 == 8 else 24
        station = frame[10:16] if record['direction']=='guest-to-ap' else frame[4:10]
        payload = frame[length:]
        if fc & 0x4000:
            group = bool(frame[4]&1)
            if group:
                required(record['direction']=='ap-to-guest' and frame[4:10]==BROADCAST
                         and frame[10:16]==AP_MAC and frame[16:22]==AP_MAC and fc & 0x0300==0x0200
                         and len(sessions)==1,
                         'independent group FromDS address scope')
                session = next(iter(sessions.values()))
            else:
                session = sessions[station]
            required(session.get('m4') is True, 'encrypted data before independently authenticated M4')
            iv = payload[:8]
            required(len(iv)==8,'actual wire ExtIV is truncated')
            key_id = iv[3]>>6
            required(iv[2]==0 and iv[3]&63==0x20
                     and key_id==(1 if group else 0), 'actual wire keyID/scope/reserved bits')
            priority = frame[24]&15 if length==26 else 0
            tid = priority if length==26 else 16
            pn = int.from_bytes(iv[:2]+iv[4:8],'little')
            domain = (record['direction'],frame[10:16],key_id,tid)
            required(pn > pns.get(domain,0), 'independent wire PN replay/decrease')
            aad = struct.pack('<H',(fc & 0xc78f)|0x4000)+frame[4:22]+bytes((frame[22]&15,0))
            if length==26:
                aad += bytes((priority,0))
            nonce = bytes((priority,))+frame[10:16]+pn.to_bytes(6,'big')
            temporal = session['gtk'] if group else session['ptk'][32:48]
            payload = AESCCM(temporal,tag_length=8).decrypt(nonce,payload[8:],aad)
            pns[domain] = pn
            counts['group_rx' if group else 'pairwise_tx' if record['direction']=='guest-to-ap' else 'pairwise_rx'] += 1
            required(payload[:6]==LLC, 'authenticated protected packet lacks LLC')
            if group:
                required(record['purpose'] in ('dhcp-offer','dhcp-ack') and payload[6:8]==bytes.fromhex('0800'),
                         'group fixture unexpectedly sent non-DHCP group data')
                source,destination,bootp=SECURE['parse_udp'](payload[8:])
                required((source,destination)==(67,68) and payload[24:28]==bytes((255,))*4,
                         'independent group DHCP IP/UDP destination')
                options=SECURE['dhcp_options'](bootp[240:]); message=options.get(53)
                required(message==(b'\x02' if record['purpose']=='dhcp-offer' else b'\x05'),
                         'group DHCP plaintext type differs from actual response')
                dhcp[record['purpose']] += 1
            continue
        if payload[:8] != LLC+bytes.fromhex('888e') or payload[9:10] != b'\x03':
            continue
        packet=payload[8:]; key=SECURE['parse_eapol_key'](packet); info=key['info']
        if info==0x008a:
            required(record['direction']=='ap-to-guest' and key['replay']==1, 'independent M1 direction/replay')
            session=sessions.setdefault(station,{'anonce':key['nonce']})
            required(session['anonce']==key['nonce'], 'repeated M1 changes nonce')
        elif info==0x010a:
            session=sessions[station]
            required(record['direction']=='guest-to-ap' and key['replay']==1, 'independent M2 direction/replay')
            context=b''.join(sorted((AP_MAC,station)))+b''.join(sorted((session['anonce'],key['nonce'])))
            ptk=b''.join(hmac.new(pmk,b'Pairwise key expansion\0'+context+bytes((i,)),'sha1').digest() for i in range(4))[:64]
            required(hmac.compare_digest(key['mic'],hmac.new(ptk[:16],key['unsigned'],'sha1').digest()[:16]),
                     'independent actual M2 MIC')
            session.update(ptk=ptk)
        elif info==0x13ca:
            session=sessions[station]
            required(record['direction']=='ap-to-guest', 'independent M3 direction')
            gtk,_=gtk1_from_actual_m3(packet,session['ptk'],session['anonce'])
            session.update(gtk=gtk,m3=True)
        elif info==0x030a:
            session=sessions[station]
            required(record['direction']=='guest-to-ap' and session.get('m3') is True
                     and key['replay']==2 and key['nonce']==bytes(32) and not key['data']
                     and hmac.compare_digest(key['mic'],hmac.new(session['ptk'][:16],key['unsigned'],'sha1').digest()[:16]),
                     'independent actual M4 MIC/shape')
            session['m4']=True
        else:
            raise WireError('unsupported actual EAPOL key flags')
    required(len(sessions)==1 and all(s.get('m4') for s in sessions.values()), 'actual complete group fixture handshake')
    required(counts['group_rx']>=2 and dhcp['dhcp-offer']>=1 and dhcp['dhcp-ack']>=1
             and counts['pairwise_tx']>=2, 'actual group OFFER/ACK and pairwise guest DHCP absent')
    return {'all_actual_wire_hashes_and_mics_verified':True,'actual_group_key_id':1,
        'actual_group_a1_broadcast':True,'actual_m3_gtk1_installation_wire_proved':True,
        'group_frames_authenticated':counts['group_rx'],'pairwise_guest_frames_authenticated':counts['pairwise_tx'],
        'pairwise_ap_frames_authenticated':counts['pairwise_rx'],'group_dhcp_offer':dhcp['dhcp-offer'],
        'group_dhcp_ack':dhcp['dhcp-ack'],'clear_ordinary_null_data_frames':counts['clear_null_data'],
        'actual_m1_nonce_sha256':sorted(sha(s['anonce']) for s in sessions.values()),
        'actual_ptk_sha256':sorted(sha(s['ptk']) for s in sessions.values()),'temporal_key_bytes_in_receipt':False}


def verified_saved_phase(directory, helpers):
    receipt_bytes=(directory/'validation.json').read_bytes()
    required(sha(receipt_bytes)==SAVED_PHASE_RECEIPT_SHA256, 'actual physical Save phase receipt pin mismatch')
    receipt=json.loads(receipt_bytes); run=json.loads((directory/'run/run.json').read_text())
    required(receipt['functional_pass'] is True and receipt['secure_wifi_pass'] is True
             and all(value is True for value in receipt['checks'].values())
             and run['status']=='stopped' and run['exit_code']==0, 'actual physical Save phase is not closed and accepted')
    required(receipt['input']['flash_sha256']==helpers['FULL_FLASH_SHA256'], 'Save phase did not begin with official flash')
    media={name:directory/'run'/file for name,file in [('flash','flash.bin'),('sd','sd.img'),('efuse','efuse.bin')]}
    for name,path in media.items():
        expected=run['storage']['final_'+name+'_sha256'] if name!='efuse' else run['artifact_sha256']['efuse.bin']
        required(file_sha(path)==expected, 'actual saved media pin changed: '+name)
    card=helpers['SMOKE']['Fat16Card'](media['sd']); book=card.read_file('/test.epub')
    required(book==helpers['make_test_epub'](), 'original saved EPUB differs from generated pinned fixture')
    store=card.read_file('/.crosspoint/wifi.json')
    required(store==(directory/'guest-written-wifi.json').read_bytes(), 'actual Save credential output differs from real FAT file')
    value=json.loads(store); credentials=value.get('credentials')
    required(isinstance(credentials,list) and len(credentials)==1 and credentials[0].get('ssid')==SSID.decode()
             and value.get('lastConnectedSsid')==SSID.decode() and 'password' not in credentials[0], 'saved source credential shape')
    efuse=media['efuse'].read_bytes();required(len(efuse)==336,'actual Save eFuse size')
    required(SECURE['decode_validated_credential'](credentials[0].get('password_obf'),efuse[24:30][::-1])==SECURE['SYNTHETIC_PSK'],
             'actual saved CPV1 checksum/password differs from physical Save fixture')
    nonces=SECURE['actual_m1_nonce_hashes'](receipt['raw_ap'])
    keys={item['ptk_sha256'] for item in receipt['raw_ap']['eapol_verifications']
          if item['purpose']=='wpa2-message-2-authenticated'}
    return media,book,store,{'actual_saved_phase_receipt_sha256':sha(receipt_bytes),
        'actual_saved_run_manifest_sha256':file_sha(directory/'run/run.json'),
        'actual_saved_media_sha256':{name:file_sha(path) for name,path in media.items()},
        'actual_saved_store_sha256':sha(store),'original_m1_nonce_sha256':sorted(nonces),
        'original_ptk_sha256':sorted(keys),'actual_save_cpv1_checksum_verified':True,
        'host_credential_seeding':False}


def run_frozen_group(args, output, host_root, helpers, dependencies, provenance):
    provenance['native_candidate']=verified_native_candidate(args)
    saved=verified_saved_phase(args.saved_phase,helpers)
    media,book,store,saved_provenance=saved
    guest=output/'group-reconnect';guest.mkdir();(guest/'frames').mkdir()
    ap=RawGroupAP(guest/'wire',nonce_generation=2001)
    command=[sys.executable,'-m','x3emu','run','--flash',str(media['flash'].resolve()),'--sd',str(media['sd'].resolve()),
             '--efuse',str(media['efuse'].resolve()),'--backend',str(args.backend.resolve()),'--rom-dir',str(args.rom_dir.resolve()),
             '--output',str(guest/'run'),'--wifi-peer',f'connect:{ap.port}','--wifi-channel','1','--wifi-random-seed','1',
             '--icount','--icount-shift','3','--seconds',str(args.host_limit)]
    report={'schema_version':1,'workflow':'actual-saved-wpa2-gtk1-group-dhcp-reconnect',
        'launcher_command':command,'host_dependency_files':dependencies,'source_provenance':provenance,
        'saved_phase_provenance':saved_provenance,'checks':{'backend_stopped_cleanly':False,
        'full_panel_trace_complete':False,'actual_closed_group_wire_authenticated':False,
        'final_native_group_ccmp_traffic_and_zero_errors':False},'frames':{},'functional_pass':False,'strict_pass':False,
        'all_crossink_functions_verified':False,'speed_selection_allowed':False,'guest_memory_or_api_modified':False,
        'limits':{'general_encryption_modelled':False,'group_address_policy_modelled':False,'hardware_replay_modelled':False,
                  'physical_rf_modelled':False,'physical_entropy_modelled':False,'timing_calibrated':False},
        'input':{name+'_sha256':file_sha(path) for name,path in media.items()}}
    process=qmp=replay=experiment=None
    network,smoke=helpers['NETWORK'],helpers['SMOKE']
    try:
        for entry in dependencies:
            data=(host_root/entry['path']).read_bytes()
            required(len(data)==entry['bytes'] and sha(data)==entry['sha256'], 'frozen group host source changed '+entry['path'])
        with (guest/'launcher.log').open('wb') as log:
            process=subprocess.Popen(command,cwd=host_root,stdin=subprocess.DEVNULL,stdout=log,stderr=log)
        experiment=smoke['Experiment'](guest,process,args.step_timeout,button_hold_ms=400)
        experiment.wait('group native launcher QMP',lambda:(guest/'run/run.json').is_file()
                        and json.loads((guest/'run/run.json').read_text())['status']=='running')
        qmp=helpers['QMPClient'](guest/'run/qmp.sock')
        replay=network['Replay'](smoke,experiment,qmp,report,guest);qmp.set_buttons(0)
        experiment.wait('actual new group X3 CPU boot',lambda:'Hardware detect: X3' in experiment.log_text('serial.log'))
        replay.check('genuine_peer_only_transport',qmp.execute('qom-get',{'path':'/machine/wifi','property':'peer-only'}) is True
                     and qmp.execute('qom-get',{'path':'/machine/wifi','property':'air-enabled'}) is False)
        report['native_group_initial']=observe_group_native(qmp)
        replay.check('initial_group_scope_and_zero_errors',True)
        replay.check('new_cpu_starts_with_zero_native_crypto_traffic',all(report['native_group_initial'][name]==0
                     for name in SECURE['CCMP_TRAFFIC_COUNTERS']))
        replay.check('actual_saved_credential_consumed_without_host_seeding',replay.read('/.crosspoint/wifi.json')==store)
        baseline=len(experiment.log_text('serial.log'));helpers['launch_join'](replay)
        def connected():
            if ap.error:
                raise WireError(ap.error)
            return SECURE['fresh_secure_connection'](experiment.log_text('serial.log'),baseline,(1,1,1,1))['verified']
        experiment.wait('genuine saved reconnect and encrypted group DHCP got IP',connected)
        replay.check('actual_group_encrypted_dhcp_source_got_ip',connected(),
                     SECURE['fresh_secure_connection'](experiment.log_text('serial.log'),baseline,(1,1,1,1)))
        report['native_group_connected']=observe_group_native(qmp,require_traffic=True)
        replay.check('actual_native_group_scope_positive_tx_rx_and_zero_errors',True)
        actual_store,observation=SECURE['secure_store_observation'](replay,guest)
        replay.check('saved_store_remains_exact_after_group_reconnect',actual_store==store,observation)
        replay.capture_text('actual-group-network-serving',('192.168.44.2',))
        replay.check('original_epub_unchanged',replay.read('/test.epub')==book)
        report['native_group_before_shutdown']=observe_group_native(qmp,require_traffic=True)
        report['completed']=True
    except Exception as error:
        report['error']=str(error)
    finally:
        if qmp:
            qmp.close()
        if process and process.poll() is None:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.terminate();process.wait(timeout=5);report['shutdown_error']='required termination'
        try:
            ap.close()
        except Exception as error:
            report['peer_close_error']=str(error)
        raw=report['raw_ap']=ap.snapshot()
        report['checks']['external_peer_closed_without_protocol_error']=raw['error'] is None and not report.get('peer_close_error')
        if experiment:
            rom,serial=experiment.log_text('rom.log'),experiment.log_text('serial.log')
            report['checks']['no_sd_error_or_guest_panic']=smoke['FATAL_LOG'].search(rom+'\n'+serial) is None
            reasons=report['reset_reasons']=re.findall(r'Reset diagnostic: reset=\d+\((\w+)\)',serial)
            report['checks']['only_original_poweron_and_software_resets']=bool(reasons and reasons[0]=='POWERON'
                and len(reasons)>=2 and all(reason=='SW' for reason in reasons[1:]))
            report['button_steps']=experiment.steps
        manifest=guest/'run/run.json'
        if manifest.is_file():
            run=report['run_manifest']=json.loads(manifest.read_text())
            report['checks']['backend_stopped_cleanly']=run['status']=='stopped' and run.get('exit_code')==0
            report['checks']['actual_new_cpu_exact_saved_flash_sd_efuse']=all(
                run['storage']['initial_'+name+'_sha256']==report['input'][name+'_sha256'] for name in ('flash','sd')) \
                and run['input']['efuse']['sha256']==report['input']['efuse_sha256']
            report['stopped_panel_trace']=helpers['stopped_trace_assessment'](guest/'run',run)
            report['checks']['full_panel_trace_complete']=bool(report['frames']) and all(frame.get('trace_complete') is True
                for frame in report['frames'].values()) and report['stopped_panel_trace']['complete'] is True
            wifi=run.get('final_state',{}).get('wifi',{})
            try:
                assess_group_native(wifi,require_traffic=True)
                report['native_group_final']={name:wifi[name] for name in tuple(SECURE['CCMP_SCOPE_FLAGS'])
                    +SECURE['CCMP_TRAFFIC_COUNTERS']+SECURE['CCMP_ERROR_COUNTERS']+(GROUP_FLAG,'rx-group-policy-modelled')}
                report['checks']['final_native_group_ccmp_traffic_and_zero_errors']=True
            except WireError as error:
                report['native_group_final_error']=str(error)
            try:
                closed=[json.loads(line) for line in (guest/'wire/wire.jsonl').read_text().splitlines()]
                required(closed==raw['records'],'closed raw group log differs from peer snapshot')
                wire=report['independent_wire_verification']=independent_wire_verification(closed)
                required(wifi['tx-ccmp-encrypted-frames']==wire['pairwise_guest_frames_authenticated']
                         and wifi['rx-ccmp-decrypted-frames']==wire['group_frames_authenticated']+wire['pairwise_ap_frames_authenticated'],
                         'native group RX/pairwise TX counts differ from independently authenticated wire')
                report['checks']['actual_closed_group_wire_authenticated']=True
                report['checks']['actual_new_m1_nonce_and_pairwise_key']=bool(
                    set(wire['actual_m1_nonce_sha256']).isdisjoint(saved_provenance['original_m1_nonce_sha256'])
                    and set(wire['actual_ptk_sha256']).isdisjoint(saved_provenance['original_ptk_sha256']))
            except Exception as error:
                report['closed_group_wire_error']=str(error)
        report['functional_pass']=bool(report.get('completed') and not report.get('error') and not report.get('shutdown_error')
            and report['checks'] and all(value is True for value in report['checks'].values()))
        report['strict_pass']=False
        network['write_json'](output/'validation.json',report)
    return report


def run_group(args):
    native=verified_native_candidate(args)
    required(not args.output.exists() or not any(args.output.iterdir()),'group output must be new or empty')
    args.output.mkdir(parents=True,exist_ok=True);output=args.output.resolve()
    host_root,helpers,dependencies=SECURE['frozen_helpers'](output)
    relative='scripts/test-crossink-secure-wifi-group.py';script=(PROJECT/relative).read_bytes()
    (host_root/relative).write_bytes(script);dependencies.append({'path':relative,'sha256':sha(script),'bytes':len(script)})
    hashes={**helpers['SOURCE_HASHES'],**SECURE['SECURE_SOURCE_HASHES']}
    provenance={'firmware_source_commit':helpers['SOURCE_COMMIT'],'sdk_source_commit':helpers['SDK_COMMIT']}
    for name,root,pins in [('crossink',args.source,hashes),('sdk',args.sdk_source,helpers['SDK_HASHES']),
                          ('ccmp',args.ccmp_reference_source,SECURE['CCMP_REFERENCE_HASHES'])]:
        data=helpers['verify_source_bytes'](root,pins)
        provenance[name]=helpers['snapshot_bytes'](output,'pinned-source/'+name,data)
    provenance['arduino']=helpers['snapshot_bytes'](output,'pinned-source/arduino',SECURE['verified_scan_source'](args.arduino_source))
    provenance['group_primary']=helpers['snapshot_bytes'](output,'pinned-source/group-primary',verified_group_primary(args.group_audit_source))
    provenance['crypto_provider']=SECURE['crypto_preflight']()
    native_snapshot=output/'pinned-source/native-validation.json'
    native_snapshot.write_bytes(args.native_validation.read_bytes())
    provenance['native_candidate']=native
    frozen=runpy.run_path(str(host_root/relative))
    return frozen['run_frozen_group'](args,output,host_root,helpers,dependencies,provenance)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--backend',type=Path,required=True)
    parser.add_argument('--rom-dir',type=Path,required=True)
    parser.add_argument('--native-validation',type=Path,required=True,help='closed full native group gate receipt')
    parser.add_argument('--native-validation-sha256',required=True,help='exact independent reviewed full-gate receipt digest')
    parser.add_argument('--saved-phase',type=Path,required=True,help='closed actual physical-password-save phase with pinned receipt')
    parser.add_argument('--source',type=Path,default=PROJECT.parent/'crossink-harness-src')
    parser.add_argument('--sdk-source',type=Path,default=PROJECT.parent/'freeink-sdk')
    parser.add_argument('--arduino-source',type=Path,required=True)
    parser.add_argument('--ccmp-reference-source',type=Path,required=True)
    parser.add_argument('--group-audit-source',type=Path,default=PROJECT/'local/recovery/group-crypto-primary-audit')
    parser.add_argument('--step-timeout',type=float,default=150)
    parser.add_argument('--host-limit',type=float,default=600)
    args=parser.parse_args();result=run_group(args)
    print(json.dumps({key:result.get(key) for key in ('workflow','functional_pass','strict_pass','error')}))
    return 0 if result['functional_pass'] else 1


if __name__=='__main__':
    raise SystemExit(main())
