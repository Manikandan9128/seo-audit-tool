from unittest.mock import MagicMock, patch

from app.services.ga4_service import list_properties


def _admin(pages):
    admin = MagicMock()
    requests = []
    for page in pages:
        req = MagicMock()
        req.execute.return_value = page
        requests.append(req)
    admin.accountSummaries.return_value.list.side_effect = requests
    return admin, requests


def test_one_summaries_call_per_page_flattens_all_accounts_and_retries():
    pages = [
        {"accountSummaries": [
            {"propertySummaries": [{"property": "properties/1", "displayName": "ValueCore"}]},
            {"propertySummaries": [{"property": "properties/2", "displayName": "BharatBenz - GA4"}]},
        ], "nextPageToken": "t2"},
        {"accountSummaries": [{"propertySummaries": [{"property": "properties/3", "displayName": "GeoPITS - GA4"}]},
                              {"account": "accounts/9"}]},
    ]
    admin, requests = _admin(pages)
    with patch("app.services.ga4_service.build", return_value=admin):
        props = list_properties(object())
    assert props == [
        {"name": "properties/1", "display_name": "ValueCore"},
        {"name": "properties/2", "display_name": "BharatBenz - GA4"},
        {"name": "properties/3", "display_name": "GeoPITS - GA4"},
    ]
    assert admin.accountSummaries.return_value.list.call_count == 2
    assert all(r.execute.call_args.kwargs.get("num_retries") == 3 for r in requests)
