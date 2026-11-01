from __future__ import annotations

import math
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

import httpx
import pytest

from research_digest import agent, llm, pipeline, reading, sources
from research_digest.config import (
    AgentConfig,
    FieldConfig,
    ModelConfig,
    RankingConfig,
    SourcesConfig,
    load_config,
)
from research_digest.filtering import normalize, prefilter, reported_ids
from research_digest.items import Item, Scores
from research_digest.note import note_name, render_note, write_note
from research_digest.ranking import attention_value, field_of, lane_of, rank_items, shortlist
from research_digest.reading import Claim, Reading, Summary, check_claims
from research_digest.sources import parse_hf_daily, parse_hf_model

EXAMPLE = Path(__file__).parents[1] / "config.example.toml"
RANKING = RankingConfig(
    signal_weights={"hf_upvotes": 1.0},
    kind_slots={"blog": 1},
    min_attention=10.0,
    attention_weight=0.6,
    content_weight=0.4,
    card_count=4,
    global_slots=1,
    novelty_slots=1,
)
TODAY = date(2026, 11, 1)


def make_item(identifier: str, **values: object) -> Item:
    defaults: dict[str, object] = {
        "id": identifier,
        "kind": "paper",
        "title": identifier,
        "published": "2026-10-31",
        "url": f"https://example.com/{identifier}",
    }
    return Item.model_validate(defaults | values)


def scored(identifier: str, field: str, content: int, upvotes: float = 0.0) -> Item:
    return make_item(
        identifier,
        signals={"hf_upvotes": upvotes},
        scores=Scores(novelty=content, impact=content, credibility=content, field=field, reason=""),
    )


ARXIV_FEED = """
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
<opensearch:totalResults>{total}</opensearch:totalResults>{entries}</feed>
"""
ARXIV_ENTRY = """
<entry><id>http://arxiv.org/abs/{arxiv_id}v2</id><published>2026-10-30T10:00:00Z</published>
<title>A   title</title><summary> An abstract. </summary>
<author><name>Jan Doe</name></author>
<category term="cs.CV"/><category term="cs.SD"/></entry>
"""


def arxiv_feed(total: int, *arxiv_ids: str) -> str:
    entries = "".join(ARXIV_ENTRY.format(arxiv_id=arxiv_id) for arxiv_id in arxiv_ids)
    return ARXIV_FEED.format(total=total, entries=entries)


def test_arxiv_query_pages_and_records_a_failed_category(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sources.time, "sleep", lambda _: None)
    pages = {"0": arxiv_feed(2, "2610.00001"), "1": arxiv_feed(2, "2610.00002")}

    def respond(request: httpx.Request) -> httpx.Response:
        query = request.url.params["search_query"]
        if query.startswith("cat:cs.SD"):
            return httpx.Response(503)
        assert "submittedDate:[202610210000 TO 202610312359]" in query
        return httpx.Response(200, text=pages[request.url.params["start"]])

    config = load_config(EXAMPLE)
    config = replace(
        config,
        fields=(
            FieldConfig("Vision", ("cs.CV",), 1),
            FieldConfig("Speech", ("cs.SD",), 1),
        ),
    )
    gaps: dict[str, str] = {}
    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        items = sources.arxiv_records(http, config, date(2026, 10, 24), date(2026, 10, 31), gaps)
    assert [item.id for item in items] == ["arxiv:2610.00001", "arxiv:2610.00002"]
    assert items[0].title == "A title"
    assert items[0].authors == ["Jan Doe"]
    assert items[0].categories == ["cs.CV", "cs.SD"]
    assert list(gaps) == ["arxiv cs.SD"]


def test_arxiv_empty_page_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sources.time, "sleep", lambda _: None)
    replies = [arxiv_feed(1), arxiv_feed(1, "2610.00001")]

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=replies.pop(0))

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        entries, total = sources._arxiv_page(http, {"start": "0"})
    assert (len(entries), total) == (1, 1)


def test_field_follows_the_primary_listed_category() -> None:
    fields = [FieldConfig("Vision", ("cs.CV",), 1), FieldConfig("Speech", ("cs.SD",), 1)]
    item = make_item("a", categories=["cs.DS", "cs.SD", "cs.CV"])
    assert field_of(item, fields) == "Speech"
    assert field_of(make_item("b", categories=["cs.DS"]), fields) is None


def test_lookup_finds_papers_without_categories() -> None:
    known = make_item("arxiv:2610.00001", categories=["cs.CV"])
    same_from_hf = make_item("arxiv:2610.00001")
    only_hf = make_item("arxiv:2610.00002")
    blog = make_item("https://example.com/post", kind="blog")
    found = sources._uncategorized_papers([known, same_from_hf, only_hf, blog])
    assert found == ["2610.00002"]


