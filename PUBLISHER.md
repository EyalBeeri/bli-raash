# Publisher session

Writes to the בלי רעש page need a person's one-time consent in an interactive Claude session, so a
background process can't do them. The pipeline (`run.py`) prepares the documents and raises a flag; this
session watches the flag and pushes. Eyal approves the first database write when the session starts.

Page: https://claude.ai/artifact/DqHRNjpSceDQVtsAggE5vZ

## Loop

1. Arm a Monitor with `python C:/Users/Eyal/bli-raash/watch_pending.py`, `timeout_ms` 1800000,
   description "בלי רעש: new feed batch". Re-arm it every time it expires. Never stop the loop.
2. On each `PENDING seq=N docs=...` event:
   - Read `C:/Users/Eyal/bli-raash/data/out/pending.json` and check the seq is still N. If it changed, handle the newer one.
   - `ArtifactData` `list` on collection `feed` of the page **with `out_dir` `C:/Users/Eyal/bli-raash/data/test/pulled`**
     to get each document's current `version` without pulling the documents into the conversation.
   - One `ArtifactData` `batch` with a `set` per listed doc: collection `feed`, doc_id = the doc name,
     `file_path` = `C:/Users/Eyal/bli-raash/data/out/<doc>.json`, `if_version` = the version you just read
     (omit it only for a doc that doesn't exist yet).
   - On success run `python C:/Users/Eyal/bli-raash/publish.py --ack N`. If it says a newer batch is pending, push that one too.
   - On a version conflict, list again and retry once.
3. Keep replies to one short line per push. Don't read the JSON files into the conversation; `file_path` sends them.

Only ever write the `feed/latest` and `feed/filtered` documents. Never write votes or anything else.
