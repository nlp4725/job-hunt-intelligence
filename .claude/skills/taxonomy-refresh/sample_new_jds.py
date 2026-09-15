"""Sample job descriptions collected since the last taxonomy refresh.

Proportional by track and seeded, so a run is reproducible. Duplicates and
off-track rows are excluded (see _common.load_corpus).

    ./venv/bin/python .claude/skills/taxonomy-refresh/sample_new_jds.py --n 500
"""

import argparse
import random
from collections import defaultdict
from datetime import datetime, timedelta

from _common import last_window_end, load_corpus, today_run_dir, write_json


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since", help="YYYY-MM-DD. Default: window_end of the last recorded run, else 14 days ago")
    parser.add_argument("--n", type=int, default=500, help="total sample size across tracks (default 500)")
    parser.add_argument("--track", choices=["ml_ai", "pm"], help="restrict to one track")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", help="default: runs/<today>/sample.json")
    args = parser.parse_args()

    window_end = datetime.now()
    since = datetime.fromisoformat(args.since) if args.since else (last_window_end() or window_end - timedelta(days=14))

    rows = load_corpus(track=args.track, since=since)
    by_track = defaultdict(list)
    for row in rows:
        by_track[row.track].append(row)

    rng = random.Random(args.seed)
    picked = []
    for track, items in sorted(by_track.items()):
        k = min(len(items), round(args.n * len(items) / len(rows)))
        picked.extend(rng.sample(items, k))

    out = args.out or today_run_dir() / "sample.json"
    write_json(out, {
        "created_at": window_end.isoformat(),
        "window_start": since.isoformat(),
        "window_end": window_end.isoformat(),
        "population": {t: len(v) for t, v in by_track.items()},
        "jobs": [
            {"id": r.id, "track": r.track, "title": r.title,
             "first_seen_at": r.first_seen_at, "raw_text": r.raw_text}
            for r in picked
        ],
    })
    print(f"window {since:%Y-%m-%d} → {window_end:%Y-%m-%d}")
    print(f"population {dict((t, len(v)) for t, v in by_track.items())}, sampled {len(picked)}")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
