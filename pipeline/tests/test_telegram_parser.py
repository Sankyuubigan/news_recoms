from __future__ import annotations

from unittest.mock import patch

from parsers.base import ParseError, extract_repo_url, make_title
from parsers.telegram import TelegramParser
from models import BackfillConfig, SourceConfig

SOURCE = SourceConfig(id=1, type="telegram", name="Test Channel", identifier="testchannel")

# Разметка, имитирующая https://t.me/s/<handle>
FIXTURE_HTML = """
<!DOCTYPE html>
<html><body>
<div class="tgme_widget_message_wrap js-widget_message_wrap">
  <div class="tgme_widget_message" data-post="testchannel/101">
    <div class="tgme_widget_message_text js-message_text">
      Вышел новый фреймворк для<br/>агентных систем на Rust.
      Подробности в блоге.
    </div>
    <a class="tgme_widget_message_photo_wrap" style="background-image:url('https://cdn.telesco.pe/file/abc.jpg')"></a>
    <a class="tgme_widget_message_date" href="https://t.me/testchannel/101">
      <time datetime="2026-11-26T08:15:00+00:00"></time>
    </a>
  </div>
</div>

<div class="tgme_widget_message_wrap js-widget_message_wrap">
  <div class="tgme_widget_message" data-post="testchannel/102">
    <div class="tgme_widget_message_text js-message_text">
      Подборка интересных статей про компиляторы и оптимизации.
    </div>
    <a class="tgme_widget_message_date" href="https://t.me/testchannel/102">
      <time datetime="2026-11-26T09:30:00+00:00"></time>
    </a>
  </div>
</div>

<div class="tgme_widget_message_wrap js-widget_message_wrap">
  <div class="tgme_widget_message" data-post="testchannel/103">
    <!-- сервисное сообщение без текста -->
    <div class="tgme_widget_message_service">Channel created</div>
    <a class="tgme_widget_message_date" href="https://t.me/testchannel/103">
      <time datetime="2026-11-26T07:00:00+00:00"></time>
    </a>
  </div>
</div>
</body></html>
"""


class TestTelegramParser:
    def test_parses_messages(self) -> None:
        parser = TelegramParser()
        posts = parser.parse_html(FIXTURE_HTML, SOURCE)
        assert len(posts) == 2  # сервисное сообщение пропущено

    def test_url_and_external_id(self) -> None:
        parser = TelegramParser()
        posts = parser.parse_html(FIXTURE_HTML, SOURCE)
        assert posts[0].url == "https://t.me/testchannel/101"
        assert posts[0].external_id == "101"
        assert posts[1].external_id == "102"

    def test_text_extracted_with_line_breaks(self) -> None:
        parser = TelegramParser()
        posts = parser.parse_html(FIXTURE_HTML, SOURCE)
        assert "агентных систем" in posts[0].content_orig
        assert "Rust" in posts[0].content_orig

    def test_published_at_normalized(self) -> None:
        parser = TelegramParser()
        posts = parser.parse_html(FIXTURE_HTML, SOURCE)
        assert posts[0].published_at == "2026-11-26T08:15:00Z"

    def test_media_url_extracted(self) -> None:
        parser = TelegramParser()
        posts = parser.parse_html(FIXTURE_HTML, SOURCE)
        assert posts[0].media_url == "https://cdn.telesco.pe/file/abc.jpg"
        assert posts[1].media_url is None

    def test_content_type_and_source(self) -> None:
        parser = TelegramParser()
        posts = parser.parse_html(FIXTURE_HTML, SOURCE)
        assert posts[0].content_type == "telegram"
        assert posts[0].source_identifier == "testchannel"
        assert posts[0].source_name == "Test Channel"

    def test_title_is_first_sentence(self) -> None:
        parser = TelegramParser()
        posts = parser.parse_html(FIXTURE_HTML, SOURCE)
        assert posts[0].title_orig.startswith("Вышел новый фреймворк")
        assert len(posts[0].title_orig) <= 111

    def test_empty_html_raises(self) -> None:
        parser = TelegramParser()
        try:
            parser.parse_html("<html><body>no messages</body></html>", SOURCE)
            assert False, "expected ParseError"
        except ParseError:
            pass

    def test_limit_applied(self) -> None:
        parser = TelegramParser()
        posts = parser.parse_html(FIXTURE_HTML, SOURCE, limit=1)
        assert len(posts) == 1
        assert posts[0].external_id == "102"  # последний пост


