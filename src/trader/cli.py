"""Offline CLI: check policy or produce a ranked allocation plan; never submit orders."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
from .core import BASELINE, Policy, Instrument, Exposure, Book, Candidate, IntentLedger, allocate


def policy_at(path):
    return Policy(**json.loads(Path(path).read_text()))


def instrument(value):
    return Instrument(**value)


def book_at(path):
    raw = json.loads(Path(path).read_text())
    def exposures(name):
        return tuple(Exposure(instrument(row['instrument']), row['quantity'], row['entry'], row['stop'], row['mark']) for row in raw.get(name, []))
    return Book(raw['cash'], exposures('positions'), exposures('pending'))


def candidates_at(path):
    raw = json.loads(Path(path).read_text())
    if raw.get('version') != 1 or not isinstance(raw.get('candidates'), list):
        raise ValueError('Unsupported candidate artifact')
    return [Candidate(instrument(row['instrument']), row['entry'], row['stop'], row['target'],
                      row['quality'], row['history_score'], row['history_lower'], row['history_samples'])
            for row in raw['candidates']]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--policy', default='config/portfolio.json')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('check')
    plan = sub.add_parser('plan')
    plan.add_argument('--book', required=True)
    plan.add_argument('--candidates', required=True)
    plan.add_argument('--output', default='reports/plan.json')
    initialize = sub.add_parser('init-intent-ledger')
    initialize.add_argument('--path', default='state/offline_intents.sqlite')
    args = parser.parse_args(argv)
    policy = policy_at(args.policy)
    if args.command == 'check':
        print(json.dumps(dict(phase='ARCHITECTURE_ONLY', execution_enabled=False,
            baseline_commit=BASELINE, policy=asdict(policy)), default=str, indent=2))
    elif args.command == 'init-intent-ledger':
        IntentLedger.initialize(args.path, policy)
        print('Offline intent ledger created; no broker account or orders touched.')
    else:
        report = allocate(book_at(args.book), candidates_at(args.candidates), policy)
        report.update(phase='OFFLINE_PLAN_ONLY', baseline_commit=BASELINE,
            warning='Input artifacts are not broker-verified. No quotes fetched or orders submitted.')
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
