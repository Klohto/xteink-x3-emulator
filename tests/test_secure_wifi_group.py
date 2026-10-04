"""Independent GTK1 packet/source guards. Host passes do not prove guest RX."""
import base64
from contextlib import contextmanager
import hashlib
import hmac
import json
from pathlib import Path
import runpy
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

GROUP = runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/test-crossink-secure-wifi-group.py'))
WIRE = GROUP['SECURE']
STATION = bytes.fromhex('025833454401')

# Source-audit recipe independently emitted this AESCCM wire vector. Its full
# JSON SHA256 is 002883ce02c59b78a676e9cc87bdf14b9324db3826bba3f04a521d81fa6c8092.
GOLDEN = {
    'key':'000102030405060708090a0b0c0d0e0f',
    'header':'08427788ffffffffffff02000000000202000000000330b6',
    'extiv':'0300006000000000',
    'plain':'aaaa0300000008000b30557a9fc4e90e33587da2c7ec11365b80a5caef14395e83a8cdf2173c6186abd0f51a3f6489aed3f81d42678cb1d6',
    'aad':'0842ffffffffffff0200000000020200000000030000',
    'nonce':'00020000000002000000000003',
    'wire':'08427788ffffffffffff02000000000202000000000330b60300006000000000e2c6cc10e7c83b598a47bc46c77deb98bde1242a5e2f6db4e8ca7ebbb2e2a2b9a143dc1543ed94c382da99e0cc02806bd7e7a14043207d0400d98529335dc46b',
}


@contextmanager
def stopped_ap():
    class Channel:
        def __init__(self): self.packets=[]
        def sendall(self,data): self.packets.append(data)
        def close(self): pass
    with tempfile.TemporaryDirectory() as directory:
        ap=GROUP['RawGroupAP'](Path(directory)/'wire')
        ap.finished.set();ap.thread.join(2);ap.connection=Channel()
        try:
            yield ap
        finally:
            ap.close()


def genuine_fixture_handshake(ap):
    ap.authenticated.add(STATION);ap.associated.add(STATION)
    ap.start_handshake(STATION)
    session=ap.handshakes[STATION]
    snonce=bytes(range(32))
    pmk=hashlib.pbkdf2_hmac('sha1',WIRE['SYNTHETIC_PSK'],WIRE['SSID'],4096,32)
    ptk=WIRE['wpa2_ptk'](pmk,WIRE['AP_MAC'],STATION,session['anonce'],snonce)
    m2=WIRE['eapol_key'](0x010a,1,snonce,data=WIRE['RSN_IE'],kck=ptk[:16])
    m4=WIRE['eapol_key'](0x030a,2,bytes(32),kck=ptk[:16])
    # Only an external test sender supplies M2/M4. These tests never start a CPU.
    header=struct.pack('<HH6s6s6sH',0x0108,0,WIRE['AP_MAC'],STATION,WIRE['AP_MAC'],0)
    ap.receive(header+WIRE['LLC']+bytes.fromhex('888e')+m2)
    ap.receive(header+WIRE['LLC']+bytes.fromhex('888e')+m4)
    return ptk,m2,m4


def actual_dhcp_requests(ap,ptk):
    fixed=bytearray(236);fixed[:3]=b'\x01\x01\x06';fixed[4:8]=bytes.fromhex('deadbeef');fixed[28:34]=STATION
    header=struct.pack('<HH6s6s6sH',0x0108,0,WIRE['AP_MAC'],STATION,bytes((255,))*6,0)
    for pn,kind in [(3,1),(4,3)]:
        options=bytes((53,1,kind))
        if kind==3:
            options+=bytes((50,4))+WIRE['CLIENT_IP']+bytes((54,4))+WIRE['AP_IP']
        datagram=WIRE['udp_packet'](bytes(fixed)+WIRE['DHCP_COOKIE']+options+b'\xff',68,67,bytes(4),bytes((255,))*4,1)
        frame=WIRE['CCMPPairwisePeer'](ptk[32:48]).encrypt(header,WIRE['LLC']+bytes.fromhex('0800')+datagram,pn)
        ap.receive(frame)


