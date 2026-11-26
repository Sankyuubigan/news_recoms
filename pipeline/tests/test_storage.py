from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from models import IngestItem, RunStats
from storage import (
    CONFIG_FILE,
    FEED_FILE,
    RUNS_FILE,
    USER_STATE_FILE,
    Storage,
    make_item_id,
)


def _iso(days_ago: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _item(url: str = "https://example.com/a", **overrides) -> IngestItem:
    data = dict(
        source_identifier="https://example.com/feed",
        url=url,
        content_hash="hash-" + url,
        content_type="article",
        title_orig="Title",
        content_orig="Body text",
        title_ru="Заголовок",
        summary_ru="Кратко",
        key_points=["a", "b"],
        category="Разработка",
        tags=["tag"],
        ai_score=8.0,
        status="inbox",
        published_at=_iso(),
    )
    data.update(overrides)
    return IngestItem(**data)


@pytest.fixture()
def store(tmp_path: Path) -> Storage:
    return Storage(tmp_path)


# ---------------------------------------------------------------- init


def test_initializes_all_files_with_defaults(tmp_path: Path) -> None:
    Storage(tmp_path)
    for name in (CONFIG_FILE, FEED_FILE, USER_STATE_FILE, RUNS_FILE):
        assert (tmp_path / name).exists(), f"{name} not created"

    config = json.loads((tmp_path / CONFIG_FILE).read_text(encoding="utf-8"))
    assert config["sources"], "default sources must not be empty"
    assert config["settings"]["min_inbox_score"] == "6.5"
    assert json.loads((tmp_path / FEED_FILE).read_text(encoding="utf-8")) == []
    assert json.loads((tmp_path / USER_STATE_FILE).read_text(encoding="utf-8"))["items"] == {}


def test_missing_settings_keys_are_filled(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILE).write_text(
        json.dumps({"sources": [], "settings": {"manual_rules": "только ИИ"}}), encoding="utf-8"
    )
    store = Storage(tmp_path)
    assert store.settings["manual_rules"] == "только ИИ"
    assert store.settings["learn_feedback_threshold"] == "10"


def test_corrupt_file_is_backed_up(tmp_path: Path) -> None:
    (tmp_path / FEED_FILE).write_text("{not json", encoding="utf-8")
    store = Storage(tmp_path)
    assert store.feed == []
    assert (tmp_path / (FEED_FILE + ".corrupt")).exists()


# ------------------------------------------------------------- ingest


def test_ingest_inserts_and_dedups_by_url(store: Storage) -> None:
    res = store.ingest([_item("https://example.com/a?utm_source=x")])
    assert res["inserted"] == 1

    # тот же URL без трекинга — дубль
    res2 = store.ingest([_item("https://example.com/a")])
    assert res2["inserted"] == 0
    assert len(res2["skipped"]) == 1
    assert len(store.feed) == 1


def test_ingest_dedups_by_content_hash(store: Storage) -> None:
    store.ingest([_item("https://example.com/a", content_hash="same")])
    res = store.ingest([_item("https://example.com/b", content_hash="same")])
    assert res["inserted"] == 0
    assert len(store.feed) == 1


def test_ingest_merges_cluster_into_merged_sources(store: Storage) -> None:
    store.ingest([_item("https://a.com/1", cluster_slug="big-release")])
    res = store.ingest([_item("https://b.com/2", cluster_slug="big-release")])

    assert res["merged"] == 1
    assert len(store.feed) == 1
    merged = store.feed[0]["merged_sources"]
    assert len(merged) == 1
    assert merged[0]["url"] == "https://b.com/2"


def test_pending_ai_item_is_completed_on_rerun(store: Storage) -> None:
    store.ingest([_item("https://example.com/a", status="pending_ai", title_ru=None, ai_score=None)])
    assert store.feed[0]["status"] == "pending_ai"

    res = store.ingest([_item("https://example.com/a", status="inbox")])
    assert res["updated"] == 1
    assert store.feed[0]["status"] == "inbox"
    assert store.feed[0]["title_ru"] == "Заголовок"


def test_alerts_only_for_high_score_inbox(store: Storage) -> None:
    res = store.ingest(
        [
            _item("https://example.com/hot", ai_score=9.5, status="inbox"),
            _item("https://example.com/meh", ai_score=7.0, status="inbox"),
            _item("https://example.com/bad", ai_score=9.9, status="rejected"),
        ]
    )
    assert len(res["alerts"]) == 1
    assert res["alerts"][0]["url"] == "https://example.com/hot"


def test_item_id_is_stable_and_ignores_tracking(store: Storage) -> None:
    a = make_item_id("https://example.com/post?utm_source=tg&fbclid=1")
    b = make_item_id("https://example.com/post")
    assert a == b
    assert len(a) == 16


# --------------------------------------------------- user_state merging


def test_merge_user_state_overrides_status(store: Storage) -> None:
    store.ingest([_item("https://example.com/a")])
    item_id = store.feed[0]["id"]

    store.user_state["items"][item_id] = {
        "status": "archived",
        "feedback": "dislike",
        "feedback_reason": "Реклама",
        "feedback_processed": False,
        "updated_at": _iso(-1),  # будущее — новее элемента
    }
    applied = store.merge_user_state()

    assert applied == 1
    assert store.feed[0]["status"] == "archived"
    assert store.feed[0]["feedback"] == "dislike"
    assert store.feed[0]["feedback_reason"] == "Реклама"


def test_merge_user_state_ignores_older_changes(store: Storage) -> None:
    store.ingest([_item("https://example.com/a")])
    item_id = store.feed[0]["id"]
    store.feed[0]["status"] = "saved"
    store.feed[0]["updated_at"] = _iso()

    store.user_state["items"][item_id] = {
        "status": "archived",
        "updated_at": _iso(5),  # старее
    }
    assert store.merge_user_state() == 0
    assert store.feed[0]["status"] == "saved"


def test_unprocessed_feedback_collects_likes(store: Storage) -> None:
    store.ingest([_item("https://example.com/a"), _item("https://example.com/b")])
    store.feed[0]["feedback"] = "like"
    store.feed[1]["feedback"] = "dislike"
    store.feed[1]["feedback_processed"] = True

    fb = store.unprocessed_feedback()
    assert len(fb) == 1
    assert fb[0]["feedback"] == "like"
    assert fb[0]["id"] == store.feed[0]["id"]


def test_update_profile_marks_feedback_processed(store: Storage) -> None:
    store.ingest([_item("https://example.com/a")])
    item_id = store.feed[0]["id"]
    store.feed[0]["feedback"] = "like"
    store.user_state["items"][item_id] = {"feedback": "like", "updated_at": _iso()}

    store.update_profile("Люблю Go и архитектуру", [item_id])

    assert store.settings["learned_profile"] == "Люблю Go и архитектуру"
    assert store.feed[0]["feedback_processed"] is True
    assert store.user_state["items"][item_id]["feedback_processed"] is True


# ----------------------------------------------------------------- prune


def test_prune_removes_old_rejected_but_keeps_saved(store: Storage) -> None:
    store.ingest(
        [
            _item("https://example.com/old-rejected", status="rejected", published_at=_iso(10)),
            _item("https://example.com/old-saved", status="saved", published_at=_iso(365)),
            _item("https://example.com/fresh", status="inbox", published_at=_iso(0)),
        ]
    )
    removed = store.prune()

    assert removed["rejected"] == 1
    urls = {it["url"] for it in store.feed}
    assert "https://example.com/old-rejected" not in urls
    assert "https://example.com/old-saved" in urls
    assert "https://example.com/fresh" in urls


def test_prune_respects_feed_max_items_but_never_saved(store: Storage) -> None:
    items = [_item(f"https://example.com/{i}", published_at=_iso(i)) for i in range(30)]
    store.ingest(items)
    store.feed[0]["status"] = "saved"

    # Жёстко урезаем лимит через monkeypatch константы
    import storage as storage_mod

    original = storage_mod.FEED_MAX_ITEMS
    storage_mod.FEED_MAX_ITEMS = 10
    try:
        store.prune()
    finally:
        storage_mod.FEED_MAX_ITEMS = original

    assert len(store.feed) == 10
    assert any(it["status"] == "saved" for it in store.feed)


# ------------------------------------------------------------ runs / save


def test_record_run_keeps_last_30(store: Storage) -> None:
    for _ in range(35):
        store.record_run(RunStats(status="success"))
    assert len(store.runs["pipeline_runs"]) == 30
    assert store.runs["pipeline_runs"][-1]["id"] == 35


def test_save_roundtrip(tmp_path: Path) -> None:
    store = Storage(tmp_path)
    store.ingest([_item("https://example.com/a")])
    store.record_run(RunStats(status="success"))
    store.save()

    reloaded = Storage(tmp_path)
    assert len(reloaded.feed) == 1
    assert len(reloaded.runs["pipeline_runs"]) == 1

    # кириллица не должна экранироваться
    raw = (tmp_path / FEED_FILE).read_text(encoding="utf-8")
    assert "Заголовок" in raw


def test_bundle_returns_recent_urls_hashes_and_pending(store: Storage) -> None:
    store.ingest(
        [
            _item("https://example.com/a", status="inbox"),
            _item("https://example.com/p", status="pending_ai"),
            _item("https://example.com/old", status="inbox"),
        ]
    )
    # состариваем fetched_at у третьего элемента — он выпадает из 14-дневного окна дедупа
    store.feed[2]["fetched_at"] = _iso(30)
    bundle = store.bundle()
    assert "https://example.com/a" in bundle.recent_urls
    assert "https://example.com/old" not in bundle.recent_urls
    assert len(bundle.pending_ai_items) == 1
    assert bundle.pending_ai_items[0]["url"] == "https://example.com/p"


def test_update_source_status_writes_last_error(store: Storage) -> None:
    ident = store.config["sources"][0]["identifier"]
    store.update_source_status([{"identifier": ident, "last_error": "timeout", "last_fetched_at": None}])
    assert store.config["sources"][0]["last_error"] == "timeout"