def test_paper_text_parses_the_path_from_papers(monkeypatch: pytest.MonkeyPatch) -> None:
    replies = {
        "download": {"data": {"format": "pdf"}, "ok": True},
        "path": {"data": "/store/paper.pdf", "ok": True},
    }
    monkeypatch.setattr(reading, "_papers", lambda command, ref: replies[command])
    monkeypatch.setattr(reading.pymupdf4llm, "to_markdown", lambda path, show_progress: path)
    assert reading._paper_text(make_item("arxiv:2610.00001")) == "/store/paper.pdf"


def test_paper_text_reports_a_failed_download(monkeypatch: pytest.MonkeyPatch) -> None:
    reply = {"error": {"code": "format_unavailable"}, "ok": False}
    monkeypatch.setattr(reading, "_papers", lambda command, ref: reply)
    with pytest.raises(ValueError, match="format_unavailable"):
        reading._paper_text(make_item("doi:10.64898/2026.05.18.26353508"))


def test_claims_verify_through_parsed_pdf_markup() -> None:
    source = "The first bug, _Softmax Collapse_ , solves 99 _._ 6% of e<sup>_zk_</sup> cases."
    summary = Summary(
        what_it_is="",
        method="",
        results="",
        limitations="",
        why_it_matters="",
        key_claims=[
            Claim(claim="a", quote="The first bug, Softmax Collapse, solves 99.6% of ezk cases."),
            Claim(claim="b", quote="The second bug, Softmax Cancellation, solves nothing at all."),
        ],
    )
    assert [claim.verified for claim in check_claims(summary, source)] == [True, False]


def test_reported_ids_survive_obsidian_rewriting_the_list(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text('---\nreported: ["arxiv:1"]\n---\nreported: nothing\n')
    (tmp_path / "b.md").write_text("---\nreported:\n  - arxiv:2\nweek: 2026-W44\n---\n")
    (tmp_path / "c.md").write_text("no properties\n")
    (tmp_path / "d.md").write_text("---\nbroken: [\n---\n")
    assert reported_ids(tmp_path) == {"arxiv:1", "arxiv:2"}


def test_another_run_in_the_week_keeps_the_existing_note(tmp_path: Path) -> None:
    parent = tmp_path / "Parent.md"
    parent.write_text("")
    first = write_note(tmp_path / "notes", parent, TODAY, "first")
    assert write_note(tmp_path / "notes", parent, TODAY, "again", first) == first
    second = write_note(tmp_path / "notes", parent, TODAY, "second")
    assert second.stem == f"{note_name(TODAY)} 2"
    assert first.read_text() == "again"
    assert f"[[{second.stem}]]" in parent.read_text()


def test_select_ignores_unknown_items(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(agent, "call_agent", lambda *_: '{"selected": ["ghost", "a", "a"]}')
    config = load_config(EXAMPLE).agent
    assert agent.select(config, [{"id": "a"}], 5, tmp_path) == ["a"]
    monkeypatch.setattr(agent, "call_agent", lambda *_: '{"selected": ["ghost"]}')
    with pytest.raises(agent.AgentError):
        agent.select(config, [{"id": "a"}], 5, tmp_path)


def test_arxiv_malformed_page_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sources.time, "sleep", lambda _: None)
    replies = ["<html>", arxiv_feed(1, "2610.00001")]

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=replies.pop(0))

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        entries, _ = sources._arxiv_page(http, {"start": "0"})
    assert len(entries) == 1


def test_hf_daily_reads_signals_and_repository() -> None:
    item = parse_hf_daily(
        {
            "numComments": 2,
            "paper": {
                "id": "2610.00001",
                "title": "T",
                "summary": "S",
                "publishedAt": "2026-10-23T00:00:00.000Z",
                "upvotes": 106,
                "githubRepo": "https://github.com/Owner/Repo",
                "githubStars": 36,
            },
        }
    )
    assert item.repo == "owner/repo"
    assert item.signals == {"hf_upvotes": 106.0, "hf_comments": 2.0, "github_stars": 36.0}


def test_hf_model_skips_quantized_copies() -> None:
    assert parse_hf_model({"id": "a/b", "tags": ["gguf"]}) is None
    model = parse_hf_model({"id": "a/b", "tags": ["arxiv:2610.00001"], "likes": 5})
    assert model is not None
    assert model.arxiv_refs == ["arxiv:2610.00001"]


