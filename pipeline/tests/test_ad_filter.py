from __future__ import annotations

from filters.ad_filter import AdFilter
from filters.dedup import DedupIndex, content_hash, normalize_url
from models import RawPost


def make_post(text: str, title: str = "", url: str = "https://t.me/test/1") -> RawPost:
    return RawPost(
        source_identifier="test",
        source_name="Test",
        url=url,
        content_type="telegram",
        title_orig=title or text[:80],
        content_orig=text,
        published_at="2026-11-26T10:00:00Z",
    )


class TestAdFilterHardPatterns:
    def test_erid_blocked(self) -> None:
        f = AdFilter()
        reason = f.check(make_post("Крутой сервис, переходи по ссылке erid: 2Vtzqx12345"))
        assert reason is not None
        assert "erid" in reason.lower()

    def test_reklama_hashtag_blocked(self) -> None:
        f = AdFilter()
        reason = f.check(make_post("Новый гаджет обзор\n#реклама"))
        assert reason is not None
        assert "реклама" in reason.lower()

    def test_inn_blocked(self) -> None:
        f = AdFilter()
        reason = f.check(make_post("ООО Ромашка, ИНН 7712345678, предлагает услуги"))
        assert reason is not None

    def test_promokod_blocked(self) -> None:
        f = AdFilter()
        reason = f.check(make_post("Скидка 50% по промокоду SALE2026 на всё"))
        assert reason is not None

    def test_webinar_blocked(self) -> None:
        f = AdFilter()
        reason = f.check(make_post("Запишись на вебинар по AI-агентам"))
        assert reason is not None

    def test_erid_in_url_blocked(self) -> None:
        f = AdFilter()
        reason = f.check(make_post("Смотрите", url="https://example.com/x?erid=2Vtzqx12345"))
        assert reason is not None

    def test_clean_post_passes(self) -> None:
        f = AdFilter()
        post = make_post(
            "Вышел Python 3.14 с новым JIT-компилятором. "
            "Бенчмарки показывают ускорение на 25% в типичных задачах."
        )
        assert f.check(post) is None

    def test_empty_text_passes(self) -> None:
        f = AdFilter()
        assert f.check(make_post("")) is None

    def test_channel_subscription_footer_passes(self) -> None:
        f = AdFilter()
        post = make_post(
            "Новый open-source инструмент для локального поиска по PDF.\n"
            "Подписывайся на канал, чтобы не пропустить обновления!"
        )
        assert f.check(post) is None


class TestAdFilterCustomPatterns:
    def test_custom_regex_from_settings(self) -> None:
        f = AdFilter(extra_patterns=r"секретный\s+промокод")
        reason = f.check(make_post("Только сегодня секретный промокод внутри"))
        assert reason is not None

    def test_invalid_regex_does_not_crash(self) -> None:
        f = AdFilter(extra_patterns="[unclosed(\nvalid_pattern")
        assert "[unclosed(" in f.invalid_patterns
        # фильтр всё равно работает
        assert f.check(make_post("обычный пост про Rust и borrow checker")) is None

    def test_blacklist_phrases(self) -> None:
        f = AdFilter(extra_blacklist=["крипто-сигналы"])
        reason = f.check(make_post("Лучшие крипто-сигналы дня"))
        assert reason is not None

    def test_filter_splits_correctly(self) -> None:
        f = AdFilter()
        posts = [
            make_post("Полезный разбор архитектуры Kubernetes", url="https://t.me/a/1"),
            make_post("Купи курс по трейдингу", url="https://t.me/a/2"),
            make_post("Релиз Rust 1.90 с новыми фичами", url="https://t.me/a/3"),
        ]
        clean, blocked = f.filter(posts)
        assert len(clean) == 2
        assert len(blocked) == 1
        assert "курс" in blocked[0][1].lower()


class TestDedup:
    def test_normalize_strips_utm(self) -> None:
        a = normalize_url("https://example.com/post?utm_source=tg&utm_medium=social&id=5")
        b = normalize_url("https://example.com/post?id=5")
        assert a == b

    def test_normalize_strips_www_and_trailing_slash(self) -> None:
        assert normalize_url("https://www.Example.com/path/") == "https://example.com/path"

    def test_normalize_keeps_meaningful_params(self) -> None:
        url = normalize_url("https://example.com/watch?v=abc")
        assert "v=abc" in url

    def test_content_hash_stable(self) -> None:
        h1 = content_hash("Заголовок", "Текст статьи про Python")
        h2 = content_hash("Заголовок", "Текст статьи про Python")
        assert h1 == h2
        assert len(h1) == 64

    def test_content_hash_differs(self) -> None:
        assert content_hash("A", "text one") != content_hash("B", "text two")

    def test_dedup_index_url(self) -> None:
        idx = DedupIndex(urls=["https://example.com/post?utm_source=x"])
        assert idx.is_duplicate("https://example.com/post", "somehash")

    def test_dedup_index_hash(self) -> None:
        h = content_hash("t", "c")
        idx = DedupIndex(hashes=[h])
        assert idx.is_duplicate("https://other.com/new", h)

    def test_dedup_new_item_not_duplicate(self) -> None:
        idx = DedupIndex(urls=["https://a.com/1"], hashes=["abc"])
        assert not idx.is_duplicate("https://b.com/2", "def")

    def test_add_after_check(self) -> None:
        idx = DedupIndex()
        assert not idx.is_duplicate("https://a.com/1", "h1")
        idx.add("https://a.com/1", "h1")
        assert idx.is_duplicate("https://a.com/1", "h1")
