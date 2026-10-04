# Published full CrossInk receipts

`index.json` lists the 57 previously approved full validation records, their
compressed and decompressed SHA-256 hashes, source/backend identity and unchanged
pass/failure flags. Each gzip file is byte-identical to the earlier published
checkpoint. Read it with `gzip -dc FILE.json.gz` or Python `gzip.open`.

The current local archive contains 119 captured records. This checkpoint
publishes source code and readable evidence metadata while retaining only those
57 approved full records; 62 further payloads remain local. Two compressed
records were rejected by automatic upload review, and all other nonapproved
compressed records were deferred without further upload attempts. The index
discloses only their paths and the publication scope. Working originals are
unchanged. No deferred record payload is included in this checkpoint.

Incomplete strict-model results and protocol failures remain recorded.
`all_functions_verified` and `speed_selection_allowed` remain false.
