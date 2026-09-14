"""The About menu with the pages, the sitemap and init-pages."""
import pytest

from ckan.tests import factories
from ckan.tests.helpers import call_action

from ckanext.cordoba_portal.cli import PAGES


def make_page(name, title, order="", private=False):
    call_action("ckanext_pages_update", page="", name=name, title=title, content="x",
                order=order, private=private, page_type="page",
                context={"user": factories.Sysadmin()["name"]})


@pytest.mark.usefixtures("with_plugins", "clean_db")
class TestAboutMenu:

    def test_header_has_one_about_menu_with_the_public_pages(self, app):
        make_page("segunda", "Segunda página", order="2")
        make_page("primera", "Primera página", order="1")
        make_page("borrador", "Borrador", private=True)

        page = app.get("/").body
        menu = page[page.index("cba-about-menu"):page.index("</ul>", page.index("cba-about-menu"))]

        assert "/pages/primera" in menu and "/pages/segunda" in menu
        assert menu.index("Primera página") < menu.index("Segunda página")
        assert "Borrador" not in menu
        # the pages do not get a tab each
        assert page.count("/pages/primera") == 1

    def test_about_page_lists_the_pages(self, app):
        make_page("primera", "Primera página", order="1")

        page = app.get("/about").body

        assert "Más información" in page
        assert '/pages/primera"' in page and "Primera página" in page


@pytest.mark.usefixtures("with_plugins", "clean_db")
class TestSitemap:

    def test_sitemap_and_robots(self, app):
        org = factories.Organization(name="gestion-o-salud", source_portal="datosgestionabierta")
        dataset = factories.Dataset(owner_org=org["id"], source_portal="cbadatos")
        group = factories.Group()
        make_page("primera", "Primera página", order="1")
        make_page("borrador", "Borrador", private=True)

        response = app.get("/sitemap.xml")

        assert response.status_code == 200
        assert response.headers["Content-Type"].startswith("application/xml")
        body = response.body
        assert "<loc>http://test.ckan.net/dataset/%s</loc>" % dataset["name"] in body
        assert "<lastmod>" in body
        assert "/organization/gestion-o-salud</loc>" in body
        assert "/group/%s</loc>" % group["name"] in body
        assert "/pages/primera</loc>" in body
        assert "borrador" not in body
        assert "/about</loc>" in body

        robots = app.get("/robots.txt").body
        assert "Sitemap: http://test.ckan.net/sitemap.xml" in robots


@pytest.mark.usefixtures("with_plugins", "clean_db")
class TestInitPages:

    def test_creates_the_missing_pages_once(self, cli):
        from ckanext.cordoba_portal.cli import cordoba_portal

        result = cli.invoke(cordoba_portal, ["init-pages"])
        assert result.exit_code == 0, result.output
        assert "created: sobre-este-ckan" in result.output

        page = call_action("ckanext_pages_show", page="sobre-este-ckan")
        assert page["title"] == PAGES[0][1]
        assert "solo con PostgreSQL" in page["content"]
        assert page["private"] is False

        # the second run leaves it alone (edited on the site)
        call_action("ckanext_pages_update", page="sobre-este-ckan", name="sobre-este-ckan",
                    title="Editada", content="editado", order="1", private=False,
                    page_type="page", context={"user": factories.Sysadmin()["name"]})
        result = cli.invoke(cordoba_portal, ["init-pages"])
        assert "exists: sobre-este-ckan" in result.output
        assert call_action("ckanext_pages_show", page="sobre-este-ckan")["title"] == "Editada"
