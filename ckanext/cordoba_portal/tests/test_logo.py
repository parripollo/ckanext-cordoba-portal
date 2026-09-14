import pytest


@pytest.mark.usefixtures("with_plugins")
def test_the_header_and_the_footer_show_the_portal_logo(app):
    page = app.get("/").body
    assert 'src="/images/cba-datos-logo.svg"' in page
    assert 'src="/images/cba-datos-logo-white.svg"' in page
    assert "ckan-logo" not in page
    # One-line footer: no link columns, no "Powered by", no language selector
    assert "footer-links" not in page
    assert "Powered by" not in page
    assert "lang-select" not in page
    assert 'rel="shortcut icon" href="/images/favicon.ico"' in page


@pytest.mark.usefixtures("with_plugins")
@pytest.mark.parametrize("path", [
    "/images/cba-datos-logo.svg",
    "/images/cba-datos-logo-white.svg",
    "/images/favicon.ico",
    "/images/favicon.svg",
    "/images/apple-touch-icon.png",
])
def test_the_logo_files_are_served(app, path):
    assert app.get(path).status_code == 200
