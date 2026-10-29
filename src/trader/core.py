"""Offline planning core. No broker credentials, network calls or order submission."""
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_FLOOR
import hashlib
import json
import math
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

BASELINE = 'c823b1209ee62d692d659dcededa0efcb9069ed7'


def money(value):
    if isinstance(value, bool):
        raise ValueError('Boolean is not a financial number')
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError('Non-finite financial number')
    return result


@dataclass(frozen=True)
class Policy:
    starting_equity: Decimal = Decimal('2000')
    total_risk_limit: Decimal = Decimal('15')
    position_risk_fraction: Decimal = Decimal('0.06')
    max_position_fraction: Decimal = Decimal('0.25')
    max_positions: int = 4
    min_history_samples: int = 20
    min_reward_risk: Decimal = Decimal('1.5')
    paper_only: bool = True
    execution_enabled: bool = False
    currency: str = 'USD'

    def __post_init__(self):
        for key in ('starting_equity', 'total_risk_limit', 'position_risk_fraction', 'max_position_fraction', 'min_reward_risk'):
            object.__setattr__(self, key, money(getattr(self, key)))
        if self.starting_equity <= 0 or self.total_risk_limit <= 0:
            raise ValueError('Invalid capital or risk limit')
        if not 0 < self.position_risk_fraction <= Decimal('0.06') or not 0 < self.max_position_fraction <= 1:
            raise ValueError('Invalid allocation/risk fraction')
        for key in ('max_positions', 'min_history_samples'):
            value = getattr(self, key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError('Invalid ' + key)
        if self.min_reward_risk <= 0 or self.paper_only is not True or self.execution_enabled is not False or self.currency != 'USD':
            raise ValueError('Foundation supports disabled USD paper planning only')


@dataclass(frozen=True)
class Instrument:
    symbol: str
    exchange: str
    currency: str = 'USD'

    def __post_init__(self):
        import re
        if not re.fullmatch(r'[A-Z][A-Z0-9.-]{0,14}', self.symbol):
            raise ValueError('Invalid symbol')
        if self.exchange not in ('XNAS', 'XNYS', 'ARCX') or self.currency != 'USD':
            raise ValueError('Market/currency not enabled; TSX is a later adapter')

    @property
    def key(self):
        return f'{self.exchange}:{self.currency}:{self.symbol}'


@dataclass(frozen=True)
class Exposure:
    instrument: Instrument
    quantity: Decimal
    entry: Decimal
    stop: Decimal
    mark: Decimal

    def __post_init__(self):
        for name in ('quantity', 'entry', 'stop', 'mark'):
            object.__setattr__(self, name, money(getattr(self, name)))
            if getattr(self, name) <= 0:
                raise ValueError('Invalid exposure ' + name)

    @property
    def risk(self):
        # Never offset one position's planned loss with another's unrealized profit.
        return self.quantity * max(Decimal(0), self.entry - self.stop)

    @property
    def cost(self):
        return self.quantity * self.entry


@dataclass(frozen=True)
class Book:
    cash: Decimal
    positions: tuple = field(default_factory=tuple)
    pending: tuple = field(default_factory=tuple)

    def __post_init__(self):
        object.__setattr__(self, 'cash', money(self.cash))
        object.__setattr__(self, 'positions', tuple(self.positions))
        object.__setattr__(self, 'pending', tuple(self.pending))
        if any(p.stop >= p.entry for p in self.pending):
            raise ValueError('Pending buy must have a protective stop below entry')
        if self.cash < 0 or self.available_cash < 0:
            raise ValueError('Cash or pending reservation is invalid')
        keys = [p.instrument.key for p in self.positions]
        if len(keys) != len(set(keys)):
            raise ValueError('Duplicate position rows')

    @property
    def equity(self):
        return self.cash + sum((p.quantity * p.mark for p in self.positions), Decimal(0))

    @property
    def available_cash(self):
        return self.cash - sum((p.cost for p in self.pending), Decimal(0))

    @property
    def risk(self):
        return sum((p.risk for p in self.positions + self.pending), Decimal(0))

    @property
    def occupied(self):
        return {p.instrument.key for p in self.positions + self.pending}


@dataclass(frozen=True)
class Candidate:
    instrument: Instrument
    entry: Decimal
    stop: Decimal
    target: Decimal
    quality: Decimal
    history_score: Decimal
    history_lower: Decimal
    history_samples: int

    def __post_init__(self):
        for name in ('entry', 'stop', 'target', 'quality', 'history_score', 'history_lower'):
            object.__setattr__(self, name, money(getattr(self, name)))
        if not 0 < self.stop < self.entry < self.target or not 0 <= self.quality <= 1 or not 0 <= self.history_lower <= 1:
            raise ValueError('Invalid candidate prices or evidence')
        if isinstance(self.history_samples, bool) or not isinstance(self.history_samples, int) or self.history_samples < 0:
            raise ValueError('Invalid sample count')

    @property
    def rank_key(self):
        return (-self.history_score, -self.history_lower, -self.quality, self.instrument.key)

    @property
    def weight(self):
        return max(Decimal('0.01'), self.history_score) * max(Decimal('0.1'), self.quality)


def historical_stats(returns, hits):
    # Preserve the original portfolio_paper.stats arithmetic, not a win forecast.
    if len(returns) != len(hits) or any(not isinstance(hit, bool) for hit in hits):
        raise ValueError('Invalid historical sample')
    values = [float(money(value)) for value in returns]
    if any(not math.isfinite(value) for value in values):
        raise ValueError('Invalid historical returns')
    n = len(values)
    if n < 20:
        return dict(rated=False, samples=n, score=0.0, hit_fraction=None, lower=0.0)
    p, z = sum(hits) / n, 1.96
    lower = (p + z*z/(2*n) - z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))) / (1 + z*z/n)
    return dict(rated=True, samples=n, score=sum(values)/(n+20), hit_fraction=p, lower=lower)


