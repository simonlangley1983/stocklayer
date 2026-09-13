"""Distinguish a saved partial batch from a broken sentiment collection."""
import argparse
import json
import os
from pathlib import Path


def outcome(code, status):
    if code == 0:
        return 0, 'notice', 'Sentiment collection completed.'
    paused = status.get('pausedReason', '')
    if code == 2 and paused == 'Collection time budget reached; remaining dates will resume later' and status.get('completedObservationCount', 0) > 0:
        return 0, 'warning', (f"Partial sentiment coverage: {status['completedObservationCount']}/"
            f"{status.get('requestedObservationCount', '?')} observations. Time limit reached; "
            'saved progress will resume in later runs. Coverage remains below target.')
    return code or 1, 'error', f'Sentiment collection failed or is below its coverage target (exit {code}). See sentiment/run-status.json.'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--exit-code', type=int, required=True)
    parser.add_argument('--status', type=Path, default=Path('sentiment/run-status.json'))
    args = parser.parse_args()
    status = json.loads(args.status.read_text())
    code, level, message = outcome(args.exit_code, status)
    print(f'::{level}::{message}')
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as stream:
            stream.write(message + '\n')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
