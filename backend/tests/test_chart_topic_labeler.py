"""Self-check for chart topic grouping: LLM-first extraction + lexical fallback."""

from app.services.chart_topic_labeler import (
    ChartVideo,
    TopicGroup,
    build_extraction_prompt,
    group_chart_videos,
    parse_extraction_response,
    parse_naming_response,
)


def test_groups_by_shared_token_across_channels():
    """Lexical fallback: groups by shared cross-channel token."""
    videos = [
        ChartVideo("v1", "Lamine Yamal wonder goal vs France", "c1", "Chan1", "FR", 1, 1_000_000),
        ChartVideo("v2", "Yamal scores again in semifinal", "c2", "Chan2", "ES", 2, 500_000),
        ChartVideo("v3", "Unrelated cooking shorts recipe", "c3", "Chan3", "US", 3, 100_000),
        ChartVideo("v4", "Yamal highlights Barcelona", "c1", "Chan1", "FR", 4, 800_000),
    ]
    groups = group_chart_videos(videos, min_channels=2)
    # "yamal" appears across c1+c2 → seed. "cooking" only c3 → dropped.
    assert len(groups) == 1
    assert groups[0].label == "yamal"
    assert groups[0].channel_count == 2
    assert len(groups[0].videos) == 3


def test_no_groups_when_no_cross_channel_token():
    """Lexical fallback: no groups when no token shared across channels."""
    videos = [
        ChartVideo("v1", "Solo topic alpha", "c1", "Chan1", "US", 1, 1000),
        ChartVideo("v2", "Different topic beta", "c2", "Chan2", "US", 2, 2000),
    ]
    assert group_chart_videos(videos, min_channels=2) == []


def test_parse_naming_response_strict_json():
    """Legacy naming parser still works for fallback path."""
    text = '[{"group": 0, "topic": "Lamine Yamal rise", "kind": "person"}, {"group": 1, "topic": null, "kind": null}]'
    named = parse_naming_response(text, 2)
    assert named == {0: {"topic": "Lamine Yamal rise", "kind": "person"}}


def test_parse_extraction_response_semantic_grouping():
    """LLM-first: groups videos by semantic topic, not shared tokens.

    This is the key test — videos about the same topic but with NO shared
    tokens should still group together when the LLM assigns them to the same
    topic. This is what lexical grouping cannot do.
    """
    videos = [
        ChartVideo("v1", "Messi scores incredible free kick", "c1", "Chan1", "US", 1, 1_000_000),
        ChartVideo("v2", "Inter Miami wins with late goal", "c2", "Chan2", "US", 2, 500_000),
        ChartVideo("v3", "GOAT does it again in Florida", "c3", "Chan3", "US", 3, 800_000),
        ChartVideo("v4", "Cooking pasta recipe tutorial", "c4", "Chan4", "US", 4, 100_000),
    ]
    # LLM response: groups v1+v2+v3 as "Messi Inter Miami", skips v4.
    llm_response = '''[
        {"topic": "Messi Inter Miami debut", "kind": "sports", "video_indices": [0, 1, 2]}
    ]'''
    groups = parse_extraction_response(llm_response, videos)
    assert len(groups) == 1
    assert groups[0].label == "Messi Inter Miami debut"
    assert len(groups[0].videos) == 3
    assert groups[0].channel_count == 3
    # No shared token between these titles — lexical would never group them.
    assert "messi" not in videos[1].title.lower()
    assert "messi" not in videos[2].title.lower()


def test_parse_extraction_response_multiple_topics():
    """LLM-first: multiple topics in one response."""
    videos = [
        ChartVideo("v1", "Trump announces new tariff", "c1", "Chan1", "US", 1, 2_000_000),
        ChartVideo("v2", "Market reacts to trade policy", "c2", "Chan2", "US", 2, 1_000_000),
        ChartVideo("v3", "Earthquake hits Japan coast", "c3", "Chan3", "JP", 3, 3_000_000),
        ChartVideo("v4", "Tsunami warning issued", "c4", "Chan4", "JP", 4, 2_500_000),
    ]
    llm_response = '''[
        {"topic": "Trump tariff policy", "kind": "politics", "video_indices": [0, 1]},
        {"topic": "Japan earthquake", "kind": "event", "video_indices": [2, 3]}
    ]'''
    groups = parse_extraction_response(llm_response, videos)
    assert len(groups) == 2
    labels = {g.label for g in groups}
    assert "Trump tariff policy" in labels
    assert "Japan earthquake" in labels


def test_parse_extraction_response_malformed_returns_empty():
    """LLM-first: malformed JSON returns empty list (triggers lexical fallback)."""
    videos = [ChartVideo("v1", "Test", "c1", "Chan1", "US", 1, 100)]
    assert parse_extraction_response("not json at all", videos) == []
    assert parse_extraction_response("```invalid```", videos) == []
    assert parse_extraction_response("", videos) == []


def test_parse_extraction_response_strips_markdown_fence():
    """LLM-first: handles ```json fenced responses."""
    videos = [
        ChartVideo("v1", "Video one", "c1", "Chan1", "US", 1, 100),
        ChartVideo("v2", "Video two", "c2", "Chan2", "US", 2, 200),
    ]
    llm_response = '''```json
[{"topic": "Test topic", "kind": "other", "video_indices": [0, 1]}]
```'''
    groups = parse_extraction_response(llm_response, videos)
    assert len(groups) == 1
    assert groups[0].label == "Test topic"


def test_parse_extraction_response_drops_single_video_topics():
    """LLM-first: topics with <2 videos are dropped (min evidence)."""
    videos = [
        ChartVideo("v1", "Video one", "c1", "Chan1", "US", 1, 100),
        ChartVideo("v2", "Video two", "c2", "Chan2", "US", 2, 200),
    ]
    llm_response = '''[
        {"topic": "Solo topic", "kind": "other", "video_indices": [0]},
        {"topic": "Real topic", "kind": "other", "confidence": "medium", "trend_type": "other", "video_indices": [0, 1]}
    ]'''
    groups = parse_extraction_response(llm_response, videos)
    assert len(groups) == 1
    assert groups[0].label == "Real topic"
    assert groups[0].confidence == "medium"


def test_build_extraction_prompt_includes_all_titles():
    """LLM-first: prompt contains every video title with index."""
    videos = [
        ChartVideo("v1", "First title here", "c1", "Chan1", "US", 1, 100),
        ChartVideo("v2", "Second title here", "c2", "Chan2", "US", 2, 200),
    ]
    prompt = build_extraction_prompt(videos, "US")
    assert "0. First title here" in prompt
    assert "1. Second title here" in prompt
    assert "region: US" in prompt


if __name__ == "__main__":
    test_groups_by_shared_token_across_channels()
    test_no_groups_when_no_cross_channel_token()
    test_parse_naming_response_strict_json()
    test_parse_extraction_response_semantic_grouping()
    test_parse_extraction_response_multiple_topics()
    test_parse_extraction_response_malformed_returns_empty()
    test_parse_extraction_response_strips_markdown_fence()
    test_parse_extraction_response_drops_single_video_topics()
    test_build_extraction_prompt_includes_all_titles()
    print("chart_topic_labeler self-check OK")