def eligibility(candidate, policy):
    if candidate.history_samples < policy.min_history_samples:
        return 'insufficient_history'
    if candidate.history_score <= 0:
        return 'nonpositive_historical_score'
    distance = candidate.entry - candidate.stop
    if distance / candidate.entry > policy.position_risk_fraction:
        return 'technical_stop_exceeds_6_percent'
    if (candidate.target - candidate.entry) / distance < policy.min_reward_risk:
        return 'insufficient_reward_risk'
    return None


def allocate(book, candidates, policy):
    if book.equity <= 0 or book.risk > policy.total_risk_limit:
        raise ValueError('Portfolio equity/risk requires review; no new plans')
    candidates = sorted(candidates, key=lambda c: c.rank_key)
    decisions, eligible, seen = [], [], set(book.occupied)
    for candidate in candidates:
        reason = eligibility(candidate, policy)
        if candidate.instrument.key in seen:
            reason = 'already_occupied_or_duplicate'
        seen.add(candidate.instrument.key)
        if reason:
            decisions.append(dict(instrument=candidate.instrument.key, decision='skip', reason=reason))
        else:
            eligible.append(candidate)
    risk_left = policy.total_risk_limit - book.risk
    cash_left = book.available_cash
    slots = max(0, policy.max_positions - len(book.occupied))
    selected = eligible[:slots]
    for candidate in eligible[slots:]:
        decisions.append(dict(instrument=candidate.instrument.key, decision='skip', reason='position_capacity'))
    remaining_weight = sum((c.weight for c in selected), Decimal(0))
    for candidate in selected:
        allowance = risk_left * candidate.weight / remaining_weight
        remaining_weight -= candidate.weight
        distance = candidate.entry - candidate.stop
        qty = min(allowance / distance, cash_left / candidate.entry,
                  book.equity * policy.max_position_fraction / candidate.entry).to_integral_value(rounding=ROUND_FLOOR)
        if qty < 1:
            decisions.append(dict(instrument=candidate.instrument.key, decision='skip', reason='whole_share_does_not_fit_cash_or_risk'))
            continue
        risk, cost = qty * distance, qty * candidate.entry
        cash_left -= cost
        risk_left -= risk
        decisions.append(dict(instrument=candidate.instrument.key, decision='plan', reason='ranked_setup_within_budget',
            quantity=str(qty), entry=str(candidate.entry), stop=str(candidate.stop), target=str(candidate.target),
            notional=str(cost), planned_risk=str(risk), rank_score=str(candidate.history_score)))
    assert book.risk + sum((money(d['planned_risk']) for d in decisions if d['decision'] == 'plan'), Decimal(0)) <= policy.total_risk_limit
    return dict(execution_enabled=False, strategy_equity=str(book.equity), available_cash=str(book.available_cash),
                risk_limit=str(policy.total_risk_limit), existing_planned_risk=str(book.risk),
                remaining_planned_risk=str(risk_left), cash_after_plans=str(cash_left), decisions=decisions)


