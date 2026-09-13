import pytest

from ckan.tests import factories


@pytest.mark.usefixtures("clean_db", "with_plugins")
def test_home_shows_the_initiative_and_the_counts(app):
    org = factories.Organization()
    factories.Dataset(owner_org=org["id"])
    factories.Dataset(owner_org=org["id"])

    page = app.get("/").body

    assert "Todos los datos de Córdoba, en un solo lugar" in page
    assert "iniciativa ciudadana" in page
    assert 'name="q"' in page
    assert "<strong>2</strong> conjuntos de datos" in page
    assert "<strong>1</strong> organizaciones" in page
