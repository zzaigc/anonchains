from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from anonchains.models.proxy_node import Anonymity, Protocol, ProxyNode
from anonchains.pools import CandidatePool, NodeStore, VerifiedPool
from anonchains.scheduler import Scheduler
from anonchains.validator.anonymity import (
    EchoChecker,
    classify,
    compute_score,
    extract_ip,
)
from anonchains.validator.batch import ValidationResult, Validator, summarise_validation
from anonchains.validator.probes import probe_node


def test_extract_ip_from_json_and_plain_text():
    assert extract_ip('{"ip":"203.0.113.7"}') == "203.0.113.7"
    assert extract_ip('{"origin":"198.51.100.2, 198.51.100.2"}') == "198.51.100.2"
    assert extract_ip("your address is 192.0.2.55\n") == "192.0.2.55"
    assert extract_ip("no address here") is None


def test_classify_anonymity_grades():
    assert classify("{}", {}, "203.0.113.7", "198.51.100.1") == (Anonymity.ELITE, True)
    assert classify("{}", {"via": "1.1"}, "203.0.113.7", "198.51.100.1") == (
        Anonymity.ANONYMOUS, True
    )
    grade, effective = classify("{}", {}, "198.51.100.1", "198.51.100.1")
    assert grade is Anonymity.TRANSPARENT and not effective


def test_score_bounds_and_latency_penalty():
    fast = compute_score(50, Anonymity.ELITE, 3, 0)
    slow = compute_score(3000, Anonymity.ELITE, 3, 0)
    failing = compute_score(50, Anonymity.UNKNOWN, 0, 9)
    assert 0 <= failing < slow < fast <= 100


def test_apply_updates_statistics(config_copy):
    node = ProxyNode(protocol=Protocol.HTTP, host="1.2.3.4", port=8080)
    ok = ValidationResult(node=node, ok=True, latency_ms=120.0, anonymity=Anonymity.ELITE)
    updated = Validator.apply(ok)
    score_after_ok = updated.score
    assert updated.success_count == 1 and updated.fail_count == 0
    assert score_after_ok > 0 and updated.anonymity is Anonymity.ELITE
    assert updated.last_checked_at is not None

    other = ProxyNode(protocol=Protocol.HTTP, host="5.6.7.8", port=8080, score=score_after_ok)
    degraded = Validator.apply(ValidationResult(node=other, ok=False, reason="timeout"))
    assert degraded.fail_count == 1 and degraded.score < score_after_ok


def test_external_protocols_are_skipped_without_network(config_copy):
    node = ProxyNode(protocol=Protocol.VLESS, host="1.2.3.4", port=443)
    probe = asyncio.run(probe_node(node, 0.1))
    assert probe.skipped and not probe.ok

    checker = EchoChecker(("https://example.test/echo",), 0.1)
    echo = asyncio.run(checker.check(node))
    assert echo.skipped


def test_validation_summary():
    node = ProxyNode(protocol=Protocol.HTTP, host="1.2.3.4", port=8080)
    results = [
        ValidationResult(node=node, ok=True),
        ValidationResult(node=node, ok=True, skipped=True),
        ValidationResult(node=node, ok=False, reason="x"),
    ]
    assert summarise_validation(results) == {
        "total": 3, "passed": 1, "external_only": 1, "failed": 1
    }


def test_scheduler_promote_demote_and_drop(tmp_path, config_copy):
    candidate_store = NodeStore(tmp_path / "candidate.db")
    verified_store = NodeStore(tmp_path / "verified.db")
    candidate = CandidatePool(candidate_store)
    verified = VerifiedPool(verified_store)

    scheduler = Scheduler(
        config_copy.config,
        crawler=MagicMock(),
        validator=Validator(config_copy.config),
        candidate_pool=candidate,
        verified_pool=verified,
    )

    good = ProxyNode(protocol=Protocol.HTTP, host="1.1.1.1", port=8080)
    good_result = ValidationResult(
        node=good, ok=True, latency_ms=100.0, anonymity=Anonymity.ELITE
    )
    bad = ProxyNode(protocol=Protocol.HTTP, host="2.2.2.2", port=8080)
    bad.fail_count = config_copy.config.validator.max_consecutive_failures
    bad_result = ValidationResult(node=bad, ok=False, reason="dead")

    promoted, demoted, removed = scheduler._apply_results([good_result, bad_result])
    assert promoted == 1 and removed >= 0
    assert verified.count() == 1
    assert verified.best(1)[0].host == "1.1.1.1"

    # A failed verified node is demoted back into the candidate pool.
    verified.upsert([good])
    failed_verified = ValidationResult(
        node=ProxyNode(protocol=Protocol.HTTP, host="1.1.1.1", port=8080),
        ok=False,
        reason="timeout",
    )
    _, demoted_count, _ = scheduler._apply_results([failed_verified])
    assert demoted_count == 1
    assert verified.count() == 0
    assert candidate.count() >= 1

    candidate_store.close()
    verified_store.close()