class TestMakeTitle:
    def test_first_sentence(self) -> None:
        assert make_title("Первое предложение тут. Второе предложение.") == "Первое предложение тут."

    def test_long_text_truncated(self) -> None:
        long_text = "слово " * 60
        title = make_title(long_text, limit=50)
        assert len(title) <= 52
        assert title.endswith("…")

    def test_empty(self) -> None:
        assert make_title("") == ""


class TestTelegramLinksAndBackfill:
    def test_extracts_hidden_github_href(self) -> None:
        html = """
        <html><body>
        <div class="tgme_widget_message_wrap">
          <div class="tgme_widget_message" data-post="reposch/500">
            <div class="tgme_widget_message_text">
              Отличный инструмент для анализа логов.
              <a href="https://github.com/acme/log-viewer/tree/main">GitHub</a>
            </div>
            <a class="tgme_widget_message_date" href="https://t.me/reposch/500">
              <time datetime="2026-11-26T10:00:00+00:00"></time>
            </a>
          </div>
        </div>
        </body></html>
        """
        src = SourceConfig(id=2, type="telegram", kind="repos", name="Repos", identifier="reposch")
        posts = TelegramParser().parse_html(html, src)
        assert len(posts) == 1
        assert posts[0].kind == "repos"
        assert posts[0].repo_url == "https://github.com/acme/log-viewer"
        assert "https://github.com/acme/log-viewer/tree/main" in posts[0].content_orig

    def test_extract_repo_url_filters_non_repo_paths(self) -> None:
        assert extract_repo_url("https://github.com/trending/python") is None
        assert extract_repo_url("https://github.com/org/tool.git") == "https://github.com/org/tool"
        assert extract_repo_url("https://codeberg.org/dev/app") == "https://codeberg.org/dev/app"

    def test_backfill_paginates_and_stops_at_id(self) -> None:
        page1 = """
        <html><head><link rel="prev" href="/s/reposch?before=100"></head><body>
        <div class="tgme_widget_message_wrap"><div class="tgme_widget_message" data-post="reposch/100">
          <div class="tgme_widget_message_text">Пост 100</div>
          <a class="tgme_widget_message_date" href="https://t.me/reposch/100"><time datetime="2026-11-20T10:00:00Z"></time></a>
        </div></div>
        <div class="tgme_widget_message_wrap"><div class="tgme_widget_message" data-post="reposch/101">
          <div class="tgme_widget_message_text">Пост 101</div>
          <a class="tgme_widget_message_date" href="https://t.me/reposch/101"><time datetime="2026-11-21T10:00:00Z"></time></a>
        </div></div>
        </body></html>
        """
        page2 = """
        <html><body>
        <div class="tgme_widget_message_wrap"><div class="tgme_widget_message" data-post="reposch/98">
          <div class="tgme_widget_message_text">Пост 98</div>
          <a class="tgme_widget_message_date" href="https://t.me/reposch/98"><time datetime="2026-11-18T10:00:00Z"></time></a>
        </div></div>
        <div class="tgme_widget_message_wrap"><div class="tgme_widget_message" data-post="reposch/99">
          <div class="tgme_widget_message_text">Пост 99</div>
          <a class="tgme_widget_message_date" href="https://t.me/reposch/99"><time datetime="2026-11-19T10:00:00Z"></time></a>
        </div></div>
        </body></html>
        """
        src = SourceConfig(id=2, type="telegram", kind="repos", name="Repos", identifier="reposch")
        cfg = BackfillConfig(enabled=True, stop_at_id=98, batch_limit=10)
        parser = TelegramParser(page_delay=0.0)

        def fake_fetch(url: str) -> str:
            return page2 if "before=100" in url else page1

        with patch.object(parser, "fetch", side_effect=fake_fetch):
            posts, next_cfg = parser.parse_backfill(src, cfg)

        ids = [p.external_id for p in posts]
        assert ids == ["101", "100", "99"]
        assert next_cfg.enabled is False
        assert next_cfg.cursor_id == 98
        assert next_cfg.processed_total == 3