class GroupPacketTests(unittest.TestCase):
    def test_fixed_independent_group1_vector_matches_encoder_and_decoder(self):
        key,header,plain,wire=(bytes.fromhex(GOLDEN[name]) for name in ('key','header','plain','wire'))
        peer=GROUP['Group1Peer'](key,transmitter=header[10:16],source=header[16:22])
        clear=header[:1]+bytes((header[1]&~0x40,))+header[2:]
        self.assertEqual(peer.encrypt(clear,plain,3),wire)
        decoded=peer.authenticate(wire)
        self.assertEqual(decoded['payload'],plain)
        self.assertEqual(decoded['aad'].hex(),GOLDEN['aad'])
        self.assertEqual(decoded['nonce'].hex(),GOLDEN['nonce'])
        self.assertEqual(decoded['key_id'],1)

    def test_mutated_mac_key_id_ciphertext_mic_and_pn_refuse_without_replay_change(self):
        header=bytes.fromhex(GOLDEN['header']);wire=bytes.fromhex(GOLDEN['wire']);key=bytes.fromhex(GOLDEN['key'])
        for offset in [1,4,10,16,24,27,32,len(wire)-1]:
            altered=wire[:offset]+bytes((wire[offset]^1,))+wire[offset+1:]
            peer=GROUP['Group1Peer'](key,transmitter=header[10:16],source=header[16:22])
            with self.subTest(offset=offset),self.assertRaises(GROUP['WireError']):
                peer.authenticate(altered)
            self.assertEqual(peer.received_pn,{})
        for key_id in (0,2,3):
            altered=wire[:27]+bytes((0x20|(key_id<<6),))+wire[28:]
            peer=GROUP['Group1Peer'](key,transmitter=header[10:16],source=header[16:22])
            with self.subTest(key_id=key_id),self.assertRaises(GROUP['WireError']):
                peer.authenticate(altered)
            self.assertEqual(peer.received_pn,{})

    def test_mic_failure_does_not_consume_valid_pn_and_equal_pn_is_refused_by_external_observer(self):
        header=bytes.fromhex(GOLDEN['header']);wire=bytes.fromhex(GOLDEN['wire'])
        peer=GROUP['Group1Peer'](bytes.fromhex(GOLDEN['key']),transmitter=header[10:16],source=header[16:22])
        with self.assertRaisesRegex(GROUP['WireError'],'MIC'):
            peer.authenticate(wire[:-1]+bytes((wire[-1]^1,)))
        self.assertEqual(peer.authenticate(wire)['pn'],3)
        with self.assertRaisesRegex(GROUP['WireError'],'replay'):
            peer.authenticate(wire)

    def test_group1_profile_refuses_to_ds_unicast_identity_fragment_and_bad_pn(self):
        header=struct.pack('<HH6s6s6sH',0x0208,0,GROUP['BROADCAST'],WIRE['AP_MAC'],WIRE['AP_MAC'],0)
        peer=GROUP['Group1Peer'](bytes(range(16)))
        for pn in (0,-1,1<<48,True):
            with self.subTest(pn=pn),self.assertRaises(GROUP['WireError']):peer.encrypt(header,b'payload',pn)
        for offset,mask in [(1,3),(4,1),(10,2),(22,1)]:
            altered=header[:offset]+bytes((header[offset]^mask,))+header[offset+1:]
            with self.subTest(offset=offset),self.assertRaises(GROUP['WireError']):peer.encrypt(altered,b'payload',1)

    def test_actual_transmitted_m3_supplies_gtk_and_independently_authenticates_broadcast_dhcp(self):
        with stopped_ap() as ap:
            ptk,_,_=genuine_fixture_handshake(ap)
            self.assertTrue(ap.group_verifications[-1]['actual_m3_aes_unwrap_verified'])
            actual_dhcp_requests(ap,ptk)
            observed=GROUP['independent_wire_verification'](ap.records)
            self.assertEqual(observed['group_frames_authenticated'],2)
            self.assertEqual(observed['pairwise_guest_frames_authenticated'],2)
            self.assertEqual(observed['group_dhcp_offer'],1)
            self.assertEqual(observed['group_dhcp_ack'],1)
            for record in ap.records:
                if record['purpose'] in ('dhcp-offer','dhcp-ack'):
                    frame=base64.b64decode(record['raw_base64'])
                    self.assertEqual(frame[4:10],GROUP['BROADCAST'])
                    self.assertEqual(frame[27]>>6,1)
                    self.assertTrue(record['protected'])
            self.assertEqual(ap.group_counters,{'encrypted_group_dhcp_offer':1,'encrypted_group_dhcp_ack':1})

    def test_repeated_m4_keeps_group_pn_and_pairwise_arp_domain(self):
        with stopped_ap() as ap:
            ptk,_,m4=genuine_fixture_handshake(ap);actual_dhcp_requests(ap,ptk)
            peer=ap.group_sessions[STATION]['peer'];ap.handshake(STATION,m4)
            self.assertIs(ap.group_sessions[STATION]['peer'],peer)
            self.assertEqual(ap.group_sessions[STATION]['tx_pn'],2)
            self.assertEqual(ap.handshakes[STATION]['tx_pn'],0)
            arp=bytes.fromhex('0001080006040002')+WIRE['AP_MAC']+WIRE['AP_IP']+STATION+WIRE['CLIENT_IP']
            ap.data(STATION,0x0806,arp,'arp-reply')
            response=ap.connection.packets[-1][8:]
            self.assertEqual(response[4:10],STATION)
            self.assertEqual(response[27]>>6,0)
            self.assertEqual(ap.handshakes[STATION]['tx_pn'],1)
            self.assertEqual(ap.group_sessions[STATION]['tx_pn'],2)

    def test_wrong_m3_mic_wrapped_key_nonce_or_key_id_never_establishes_gtk(self):
        from cryptography.hazmat.primitives.keywrap import aes_key_wrap
        with stopped_ap() as ap:
            ptk,_,_=genuine_fixture_handshake(ap);session=ap.handshakes[STATION];m3=session['m3']
            for offset in (17,81,99):
                bad=m3[:offset]+bytes((m3[offset]^1,))+m3[offset+1:]
                with self.subTest(offset=offset),self.assertRaises(GROUP['WireError']):
                    GROUP['gtk1_from_actual_m3'](bad,ptk,session['anonce'])
            for key_id in (0,2,3):
                data=WIRE['RSN_IE']+bytes.fromhex('dd16000fac01')+bytes((key_id,0))+bytes(range(16))
                data+=b'\xdd'+bytes(7-(len(data)&7))
                bad=WIRE['eapol_key'](0x13ca,2,session['anonce'],data=aes_key_wrap(ptk[16:32],data),kck=ptk[:16])
                with self.subTest(key_id=key_id),self.assertRaisesRegex(GROUP['WireError'],'KeyID1'):
                    GROUP['gtk1_from_actual_m3'](bad,ptk,session['anonce'])
            parsed=WIRE['parse_eapol_key'](m3);wrapped=parsed['data']
            corrupted=wrapped[:-1]+bytes((wrapped[-1]^1,))
            bad=WIRE['eapol_key'](0x13ca,2,session['anonce'],data=corrupted,kck=ptk[:16])
            with self.assertRaisesRegex(GROUP['WireError'],'unwrap'):
                GROUP['gtk1_from_actual_m3'](bad,ptk,session['anonce'])

    def test_group_dhcp_never_sends_before_authentic_m4(self):
        with stopped_ap() as ap:
            before=len(ap.records)
            with self.assertRaisesRegex(GROUP['WireError'],'authentic M4'):
                ap.data(STATION,0x0800,bytes(28),'dhcp-offer')
            self.assertEqual(len(ap.records),before)
            self.assertFalse(any(ap.group_counters.values()))

    def test_closed_wire_corruption_refused_even_if_metadata_relabelled(self):
        with stopped_ap() as ap:
            ptk,_,_=genuine_fixture_handshake(ap);actual_dhcp_requests(ap,ptk)
            records=list(ap.records);index=next(i for i,r in enumerate(records) if r['purpose']=='dhcp-offer')
            item=records[index];frame=base64.b64decode(item['raw_base64']);frame=frame[:-1]+bytes((frame[-1]^1,))
            records[index]={**item,'raw_base64':base64.b64encode(frame).decode(),'sha256':hashlib.sha256(frame).hexdigest()}
            with self.assertRaises(Exception):GROUP['independent_wire_verification'](records)

    def test_clear_power_save_null_data_is_observed_separately_and_cannot_hide_protected_or_malformed_frames(self):
        with stopped_ap() as ap:
            ptk,_,_=genuine_fixture_handshake(ap);actual_dhcp_requests(ap,ptk)
            null=struct.pack('<HH6s6s6sH',0x1148,0,WIRE['AP_MAC'],STATION,WIRE['AP_MAC'],0)
            ap.receive(null)
            result=GROUP['independent_wire_verification'](ap.records)
            self.assertEqual(result['clear_ordinary_null_data_frames'],1)
            self.assertEqual(result['group_frames_authenticated'],2)
            last=ap.records[-1]
            for mutated in [null[:1]+bytes((null[1]|0x40,))+null[2:],null+b'extra',
                            null[:4]+bytes(6)+null[10:]]:
                changed={**last,'raw_base64':base64.b64encode(mutated).decode(),'bytes':len(mutated),
                         'sha256':hashlib.sha256(mutated).hexdigest(),'protected':bool(mutated[1]&0x40)}
                with self.subTest(frame=mutated.hex()),self.assertRaises(Exception):
                    GROUP['independent_wire_verification'](ap.records[:-1]+[changed])

    def test_external_peer_closes_thread_and_wire_file_without_claiming_native_guest_success(self):
        with tempfile.TemporaryDirectory() as directory:
            ap=GROUP['RawGroupAP'](Path(directory)/'wire');ap.close()
            self.assertFalse(ap.thread.is_alive());self.assertTrue(ap.log.closed)
            snapshot=ap.snapshot();self.assertFalse(snapshot['group_fixture']['native_group_verified'])
            self.assertFalse(snapshot['native_ccmp_verified']);self.assertFalse(snapshot['speed_selection_allowed'])


