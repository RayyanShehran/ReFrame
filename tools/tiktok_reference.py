"""Temporary-only developer TikTok feasibility CLI."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import reference_engine


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="One public HTTPS TikTok video URL or vm/vt short link")
    args = parser.parse_args()
    try:
        result = reference_engine.run_reference(args.url)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))
    return 0 if result["video_decode"] == "passed" and result["audio"] in {"decoded", "absent"} and result["failure_category"] is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
else:
    sys.modules[__name__] = reference_engine
