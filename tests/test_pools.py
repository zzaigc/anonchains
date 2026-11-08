from __future__ import annotations

import time

from anonchains.crypto.vault import Vault
from anonchains.models.proxy_node import Anonymity, Protocol, ProxyNode
from anonchains.pools import CandidatePool, NodeStore, VerifiedPool


def make_node(host: str, port: int = 8080, protocol: Protocol = Protocol.HTTP, score: float = 0.0):
    return ProxyNode(protocol=protocol, host=host, port=port, score=score)


def test_upsert_deduplicates_and_hydrates(tmp_path):
    store = NodeStore(tmp_path / "candidate.db")
    node = make_node("1.2.3.4")
    assert store.upsert_many([node, node]) == 2
    assert store.count() == 1
    loaded = store.get(node.stable_key())
    assert loaded is not None
    assert loaded.host == "1.2.3.4" and loaded.port == 8080
    assert loaded.protocol is Protocol.HTTP
    store.close()


def test_sealed_credentials_and_safe_degradation(vault_path):
    vault = Vault(vault_path)
    vault.initialize("a-strong-passphrase")
    db_path = vault_path.parent / "candidate.db"
    store = NodeStore(db_path, vault)
    node = make_node("8.8.8.8")
    node.credential = {"password": "super-secret-value"}
    node.raw = "http://8.8.8.8:8080"
    store.upsert_many([node])

    loaded = store.get(node.stable_key())
    assert loaded is not None
    assert loaded.credential == {"password": "super-secret-value"}
    assert loaded.raw == node.raw
    assert b"super-secret-value" not in db_path.read_bytes()
    assert store.stats()["sealed"] == 1

    vault.lock()
    degraded = store.get(node.stable_key())
    assert degraded is not None
    assert degraded.credential is None and degraded.raw is None
    store.close()


def test_candidate_and_verified_pools(tmp_path):
    candidate_store = NodeStore(tmp_path / "candidate.db")
    verified_store = NodeStore(tmp_path / "verified.db")
    candidate = CandidatePool(candidate_store)
    verified = VerifiedPool(verified_store)

    nodes = [
        make_node("1.1.1.1", 8080, Protocol.HTTP, 90.0),
        make_node("2.2.2.2", 1080, Protocol.SOCKS5, 70.0),
        make_node("3.3.3.3", 3128, Protocol.HTTP, 60.0),
    ]
    candidate.add(nodes)
    assert candidate.count() == 3
    stale = candidate.take_stale(10, 0)
    assert len(stale) == 3

    verified.upsert(nodes)
    assert verified.count() == 3
    best = verified.best(2, protocols=("http",))
    assert [n.host for n in best] == ["1.1.1.1", "3.3.3.3"]

    front = verified.front_pick(exclude_host="1.1.1.1")
    assert front is not None and front.host != "1.1.1.1"
    assert front.protocol in {Protocol.HTTP, Protocol.HTTPS}

    removed = verified.demote([nodes[0].stable_key()])
    assert len(removed) == 1 and verified.count() == 2
    candidate_store.close()
    verified_store.close()


def test_stale_selection_respects_ttl(tmp_path):
    store = NodeStore(tmp_path / "candidate.db")
    fresh = make_node("1.1.1.1")
    fresh.last_checked_at = time.time()
    old = make_node("2.2.2.2")
    old.last_checked_at = time.time() - 10_000
    store.upsert_many([fresh, old])
    pool = CandidatePool(store)
    due = pool.take_stale(10, 3600)
    assert [n.host for n in due] == ["2.2.2.2"]
    assert len(pool.take_stale(10, 0)) == 2
    store.close()


def test_stats_grouping(tmp_path):
    store = NodeStore(tmp_path / "candidate.db")
    elite = make_node("1.1.1.1", score=88)
    elite.anonymity = Anonymity.ELITE
    store.upsert_many([elite, make_node("2.2.2.2", 1080, Protocol.SOCKS5)])
    stats = store.stats()
    assert stats["total"] == 2
    assert stats["by_protocol"]["http"] == 1
    assert stats["by_anonymity"]["elite"] == 1
    assert stats["avg_score"] > 0
    store.close()
