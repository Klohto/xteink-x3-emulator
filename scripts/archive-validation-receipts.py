#!/usr/bin/env python3
"""Archive complete receipts losslessly; never upgrade their validation flags."""

import argparse
from datetime import date
import gzip
import hashlib
import json
from pathlib import Path
import re

PROJECT = Path(__file__).resolve().parent.parent


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def normalize(value):
    if isinstance(value, dict):
        return {key: normalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize(item) for item in value]
    if isinstance(value, str):
        return value.replace(str(PROJECT.parent) + '/', 'workspace/')
    return value


def backend_hash(receipt):
    for value in (receipt, receipt.get('run_manifest', {}), receipt.get('final_run', {})):
        if isinstance(value, dict):
            backend = value.get('backend', {})
            if isinstance(backend, dict) and backend.get('sha256'):
                return backend['sha256']
    return receipt.get('backend_sha256')


def archive(source, directory):
    index_path = directory/'index.json'
    index = json.loads(index_path.read_text())
    if index.get('schema_version') != 2:
        raise ValueError('destination must have a schema2 receipt index')
    original = source.read_bytes()
    digest = sha256(original)
    if any(row['original_receipt_sha256'] == digest for row in index['receipts']):
        return None
    receipt = json.loads(original)
    if not isinstance(receipt, dict):
        raise ValueError('receipt must be a JSON object')
    pin = receipt.get('firmware_source_commit', receipt.get('source_commit'))
    if pin is not None and pin != index['firmware_source_commit']:
        raise ValueError('receipt firmware source differs from the index')
    name = receipt.get('workflow', source.parent.name)
    name = re.sub(r'[^a-zA-Z0-9_-]+', '-', name).strip('-') or 'receipt'
    exported = normalize(receipt)
    try:
        original_path = str(source.relative_to(PROJECT))
    except ValueError:
        original_path = str(source)
    exported['archive'] = {
        'original_receipt_sha256': digest, 'original_receipt': original_path,
        'archived_date': date.today().isoformat(),
        'scope': 'complete original receipt; container path prefix normalized; all flags/counters retained',
    }
    raw = (json.dumps(exported, indent=2) + '\n').encode()
    compressed = gzip.compress(raw, mtime=0)
    assert gzip.decompress(compressed) == raw
    target = directory/f'{name}-{digest[:8]}.json.gz'
    with target.open('xb') as output:
        output.write(compressed)
    functional_field = next((field for field in ('functional_pass', 'stock_usb_file_transfer_verified',
                              'reading_flow_pass') if field in receipt), None)
    strict_field = 'strict_pass' if 'strict_pass' in receipt else (
        'passed' if 'reading_flow_pass' in receipt and 'passed' in receipt else None)
    row = {
        'path': str(target.relative_to(PROJECT)), 'encoding': 'gzip',
        'sha256': sha256(compressed), 'decompressed_sha256': sha256(raw),
        'original_receipt_sha256': digest, 'backend_sha256': backend_hash(receipt),
        'functional_pass': receipt.get(functional_field), 'functional_pass_field': functional_field,
        'strict_pass': receipt.get(strict_field), 'strict_pass_field': strict_field,
        'panel_trace_complete': receipt.get('panel_trace_complete'),
        'status': receipt.get('status'), 'checks': len(receipt.get('checks', {})),
    }
    index['receipts'].append(row)
    # Archiving evidence never grants comprehensive verification or speed use.
    index['all_functions_verified'] = False
    index['speed_selection_allowed'] = False
    partial = index_path.with_suffix('.json.partial')
    partial.write_text(json.dumps(index, indent=2) + '\n')
    partial.replace(index_path)
    assert source.read_bytes() == original
    return row['path']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('receipts', type=Path, nargs='+')
    parser.add_argument('--directory', type=Path, default=PROJECT/'docs/evidence/functions-latest')
    args = parser.parse_args()
    for receipt in args.receipts:
        result = archive(receipt.resolve(), args.directory.resolve())
        print(result or f'already archived: {receipt.name}')
