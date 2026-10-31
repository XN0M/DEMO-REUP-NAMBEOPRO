"""Explicit metadata-only live check; never edits channels or downloads media."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.douyin_monitor import scan_channel
from core.monitor_errors import MonitorError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('url', help='Public Douyin profile URL')
    parser.add_argument('--output', type=Path, help='Optional JSON verification report')
    args = parser.parse_args()
    try:
        first = scan_channel(args.url)
        second = scan_channel(args.url, known_ids=set(first.observed_ids))
        unseen = [v for v in second.videos if v['video_id'] not in first.observed_ids]
        report = {'ok': bool(first.videos), 'mode': 'anonymous_new_context_each_scan',
                  'count': len(first.videos), 'complete': first.complete,
                  'repeat_complete': second.complete, 'new_ids_on_repeat': [v['video_id'] for v in unseen],
                  'videos': first.videos}
    except MonitorError as error:
        report = {'ok': False, 'mode': 'anonymous_new_context', 'code': error.code, 'message': str(error)}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
