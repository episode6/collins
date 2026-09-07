"""scripts/run_e2e.py's --shard deal: every check lands in exactly one
shard, the shards are balanced by the weight table, and a check the table
has never heard of still runs somewhere."""

import argparse
import importlib.util
import os

import pytest

_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts", "run_e2e.py",
)
_spec = importlib.util.spec_from_file_location("run_e2e", _PATH)
run_e2e = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_e2e)


def _all_checks():
    return run_e2e.discover(only=[])


def test_shards_partition_the_suite():
    checks = _all_checks()
    assert len(checks) > 4
    dealt = [run_e2e.shard(checks, i, 4) for i in (1, 2, 3, 4)]
    assert sorted(sum(dealt, [])) == sorted(checks)
    # Each shard keeps discover()'s order, so a run prints them the way
    # the unsharded suite would.
    for part in dealt:
        assert part == [p for p in checks if p in part]


def test_shards_balance_by_time():
    checks = _all_checks()
    loads = [
        sum(map(run_e2e.check_seconds, run_e2e.shard(checks, i, 4)))
        for i in (1, 2, 3, 4)
    ]
    # LPT can't beat "heaviest single check" and the table's tail is small;
    # anything wider than the heaviest check is a mis-deal.
    heaviest = max(map(run_e2e.check_seconds, checks))
    assert max(loads) - min(loads) <= heaviest


def test_every_weighted_check_still_exists():
    names = {os.path.basename(p) for p in _all_checks()}
    stale = set(run_e2e.CHECK_SECONDS) - names
    assert not stale, f"CHECK_SECONDS names checks that are gone: {stale}"


def test_unweighted_check_is_dealt_once():
    checks = _all_checks() + [os.path.join(run_e2e.SCRIPTS_DIR, "check_zz_new.py")]
    homes = [i for i in (1, 2, 3, 4) if checks[-1] in run_e2e.shard(checks, i, 4)]
    assert len(homes) == 1
    assert run_e2e.check_seconds(checks[-1]) == run_e2e.DEFAULT_SECONDS


def test_one_shard_is_the_whole_suite():
    checks = _all_checks()
    assert run_e2e.shard(checks, 1, 1) == checks


def test_deal_is_deterministic():
    checks = _all_checks()
    assert run_e2e.shard(checks, 2, 4) == run_e2e.shard(list(checks), 2, 4)
    assert set(run_e2e.shard(checks, 2, 4)) == set(run_e2e.shard(checks[::-1], 2, 4))


@pytest.mark.parametrize("bad", ["0/4", "5/4", "1/0", "x", "1", "1/4/2"])
def test_parse_shard_rejects(bad):
    with pytest.raises(argparse.ArgumentTypeError):
        run_e2e.parse_shard(bad)


def test_parse_shard():
    assert run_e2e.parse_shard("3/4") == (3, 4)
