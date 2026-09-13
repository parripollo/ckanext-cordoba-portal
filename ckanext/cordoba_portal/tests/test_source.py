"""The provenance of a dataset: source_portal and source_url."""
import pytest

from ckan.plugins import toolkit
from ckan.tests import factories

GESTION = "https://datosgestionabierta.cba.gov.ar"


@pytest.fixture
def harvested(clean_db):
    org = factories.Organization(
        name="gestion-o-salud",
        title="Ministerio de Salud",
        source_portal="datosgestionabierta",
        source_url=GESTION + "/organization/o-salud",
    )
    dataset = factories.Dataset(
        owner_org=org["id"],
        title="Vacunación 2026",
        source_portal="datosgestionabierta",
        source_url=GESTION + "/dataset/abc-123",
    )
    resource = factories.Resource(
        package_id=dataset["id"],
        name="vacunacion.csv",
        url="https://cbadatos.com.ar/dataset/x/resource/y/download/vacunacion.csv",
        source_url=GESTION + "/dataset/abc-123/resource/r-1/download/vacunacion.csv",
        source_downloaded="2026-09-13T10:00:00",
    )
    return {"org": org, "dataset": dataset, "resource": resource}


@pytest.mark.usefixtures("with_plugins")
class TestProvenanceOnThePages:

    def test_dataset_page(self, app, harvested):
        page = app.get("/dataset/" + harvested["dataset"]["name"]).body

        assert "Procedencia" in page
        assert "Portal de Datos Abiertos de Gestión (Gobierno de Córdoba)" in page
        assert 'href="' + GESTION + '/dataset/abc-123"' in page
        assert "Ver en el portal de origen" in page
        assert "Producido por" in page and "Ministerio de Salud" in page

    def test_resource_page(self, app, harvested):
        url = "/dataset/{}/resource/{}".format(
            harvested["dataset"]["name"], harvested["resource"]["id"])
        page = app.get(url).body

        assert "Procedencia:" in page
        assert "Portal de Datos Abiertos de Gestión" in page
        assert 'href="' + GESTION + '/dataset/abc-123/resource/r-1/download/vacunacion.csv"' in page
        assert "Archivo original" in page
        assert "copia guardada el" in page

    def test_organization_page(self, app, harvested):
        page = app.get("/organization/gestion-o-salud").body

        assert "Organización del" in page
        assert "Portal de Datos Abiertos de Gestión" in page
        assert 'href="' + GESTION + '/organization/o-salud"' in page

    def test_own_dataset_shows_own_production(self, app, clean_db):
        dataset = factories.Dataset(source_portal="cbadatos")

        page = app.get("/dataset/" + dataset["name"]).body

        assert "Producción propia (cbadatos.com.ar)" in page
        assert "Ver en el portal de origen" not in page

    def test_search_facet_with_labels(self, app, harvested):
        factories.Dataset(source_portal="cbadatos")

        page = app.get("/dataset/").body

        assert "Portal de origen" in page
        assert "Gestión Abierta" in page
        assert "Producción propia" in page
        assert "source_portal=datosgestionabierta" in page

        filtered = app.get("/dataset/?source_portal=datosgestionabierta").body
        assert "Vacunación 2026" in filtered
        assert "1 conjunto de datos encontrado" in filtered or "1 dataset found" in filtered


@pytest.mark.usefixtures("with_plugins", "clean_db")
class TestProvenanceIsRequired:

    def test_source_portal_is_required(self):
        with pytest.raises(toolkit.ValidationError) as e:
            factories.Dataset()
        assert "source_portal" in e.value.error_dict

    def test_source_portal_must_be_a_known_portal(self):
        with pytest.raises(toolkit.ValidationError) as e:
            factories.Dataset(source_portal="otro-portal")
        assert "source_portal" in e.value.error_dict

    def test_source_url_must_be_a_url(self):
        with pytest.raises(toolkit.ValidationError) as e:
            factories.Dataset(source_portal="cbadatos", source_url="no es una url")
        assert "source_url" in e.value.error_dict