def test_normalize_merges_records_repositories_and_models() -> None:
    paper = make_item("arxiv:1", repo="o/r", signals={"hf_upvotes": 5.0}, sources=["hf_daily"])
    duplicate = make_item("arxiv:1", abstract="Longer", signals={"hn_points": 9.0})
    repo = make_item("gh:o/r", kind="repo", repo="o/r", signals={"github_stars": 7.0})
    model = make_item("hf:o/m", kind="model", arxiv_refs=["arxiv:1"], signals={"model_likes": 3.0})
    other = make_item("gh:x/y", kind="repo", repo="x/y")
    merged = {item.id: item for item in normalize([paper, duplicate, repo, model, other])}
    assert set(merged) == {"arxiv:1", "gh:x/y"}
    assert merged["arxiv:1"].abstract == "Longer"
    assert merged["arxiv:1"].signals == {
        "hf_upvotes": 5.0,
        "hn_points": 9.0,
        "github_stars": 7.0,
        "model_likes": 3.0,
    }


def test_prefilter_drops_reported_and_excluded_items() -> None:
    sources = SourcesConfig(
        attention_window_days=28,
        arrival_grace_days=3,
        exclude_keywords=("Survey",),
        preprint_servers=(),
        preprint_categories=(),
        preprint_keywords=(),
        hf_model_limit=1,
        hn_min_points=1,
        github_topics=(),
        github_limit=1,
        blogs=(),
    )
    items = [make_item("a"), make_item("b"), make_item("c", title="A survey of things")]
    assert [item.id for item in prefilter(items, sources, {"a"})] == ["b"]


def test_attention_is_divided_by_age() -> None:
    fresh = make_item("a", published="2026-11-01", signals={"hf_upvotes": 100.0})
    old = make_item("b", published="2026-10-23", signals={"hf_upvotes": 100.0})
    assert attention_value(fresh, RANKING, TODAY) == 100.0
    assert attention_value(old, RANKING, TODAY) == pytest.approx(100.0 / math.sqrt(10))


def test_old_items_stay_only_while_they_hold_attention() -> None:
    fresh_after = date(2026, 10, 20)
    old_hot = make_item("a", published="2026-10-05", signals={"hf_upvotes": 500.0})
    old_quiet = make_item("b", published="2026-10-05", signals={"hf_upvotes": 5.0})
    new_quiet = make_item("c", published="2026-10-30")
    assert lane_of(old_hot, RANKING, TODAY, fresh_after) == "attention"
    assert lane_of(old_quiet, RANKING, TODAY, fresh_after) is None
    assert lane_of(new_quiet, RANKING, TODAY, fresh_after) == "novelty"


def test_shortlist_fills_from_attention_with_guaranteed_slots() -> None:
    fields = (
        FieldConfig("Big", ("cs.LG",), 1),
        FieldConfig("Small", ("q-fin.ST",), 1),
    )
    blog = scored("blog", "Big", 2)
    blog.kind = "blog"
    items = [
        scored("hot", "Big", 5, upvotes=1000.0),
        scored("big-new", "Big", 9),
        scored("small", "Small", 1),
        blog,
        scored("warm", "Big", 5, upvotes=100.0),
        scored("filler", "Big", 2),
    ]
    ranked = rank_items(items, RANKING, TODAY, date(2026, 10, 20))
    assert {item.id: item.lane for item in ranked}["hot"] == "attention"
    chosen = {item.id for item in shortlist(ranked, fields, replace(RANKING, card_count=5))}
    assert chosen == {"hot", "warm", "small", "blog", "big-new"}


def test_rank_handles_no_attention() -> None:
    ranked = rank_items([scored("a", "Big", 5)], RANKING, TODAY, date(2026, 10, 20))
    assert ranked[0].rank_score == pytest.approx(0.5)


def test_agent_command_gets_prompt_and_reply_loses_its_fence(tmp_path: Path) -> None:
    script = "import sys; print('```json\\n' + sys.stdin.read().upper() + '\\n```')"
    config = AgentConfig((sys.executable, "-c", script), "English")
    assert agent.call_agent(config, "hello", tmp_path) == "HELLO"


def test_agent_command_failure_is_reported(tmp_path: Path) -> None:
    config = AgentConfig((sys.executable, "-c", "import sys; sys.exit(3)"), "English")
    with pytest.raises(agent.AgentError, match="status 3"):
        agent.call_agent(config, "hello", tmp_path)


def test_invalid_agent_reply_is_retried_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    replies = iter(["not json", '{"selected": ["a"]}'])
    monkeypatch.setattr(agent, "call_agent", lambda *_: next(replies))
    config = AgentConfig(("unused",), "English")
    assert agent.select(config, [{"id": "a"}], 5, tmp_path) == ["a"]


def test_note_without_sections_fails_after_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str] = []

    def reply(*_: object) -> str:
        calls.append("called")
        return "no sections"

    monkeypatch.setattr(agent, "call_agent", reply)
    with pytest.raises(agent.AgentError):
        agent.write_note(AgentConfig(("unused",), "English"), {}, tmp_path)
    assert len(calls) == 2


