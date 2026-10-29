"""Synthetic fixtures test budget mechanics, never profitability."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal as D
import copy
import pytest
from trader.core import Policy, Instrument, Exposure, Book, Candidate, allocate, historical_stats, IntentLedger, money


def candidate(symbol='TEST', entry='50', stop='47', target='56', score='1', quality='.8'):
    return Candidate(Instrument(symbol, 'XNAS'), entry, stop, target, quality, score, '.4', 30)


def plans(report):
    return [row for row in report['decisions'] if row['decision'] == 'plan']


def test_starting_equity_is_not_a_permanent_ceiling():
    policy = Policy()
    initial = plans(allocate(Book('2000'), [candidate(entry='10', stop='9.8', target='10.4')], policy))[0]
    grown = plans(allocate(Book('3000'), [candidate(entry='10', stop='9.8', target='10.4')], policy))[0]
    assert D(initial['notional']) == 500
    assert D(grown['notional']) == 750
    assert D(grown['planned_risk']) == 15


def test_positions_count_in_equity_but_not_available_cash():
    position = Exposure(Instrument('HELD', 'XNYS'), '10', '50', '49.5', '60')
    book = Book('1500', (position,))
    assert book.equity == 2100 and book.available_cash == 1500
    assert book.risk == 5


def test_three_positions_share_one_fixed_fifteen_dollar_budget():
    report = allocate(Book('2000'), [candidate('AAA'), candidate('BBB', score='.7'), candidate('CCC', score='.3')], Policy())
    rows = plans(report)
    assert len(rows) == 3
    assert sum(D(row['planned_risk']) for row in rows) <= 15
    assert D(rows[0]['planned_risk']) >= D(rows[-1]['planned_risk'])
    assert all(D(row['planned_risk']) <= D(row['notional']) * D('.06') for row in rows)


def test_pending_orders_reserve_both_cash_and_risk():
    pending = Exposure(Instrument('WAIT', 'XNAS'), '4', '50', '47', '50')
    book = Book('2000', pending=(pending,))
    report = allocate(book, [candidate()], Policy())
    assert book.available_cash == 1800
    assert sum(D(row['planned_risk']) for row in plans(report)) <= 3


def test_no_new_plan_when_combined_risk_used():
    held = Exposure(Instrument('HELD', 'XNAS'), '5', '50', '47', '50')
    report = allocate(Book('1750', (held,)), [candidate()], Policy())
    assert not plans(report)


def test_wide_stop_is_not_tightened_to_force_trade():
    report = allocate(Book('2000'), [candidate(stop='46')], Policy())
    assert report['decisions'][0]['reason'] == 'technical_stop_exceeds_6_percent'


def test_stop_preserved_and_quantity_reduced():
    row = plans(allocate(Book('2000'), [candidate()], Policy()))[0]
    assert row['stop'] == '47' and row['quantity'] == '5'
    assert row['notional'] == '250' and row['planned_risk'] == '15'


def test_growth_does_not_raise_combined_risk_limit():
    report = allocate(Book('20000'), [candidate('AAA'), candidate('BBB')], Policy())
    assert sum(D(row['planned_risk']) for row in plans(report)) <= 15


def test_losses_reduce_allocation_cap():
    row = plans(allocate(Book('1000'), [candidate(entry='10', stop='9.8', target='10.4')], Policy()))[0]
    assert D(row['notional']) <= 250


def test_unrealized_profit_never_offsets_other_risk():
    profitable = Exposure(Instrument('WIN', 'XNAS'), '10', '10', '12', '15')
    risky = Exposure(Instrument('RISK', 'XNAS'), '5', '50', '47', '50')
    assert Book('1600', (profitable, risky)).risk == 15


def test_duplicate_and_occupied_instruments_skipped():
    held = Exposure(Instrument('TEST', 'XNAS'), '1', '50', '49', '50')
    assert not plans(allocate(Book('1950', (held,)), [candidate(), candidate()], Policy()))


def test_unrated_and_nonpositive_history_not_auto_planned():
    rows = [replace(candidate('AAA'), history_samples=19), candidate('BBB', score='-.1')]
    assert not plans(allocate(Book('2000'), rows, Policy()))


def test_historical_stats_preserve_original_shrinkage():
    assert not historical_stats([1] * 19, [True] * 19)['rated']
    result = historical_stats([1] * 20, [True] * 20)
    assert result['rated'] and result['score'] == .5


@pytest.mark.parametrize('value', ['NaN', 'Infinity', True])
def test_invalid_money_rejected(value):
    with pytest.raises(ValueError):
        money(value)


def test_tsx_and_live_execution_not_enabled():
    with pytest.raises(ValueError):
        Instrument('TEST', 'XTSE', 'CAD')
    with pytest.raises(ValueError):
        Policy(execution_enabled=True)


def test_zero_cash_cannot_be_replaced_by_unrealized_equity():
    held = Exposure(Instrument('HELD', 'XNAS'), '10', '50', '50', '60')
    assert not plans(allocate(Book('0', (held,)), [candidate()], Policy()))


def test_risk_breach_stops_new_planning():
    held = Exposure(Instrument('HELD', 'XNAS'), '10', '50', '47', '50')
    with pytest.raises(ValueError):
        allocate(Book('1500', (held,)), [candidate()], Policy())


def test_ledger_never_auto_resets_and_reservations_are_idempotent(tmp_path):
    policy = Policy()
    path = tmp_path / 'intents.sqlite'
    with pytest.raises(ValueError):
        IntentLedger(path)
    ledger = IntentLedger.initialize(path, policy)
    with pytest.raises(FileExistsError):
        IntentLedger.initialize(path, policy)
    report = allocate(Book('2000'), [candidate()], policy)
    assert ledger.reserve('first', report, policy)
    assert not ledger.reserve('first', report, policy)
    changed = allocate(Book('2000'), [candidate('OTHER')], policy)
    with pytest.raises(ValueError):
        ledger.reserve('first', changed, policy)


def test_ledger_serializes_competing_reservations(tmp_path):
    policy = Policy()
    ledger = IntentLedger.initialize(tmp_path / 'intents.sqlite', policy)
    def reserve(symbol):
        report = allocate(Book('2000'), [candidate(symbol)], policy)
        try:
            return ledger.reserve(symbol, report, policy)
        except ValueError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(reserve, ['AAA', 'BBB'])) == 1


def test_ledger_independently_rejects_forged_risk_totals(tmp_path):
    policy = Policy()
    ledger = IntentLedger.initialize(tmp_path / 'intents.sqlite', policy)
    report = allocate(Book('2000'), [candidate()], policy)
    report['decisions'][0]['planned_risk'] = '0'
    with pytest.raises(ValueError):
        ledger.reserve('forged', report, policy)
