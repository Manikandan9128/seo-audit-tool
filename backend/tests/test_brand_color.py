from app.services.brand_color import _is_grayscale, extract_brand_color


def test_pure_gray_is_grayscale():
    assert _is_grayscale("808080") is True


def test_near_gray_border_color_is_grayscale():
    # Regression test: D0D5DD is a plain Tailwind-style UI border gray
    # (RGB spread only 13) that the old tolerance=12 let through as a
    # "brand color," making report accents nearly invisible.
    assert _is_grayscale("D0D5DD") is True


def test_near_black_is_grayscale():
    assert _is_grayscale("1A1A1A") is True


def test_near_white_is_grayscale():
    assert _is_grayscale("FAFAFA") is True


def test_real_color_is_not_grayscale():
    assert _is_grayscale("FF8F84") is False


def test_extract_brand_color_prefers_theme_color_meta():
    html = '<meta name="theme-color" content="#ABCDEF">'
    assert extract_brand_color(html) == "ABCDEF"


def test_extract_brand_color_falls_back_to_frequent_non_gray_hex():
    html = "color: #FF8F84; color: #FF8F84; border: #D0D5DD;"
    assert extract_brand_color(html) == "FF8F84"


def test_extract_brand_color_skips_grayscale_only_page():
    html = "border: #D0D5DD; text: #1A1A1A;"
    assert extract_brand_color(html) is None
