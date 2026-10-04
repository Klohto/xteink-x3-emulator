# Full CrossInk receipts

`index.json` lists each full validation receipt, its compressed and decompressed
SHA256, original receipt SHA256, backend identity and unchanged pass/failure
flags. Files use lossless gzip compression to keep repeated native traces small.

Read any receipt with `gzip -dc FILE.json.gz` or Python's `gzip.open`.
The records include incomplete strict-model results and failed protocols;
`all_functions_verified` and `speed_selection_allowed` remain false.

This checkpoint publishes 57 of 65 captured full receipts. Two compressed
records were rejected by automatic upload review and six further records were
deferred. Their unchanged originals are retained locally; `index.json` records
the omitted paths. The published code and its test results are unaffected.
