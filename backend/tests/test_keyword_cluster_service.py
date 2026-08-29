from unittest.mock import patch

from app.services.keyword_cluster_service import generate_keyword_clusters


def test_clusters_map_keywords_to_labels():
    fake_response = """[
      {"cluster": "Certified Payroll", "keywords": ["certified payroll software", "certified payroll service"]},
      {"cluster": "Labor Burden", "keywords": ["calculating labor burden"]}
    ]"""
    with patch("app.services.keyword_cluster_service.generate_text", return_value=(fake_response, "gemini")):
        result = generate_keyword_clusters(["certified payroll software", "certified payroll service", "calculating labor burden"])
    assert result == {
        "certified payroll software": "Certified Payroll",
        "certified payroll service": "Certified Payroll",
        "calculating labor burden": "Labor Burden",
    }


def test_drops_hallucinated_keywords_not_in_input():
    # Regression guard: the AI must never attach a cluster label to a
    # keyword that wasn't actually in the uploaded data.
    fake_response = '[{"cluster": "Payroll", "keywords": ["real keyword", "invented keyword"]}]'
    with patch("app.services.keyword_cluster_service.generate_text", return_value=(fake_response, "gemini")):
        result = generate_keyword_clusters(["real keyword"])
    assert result == {"real keyword": "Payroll"}
    assert "invented keyword" not in result


def test_returns_empty_dict_when_ai_unavailable():
    from app.integrations.text_ai_client import NoAIProviderConfigured

    with patch("app.services.keyword_cluster_service.generate_text", side_effect=NoAIProviderConfigured("no key")):
        result = generate_keyword_clusters(["some keyword"])
    assert result == {}


def test_returns_empty_dict_on_invalid_json():
    with patch("app.services.keyword_cluster_service.generate_text", return_value=("not json at all", "gemini")):
        result = generate_keyword_clusters(["some keyword"])
    assert result == {}


def test_returns_empty_dict_for_empty_input():
    assert generate_keyword_clusters([]) == {}
