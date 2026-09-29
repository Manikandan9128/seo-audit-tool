from unittest.mock import MagicMock, patch

from app.services import tech_stack_service as ts


def _resp(html, headers=None, url="https://example.com/"):
    r = MagicMock()
    r.text = html
    r.headers = headers or {}
    r.url.scheme = "https"
    return r


def _detect(html, headers=None):
    with patch.object(ts.httpx, "get", return_value=_resp(html, headers)), \
         patch.object(ts, "_resolve_ip", return_value="1.2.3.4"), patch.object(ts, "_reverse_dns", return_value=None):
        result = ts.detect_tech_stack("https://example.com")
    return {(d["category"], d["name"]) for d in result["detected"]}


def test_detects_gtm_and_hubspot_forms_on_a_geopits_shaped_page():
    html = (
        "<script>j.src='https://www.googletagmanager.com/gtm.js?id='+i+dl;</script>"
        '<div id="hubspot-form"></div><script>s.src="https://js.hsforms.net/forms/embed/v2.js"</script>'
    )
    found = _detect(html)
    assert ("analytics", "Google Tag Manager") in found
    assert ("marketing", "HubSpot") in found


def test_detects_hubspot_tracking_cookieyes_clearbit_and_sentry():
    html = (
        '<script src="//js.hs-scripts.com/24336868.js"></script>'
        '<script src="https://cdn-cookieyes.com/client_data/x/script.js"></script>'
        '<script src="https://tag.clearbitscripts.com/v1/pk_x/tags.js"></script>'
        '<script src="https://js.sentry-cdn.com/abc.min.js"></script>'
    )
    found = _detect(html)
    assert {("marketing", "HubSpot"), ("consent", "CookieYes"), ("marketing", "Clearbit"), ("monitoring", "Sentry")} <= found


def test_a_plain_link_to_a_hubspot_blog_is_not_reported_as_hubspot():
    assert ("marketing", "HubSpot") not in _detect('<a href="https://blog.hubspot.com/seo">read this</a>')


def test_server_header_that_repeats_the_cdn_name_is_not_a_second_tile():
    found = _detect("<html></html>", {"server": "cloudflare", "cf-ray": "abc"})
    assert ("cdn", "Cloudflare") in found
    assert not any(cat == "server" for cat, _ in found)


def test_unrelated_server_header_is_still_reported():
    found = _detect("<html></html>", {"server": "nginx"})
    assert ("server", "nginx") in found
