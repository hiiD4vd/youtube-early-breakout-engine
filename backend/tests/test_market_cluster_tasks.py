from types import SimpleNamespace

import pytest

from app.tasks.market_cluster_tasks import _centroid, _compatible, _dot, _identity_keys


def feature(*, vector: dict[str, float], theme: str = "", content_format: str = "", topic_type: str = "other", entities=None):
    return SimpleNamespace(
        sparse_vector=vector,
        provenance={
            "semantic": {
                "topic_theme": theme,
                "content_format": content_format,
                "topic_type": topic_type,
                "entities": entities or [],
            }
        },
    )


def test_centroid_is_normalized_instead_of_growing_with_cluster_size():
    row = feature(vector={"animal": 1.0})
    one = _centroid([row])
    many = _centroid([row] * 100)
    assert _dot(row.sparse_vector, one) == pytest.approx(1.0)
    assert _dot(row.sparse_vector, many) == pytest.approx(1.0)


def test_shared_words_do_not_merge_incompatible_topics():
    animal = feature(vector={"facts": 0.8}, theme="animal facts", topic_type="animals")
    station = feature(vector={"facts": 0.8}, theme="railway history", topic_type="education")
    assert _compatible(station, [animal]) == (False, False)


def test_recognisable_ranking_format_can_group_different_subjects():
    turtles = feature(vector={"ranking": 0.8}, theme="animal rankings", content_format="countdown ranking")
    football = feature(vector={"ranking": 0.8}, theme="football rankings", content_format="countdown ranking")
    assert _compatible(football, [turtles]) == (True, True)


def test_identity_index_does_not_use_generic_lexical_words():
    row = feature(
        vector={"viral": 0.8, "funny": 0.6},
        theme="pet care",
        content_format="tutorial",
        topic_type="animals",
        entities=["cat"],
    )
    assert _identity_keys(row) == {
        "theme:pet care",
        "format:tutorial",
        "entity:animals:cat",
    }
