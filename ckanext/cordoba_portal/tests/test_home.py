import pytest

from ckan.tests import factories


@pytest.mark.usefixtures("with_plugins", "clean_db")
def test_home_shows_the_initiative_and_the_counts(app):
    org = factories.Organization()
    factories.Dataset(owner_org=org["id"], source_portal="cbadatos")
    factories.Dataset(owner_org=org["id"], source_portal="cbadatos")

    page = app.get("/").body

    assert "Todos los datos de Córdoba, en un solo lugar" in page
    assert "iniciativa ciudadana" in page
    assert 'name="q"' in page
    assert "<strong>2</strong> conjuntos de datos" in page
    assert "<strong>1</strong> organizaciones" in page
    # No "recent datasets": harvested datasets carry a mix of dates.
    assert "Recent Datasets" not in page
    assert "recent-packages" not in page


@pytest.mark.usefixtures("with_plugins")
def test_about_page_names_the_source_portals(app):
    page = app.get("/about").body

    assert "agrupar en un solo lugar los datos que ya están" in page
    assert "https://datosgestionabierta.cba.gov.ar/" in page
    assert "https://datosestadistica.cba.gov.ar/" in page
    assert "procesar más datos" in page
    assert "instancia experimental de CKAN" in page