class IntentLedger:
    """Durable offline reservations. Not a broker fill ledger or an executor."""
    def __init__(self, path):
        self.path = Path(path)
        if not self.path.exists():
            raise ValueError('Ledger missing; explicit initialization required')

    @classmethod
    def initialize(cls, path, policy):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation: an existing or damaged ledger is never reset.
        descriptor = __import__('os').open(path, __import__('os').O_CREAT | __import__('os').O_EXCL | __import__('os').O_WRONLY, 0o600)
        __import__('os').close(descriptor)
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            db.execute('INSERT INTO metadata VALUES (?, ?)', ('starting_equity', str(policy.starting_equity)))
            db.execute('CREATE TABLE intents (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, cash TEXT NOT NULL, risk TEXT NOT NULL, report TEXT NOT NULL)')
        return cls(path)

    def reserve(self, intent_id, report, policy):
        if not isinstance(intent_id, str) or not intent_id or report.get('execution_enabled') is not False:
            raise ValueError('Invalid disabled plan')
        # Phase 1 deliberately supports only initially empty-account reservations.
        # Broker reconciliation/fill consumption must be implemented before trading.
        if money(report['existing_planned_risk']) != 0:
            raise ValueError('Live-position reservation requires future broker reconciliation adapter')
        plans = [d for d in report['decisions'] if d['decision'] == 'plan']
        instruments = set()
        for row in plans:
            exchange, currency, symbol = row['instrument'].split(':')
            instrument = Instrument(symbol, exchange, currency)
            if instrument.key in instruments:
                raise ValueError('Duplicate reservation instrument')
            instruments.add(instrument.key)
            qty, entry, stop, target = [money(row[key]) for key in ('quantity', 'entry', 'stop', 'target')]
            if qty <= 0 or qty != qty.to_integral_value() or not 0 < stop < entry < target:
                raise ValueError('Invalid reservation prices or quantity')
            if money(row['planned_risk']) != qty * (entry - stop) or money(row['notional']) != qty * entry:
                raise ValueError('Reservation financial totals do not match')
            if (entry - stop) / entry > policy.position_risk_fraction or (target - entry) / (entry - stop) < policy.min_reward_risk:
                raise ValueError('Reservation violates position risk or reward policy')
        cash = sum((money(d['notional']) for d in plans), Decimal(0))
        risk = sum((money(d['planned_risk']) for d in plans), Decimal(0))
        if cash < 0 or risk < 0:
            raise ValueError('Invalid reservation')
        text = json.dumps(report, sort_keys=True)
        fingerprint = hashlib.sha256(text.encode()).hexdigest()
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT fingerprint FROM intents WHERE id=?', (intent_id,)).fetchone()
            if existing:
                if existing[0] != fingerprint:
                    raise ValueError('Intent ID reused for a different plan')
                return False
            starting = money(db.execute('SELECT value FROM metadata WHERE key=?', ('starting_equity',)).fetchone()[0])
            rows = db.execute('SELECT cash,risk,report FROM intents').fetchall()
            previous = {d['instrument'] for row in rows for d in json.loads(row[2])['decisions'] if d['decision'] == 'plan'}
            if previous & instruments or len(previous | instruments) > policy.max_positions:
                raise ValueError('Reservation duplicates a symbol or exceeds position capacity')
            if any(money(d['notional']) > starting * policy.max_position_fraction for d in plans):
                raise ValueError('Reservation exceeds initial per-position allocation cap')
            reserved_cash = sum((money(r[0]) for r in rows), Decimal(0))
            reserved_risk = sum((money(r[1]) for r in rows), Decimal(0))
            if reserved_cash + cash > starting or reserved_risk + risk > policy.total_risk_limit:
                raise ValueError('Reservation exceeds remaining cash or combined risk')
            db.execute('INSERT INTO intents VALUES (?, ?, ?, ?, ?)', (intent_id, fingerprint, str(cash), str(risk), text))
        return True