@pytest.mark.parametrize(
    ("handler", "message"),
    [
        (httpx.ConnectError("refused"), "host not reachable"),
        (httpx.Response(503), "endpoint not available"),
        (httpx.Response(200, text="not json"), "endpoint not available"),
        (httpx.Response(200, json={"data": [{"id": "other"}]}), "not served"),
    ],
)
def test_unavailable_model_reports_the_reason(
    monkeypatch: pytest.MonkeyPatch,
    handler: httpx.Response | Exception,
    message: str,
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if isinstance(handler, Exception):
            raise handler
        return handler

    client = httpx.Client
    monkeypatch.setattr(
        llm.httpx,
        "Client",
        lambda **options: client(transport=httpx.MockTransport(respond), **options),
    )
    with pytest.raises(llm.ModelUnavailable, match=message):
        llm.check_available(ModelConfig("http://host/v1", "wanted", 1))


def test_evidence_check_tolerates_formatting_but_not_invention() -> None:
    summary = Summary(
        what_it_is="",
        method="",
        results="",
        limitations="",
        why_it_matters="",
        key_claims=[
            Claim(claim="real", quote="Accuracy reached 97.5 percent on the held-out set"),
            Claim(claim="invented", quote="Accuracy reached 99.9 percent on the held-out set"),
            Claim(claim="too short", quote="Accuracy"),
        ],
    )
    source = "**Accuracy** reached 97.5 per-\ncent on the\nheld-out set."
    assert [claim.verified for claim in check_claims(summary, source)] == [True, False, False]


def make_reading(identifier: str) -> Reading:
    empty = Summary(what_it_is="", method="", results="", limitations="", why_it_matters="")
    return Reading(
        id=identifier,
        title="A | B",
        url="https://example.com",
        summary=empty,
        claims=[],
        truncated=False,
    )


def test_note_records_reported_ids_for_later_runs(tmp_path: Path) -> None:
    parent = tmp_path / "Calendar.md"
    parent.write_text("## 2026\n")
    day = date(2026, 11, 1)
    content = render_note(day, "## Top developments\nText", [make_reading("arxiv:1")], parent)
    path = write_note(tmp_path / "notes", parent, day, content)
    assert path.name == f"{note_name(day)}.md"
    assert content.startswith('---\nup: "[[Calendar]]"\n')
    assert "A \\| B" in content
    assert reported_ids(tmp_path / "notes") == {"arxiv:1"}

    write_note(tmp_path / "notes", parent, day, content)
    assert parent.read_text().count(f"[[{note_name(day)}]]") == 1


def test_note_is_named_after_the_week_it_covers() -> None:
    monday = date(2026, 11, 2)
    assert note_name(monday) == "2026-W44 Research Digest"
    assert "week: 2026-W44" in render_note(monday, "body", [], Path("Parent.md"))
    assert note_name(date(2026, 11, 3)) == "2026-W45 Research Digest"


def test_note_requires_an_existing_parent(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        write_note(tmp_path, tmp_path / "missing.md", TODAY, "body")


def test_this_week_resumes_and_finished_run_sets_next_start(tmp_path: Path) -> None:
    first = pipeline.choose_run(tmp_path, date(2026, 10, 25), None, None)
    assert first.manifest.since == "2026-10-18"
    assert pipeline.choose_run(tmp_path, date(2026, 10, 25), None, None).path == first.path

    first.mark("write")
    second = pipeline.choose_run(tmp_path, date(2026, 11, 1), None, None)
    assert second.path != first.path
    assert second.manifest.since == "2026-10-25"


def test_explicit_dates_start_a_new_run(tmp_path: Path) -> None:
    first = pipeline.choose_run(tmp_path, TODAY, None, None)
    second = pipeline.choose_run(tmp_path, TODAY, date(2026, 10, 1), None)
    assert second.path != first.path
    assert second.manifest.since == "2026-10-01"


def test_execute_resumes_only_unfinished_stages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config(EXAMPLE)
    paths = type(config.paths)(tmp_path, tmp_path / "notes", tmp_path / "parent.md")
    config = type(config)(**{**config.__dict__, "paths": paths})
    calls: list[str] = []
    stages = {name: (lambda n: lambda run, cfg: calls.append(n))(name) for name in pipeline.STAGES}
    monkeypatch.setattr(pipeline, "STAGE_FUNCTIONS", stages)

    pipeline.execute(config, TODAY, dry_run=True)
    assert calls == ["collect", "score", "rank"]
    calls.clear()
    pipeline.execute(config, TODAY)
    assert calls == ["select", "read", "write"]
    calls.clear()
    pipeline.execute(config, TODAY)
    assert calls == []