class GroupNativeAndSourceTests(unittest.TestCase):
    def test_group_native_guard_requires_readonly_typed_complete_scope_and_zero_errors(self):
        valid={**WIRE['CCMP_SCOPE_FLAGS'],**dict.fromkeys(WIRE['CCMP_TRAFFIC_COUNTERS'],3),
               **dict.fromkeys(WIRE['CCMP_ERROR_COUNTERS'],0),GROUP['GROUP_FLAG']:True,'rx-group-policy-modelled':False}
        self.assertTrue(GROUP['assess_group_native'](valid,require_traffic=True))
        for name,value in [(GROUP['GROUP_FLAG'],None),(GROUP['GROUP_FLAG'],1),(GROUP['GROUP_FLAG'],False),
                           ('rx-group-policy-modelled',True),('rx-group-policy-modelled',0),
                           ('rx-ccmp-auth-failed-frames',1),('rx-ccmp-decrypted-frames',0)]:
            changed={**valid,name:value}
            with self.subTest(name=name,value=value),self.assertRaises(GROUP['WireError']):
                GROUP['assess_group_native'](changed,require_traffic=True)
        class QMP:
            def __init__(self):self.calls=[]
            def execute(self,method,args):self.calls.append((method,args));return valid[args['property']]
        qmp=QMP();self.assertEqual(GROUP['observe_group_native'](qmp,require_traffic=True),valid)
        self.assertTrue(all(method=='qom-get' and args['path']=='/machine/wifi' for method,args in qmp.calls))

    def test_primary_contract_corruption_is_refused_before_native_launch(self):
        observer=GROUP['verified_group_primary'];pins=observer.__globals__['GROUP_PRIMARY_HASHES']
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);contract=b'bounded declared group contract\n';receipt=json.dumps(
                {'firmware_modified':False,'native_changes':False,'guest_execution':False}).encode()
            testpins={'GROUP-CONTRACT.md':hashlib.sha256(contract).hexdigest(),'audit-receipt.json':hashlib.sha256(receipt).hexdigest()}
            (root/'GROUP-CONTRACT.md').write_bytes(contract);(root/'audit-receipt.json').write_bytes(receipt)
            with patch.dict(pins,testpins,clear=True):
                self.assertEqual(observer(root)['GROUP-CONTRACT.md'],contract)
                (root/'GROUP-CONTRACT.md').write_bytes(contract+b'changed')
                with self.assertRaisesRegex(GROUP['WireError'],'pin mismatch'):observer(root)

    def test_saved_input_guard_refuses_arbitrary_or_modified_phase_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'validation.json').write_text(json.dumps({'functional_pass':True}))
            with self.assertRaisesRegex(GROUP['WireError'],'Save phase receipt pin mismatch'):
                GROUP['verified_saved_phase'](root,{})

    def test_native_guard_refuses_unpinned_or_old_pairwise_only_gate_before_socket_or_cpu(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);receipt=root/'native.json';data=json.dumps({'full_gate_pass':True,
                'full_gate_cases':119,'full_gate_suites':12,'skips':0}).encode();receipt.write_bytes(data)
            args=SimpleNamespace(native_validation=receipt,native_validation_sha256='0'*64)
            with self.assertRaisesRegex(GROUP['WireError'],'receipt pin mismatch'):
                GROUP['verified_native_candidate'](args)
            args.native_validation_sha256=hashlib.sha256(data).hexdigest()
            with self.assertRaisesRegex(GROUP['WireError'],'full gate is incomplete'):
                GROUP['verified_native_candidate'](args)


if __name__=='__main__':
    unittest.main()
