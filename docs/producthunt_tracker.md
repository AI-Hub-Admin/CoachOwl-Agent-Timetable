# Track Competitor Launches on Product Hunt

This repo includes a small tracker that watches Product Hunt RSS/Atom feeds and returns **new** launches that match your competitor keywords (deduped per `user_id` in sqlite).

## Configure

- Copy `assets/producthunt_tracker_config.example.json` → `assets/producthunt_tracker_config.json`
- Edit:
  - `user_id`: any stable id (used for dedupe)
  - `keywords`: competitor names / product names / domains / keywords
  - `feed_urls`: defaults to `https://www.producthunt.com/feed` if omitted

## Run

From repo root:

```bash
python -m python.src.producthunt_tracker
```

The output is JSON:
- `new_matches`: entries not seen before for this `user_id`
- `total_matches`: matches found in the feed window (may include previously seen)

## Notes

- Dedupe table: `producthunt_seen` in `db/project.sqlite`
- Feed date handling is best-effort; if a feed item omits published time, it will be considered eligible.

