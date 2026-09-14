"""The legislatura harvester: the section pages, their headings and files."""
import hashlib
import json
import os
from datetime import datetime
from unittest import mock
from urllib.parse import urlparse, parse_qs

import pytest
import requests

from ckan import model
from ckan.tests import factories
from ckan.tests.helpers import call_action
from ckanext.harvest.model import HarvestGatherError, HarvestObject
from ckanext.harvest.tests.factories import HarvestSourceObj, HarvestJobObj

from ckanext.cordoba_portal import legislatura
from ckanext.cordoba_portal.legislatura import LegislaturaHarvester

PORTAL = "https://legislaturacba.gob.ar"
HERE = os.path.dirname(__file__)


def fixture(name):
    with open(os.path.join(HERE, "data", "legislatura-" + name), encoding="utf-8") as f:
        return f.read()   # real answers of the portal, trimmed


PAGES = {
    "sesiones": json.loads(fixture("sesiones.json")),
    "administracion": json.loads(fixture("administracion.json")),
}
METADATA = fixture("metadato.txt")
FILE = b"periodo;anio\n148;2026\n"
SHA = hashlib.sha256(FILE).hexdigest()
CONFIG = {"pause": 0, "copy_pause": 0, "pages": ["sesiones", "administracion"],
          "user_agent": "Mozilla/5.0 test cbadatos.com.ar"}
DIARIOS = PORTAL + "/diarios-de-sesion/"
DIARIOS_CSV = PORTAL + "/wp-content/uploads/2026/09/DIARIOS-DE-SESIONES.csv"


class FakeResponse:
    def __init__(self, data=None, status_code=200, content=b"", headers=None):
        self._data = data
        self.status_code = status_code
        self.content = self._content = content
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("HTTP %s" % self.status_code)

    def json(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, chunk_size):
        for i in range(0, len(self._content), chunk_size):
            yield self._content[i:i + chunk_size]


class FakePortal:
    """requests.get of the portal: the pages API from the fixtures, the
    metadata text file, the uploads."""

    def __init__(self, file_status=200, file_content=FILE):
        self.calls = []
        self.file_status = file_status
        self.file_content = file_content

    def __call__(self, url, params=None, headers=None, **kwargs):
        self.calls.append((url, params or {}, headers or {}, kwargs.get("stream", False)))
        path = urlparse(url).path
        if path == "/wp-json/wp/v2/pages":
            return FakeResponse(PAGES.get(params["slug"], []))
        if path.endswith(".txt") and not kwargs.get("stream"):
            # read at fetch, for the extras; the portal serves it with a BOM
            # and no charset
            return FakeResponse(content=("\ufeff" + METADATA).encode("utf-8"))
        if "/wp-content/uploads/" in path:
            return FakeResponse(status_code=self.file_status, content=self.file_content, headers={
                "Content-Type": "text/csv", "Content-Length": str(len(self.file_content)),
                "Last-Modified": "Mon, 14 Sep 2026 13:31:29 GMT"})
        return FakeResponse(status_code=404)

    def page_calls(self):
        return [p["slug"] for u, p, _, _ in self.calls if u.endswith("/wp-json/wp/v2/pages")]

    def file_calls(self):
        """The downloads (streamed); the metadata file read at fetch is not one."""
        return [(u, h) for u, _, h, stream in self.calls if stream]


@pytest.fixture(autouse=True)
def forget_the_cached_harvest_user():
    LegislaturaHarvester()._user_name = None


@pytest.fixture
def source(with_plugins, clean_db):
    org = factories.Organization(name="legislatura", title="Legislatura de Córdoba",
                                 source_portal="legislatura")
    return HarvestSourceObj(url=PORTAL, source_type="legislatura", owner_org=org["id"],
                            config=json.dumps(CONFIG), title="Legislatura")


def harvest(source, portal=None, copy_files=True, pages=None):
    """One whole run: gather, fetch, import. Returns (portal, results)."""
    portal = portal or FakePortal()
    job = HarvestJobObj(source=source)
    harvester = LegislaturaHarvester()
    results = {}
    with mock.patch("ckanext.cordoba_portal.legislatura.requests.get", portal), \
            mock.patch("ckanext.cordoba_portal.harvester.requests.get", portal), \
            mock.patch.dict(PAGES, pages or {}):
        source.config = json.dumps(dict(CONFIG, copy_files=copy_files))
        ids = harvester.gather_stage(job)
        for object_id in ids or []:
            obj = HarvestObject.get(object_id)
            assert harvester.fetch_stage(obj)
            results[obj.guid] = harvester.import_stage(obj)
    job.status = "Finished"
    job.finished = datetime.utcnow()
    job.save()
    return portal, results


def changed_page(slug, old, new):
    page = json.loads(json.dumps(PAGES[slug]))
    page[0]["content"]["rendered"] = page[0]["content"]["rendered"].replace(old, new)
    return page


@pytest.mark.usefixtures("with_plugins", "clean_db")
class TestConfig:

    def test_empty_is_fine(self):
        assert LegislaturaHarvester().validate_config("") == ""

    def test_numbers_pages_and_org_are_checked(self):
        with pytest.raises(ValueError):
            LegislaturaHarvester().validate_config(json.dumps({"pause": "slow"}))
        with pytest.raises(ValueError, match="pages"):
            LegislaturaHarvester().validate_config(json.dumps({"pages": "sesiones"}))
        with pytest.raises(ValueError, match="single_org"):
            LegislaturaHarvester().validate_config(json.dumps({"single_org": "nope"}))

    def test_info(self):
        assert LegislaturaHarvester().info()["name"] == "legislatura"


class TestGather:

    def test_one_object_per_heading(self, source):
        job = HarvestJobObj(source=source)
        portal = FakePortal()
        with mock.patch("ckanext.cordoba_portal.legislatura.requests.get", portal):
            ids = LegislaturaHarvester().gather_stage(job)

        assert sorted(HarvestObject.get(i).guid for i in ids) == [
            "asistencia-de-legisladores-a-sesiones-2020", "diarios-de-sesion",
            "escala-salarial", "gastos-de-viaticos", "ruta-de-los-expedientes"]
        content = json.loads(HarvestObject.get(ids[1]).content)
        assert content["section"] == {"slug": "sesiones", "title": "Sesiones",
                                      "url": PORTAL + "/sesiones/"}
        assert content["dataset"]["title"] == "DIARIOS DE SESIÓN (2002 – 2026)"
        assert content["dataset"]["url"] == DIARIOS
        assert content["dataset"]["updated"] == "2026-09-14"
        assert len(content["dataset"]["files"]) == 5
        # one request per section page, with the browser's User-Agent
        assert portal.page_calls() == ["sesiones", "administracion"]
        assert all(h["User-Agent"] == CONFIG["user_agent"] for _, _, h, _ in portal.calls)

    def test_a_missing_page_is_a_gather_error_the_rest_goes_on(self, source):
        job = HarvestJobObj(source=source)
        source.config = json.dumps(dict(CONFIG, pages=["sesiones", "nada"]))
        with mock.patch("ckanext.cordoba_portal.legislatura.requests.get", FakePortal()):
            ids = LegislaturaHarvester().gather_stage(job)
        assert len(ids) == 3
        errors = model.Session.query(HarvestGatherError).filter_by(harvest_job_id=job.id).all()
        assert len(errors) == 1 and "nada" in errors[0].message

    def test_portal_down_is_a_gather_error(self, source):
        job = HarvestJobObj(source=source)
        with mock.patch("ckanext.cordoba_portal.legislatura.requests.get",
                        return_value=FakeResponse(status_code=500)):
            assert LegislaturaHarvester().gather_stage(job) is None


class TestImport:

    def test_the_dataset(self, source):
        harvest(source, copy_files=False)

        dataset = call_action("package_show", id="diarios-de-sesion")
        assert dataset["title"] == "Diarios de sesión (2002 – 2026)"
        assert dataset["notes"].startswith("Este set de datos sistematiza el acceso a los documentos")
        assert dataset["source_portal"] == "legislatura"
        assert dataset["source_url"] == DIARIOS
        assert dataset["organization"]["name"] == "legislatura"
        assert dataset["license_id"] == "notspecified"
        extras = {e["key"]: e["value"] for e in dataset["extras"] if not e["key"].startswith("harvest_")}
        assert extras == {
            "seccion": "Sesiones",
            "tema": "Sesiones",
            "frecuencia": "semanal",
            "fuente": "Secretaría Legislativa de la Legislatura de la Provincia de Córdoba",
            "actualizado_en_origen": "2026-09-14",
        }
        assert [g["name"] for g in dataset["groups"]] == ["legislatura-sesiones"]
        assert call_action("group_show", id="legislatura-sesiones")["title"] == "Sesiones"

        # a mixed-case title is left alone; the section is the group
        ruta = call_action("package_show", id="ruta-de-los-expedientes")
        assert ruta["title"] == "Ruta de los expedientes (2002-2026)"
        salarial = call_action("package_show", id="escala-salarial")
        assert [g["name"] for g in salarial["groups"]] == ["legislatura-administracion"]
        assert call_action("package_search", q="")["count"] == 5

    def test_the_resources(self, source):
        harvest(source, copy_files=False)

        dataset = call_action("package_show", id="diarios-de-sesion")
        by_name = {r["name"]: r for r in dataset["resources"]}
        assert list(by_name) == [
            "Diarios de sesión (2002 – 2026) - CSV",
            "Diarios de sesión (2002 – 2026) - XLSX",
            "Diarios de sesión (2002 – 2026) - XML",
            "Diarios de sesión (2002 – 2026) - JSON",
            "Diarios de sesión (2002 – 2026) - Metadatos",
        ]
        csv = by_name["Diarios de sesión (2002 – 2026) - CSV"]
        assert csv["format"] == "CSV" and csv["url"] == DIARIOS_CSV
        assert csv["source_url"] == DIARIOS_CSV
        assert csv["source_last_modified"] == "2026-09-14"
        assert by_name["Diarios de sesión (2002 – 2026) - XML"]["format"] == "ZIP"
        assert by_name["Diarios de sesión (2002 – 2026) - Metadatos"]["format"] == "TXT"

    def test_a_heading_that_points_elsewhere_is_a_link(self, source):
        harvest(source, copy_files=False)

        dataset = call_action("package_show", id="gastos-de-viaticos")
        assert dataset["title"] == "Gastos de viáticos"
        assert dataset["source_url"] == PORTAL + "/administracion/"     # no page of its own
        assert dataset["notes"].startswith("Acceso: https://transparencia.cba.gov.ar")
        assert len(dataset["resources"]) == 1
        link = dataset["resources"][0]
        assert link["url"] == "https://transparencia.cba.gov.ar/MasterConsulta.aspx"
        assert link["format"] == "HTML" and not link.get("source_url")

    def test_second_run_is_unchanged_and_stable(self, source):
        harvest(source, copy_files=False)
        before = call_action("package_show", id="diarios-de-sesion")

        portal, results = harvest(source, copy_files=False)

        assert set(results.values()) == {"unchanged"}
        after = call_action("package_show", id="diarios-de-sesion")
        assert after["id"] == before["id"]
        assert [r["id"] for r in after["resources"]] == [r["id"] for r in before["resources"]]
        assert after["metadata_modified"] == before["metadata_modified"]

    def test_a_new_year_in_the_title_is_the_same_dataset(self, source):
        harvest(source, copy_files=False)
        newer = changed_page("sesiones", "DIARIOS DE SESIÓN (2002 – 2026)", "DIARIOS DE SESIÓN (2002 – 2027)")

        portal, results = harvest(source, copy_files=False, pages={"sesiones": newer})

        assert results["diarios-de-sesion"] is True
        dataset = call_action("package_show", id="diarios-de-sesion")
        assert dataset["title"] == "Diarios de sesión (2002 – 2027)"
        assert call_action("package_search", q="")["count"] == 5

    def test_a_name_taken_by_another_portal(self, source):
        org = factories.Organization()
        factories.Dataset(name="escala-salarial", owner_org=org["id"],
                          source_portal="datosgestionabierta")

        harvest(source, copy_files=False)

        ours = call_action("package_search", fq="source_portal:legislatura")["results"]
        names = sorted(d["name"] for d in ours if d["name"].startswith("escala"))
        assert names and names[0] != "escala-salarial"


class TestCopy:

    def test_files_are_copied(self, source):
        portal, results = harvest(source)

        # 4 datasets with 5 files each; the link elsewhere is not fetched
        assert len(portal.file_calls()) == 20
        assert all(h["User-Agent"] == CONFIG["user_agent"] for _, h in portal.file_calls())
        dataset = call_action("package_show", id="diarios-de-sesion")
        csv = dataset["resources"][0]
        assert csv["url_type"] == "upload"
        assert csv["url"].endswith("/download/diarios-de-sesiones.csv")    # CKAN munges the name
        assert csv["hash"] == SHA
        assert csv["source_url"] == DIARIOS_CSV
        meta = dataset["resources"][4]
        assert meta["url_type"] == "upload" and meta["url"].endswith(".txt")
        link = call_action("package_show", id="gastos-de-viaticos")["resources"][0]
        assert link.get("url_type") != "upload"

    def test_copies_survive_the_next_run_without_downloads(self, source):
        harvest(source)
        csv = call_action("package_show", id="diarios-de-sesion")["resources"][0]

        portal, results = harvest(source)

        assert portal.file_calls() == []
        assert results["diarios-de-sesion"] == "unchanged"
        after = call_action("resource_show", id=csv["id"])
        assert after["url_type"] == "upload" and after["url"] == csv["url"]

    def test_a_newer_date_fetches_the_files_again(self, source):
        harvest(source)
        newer = changed_page("sesiones", "Última actualización: 14/9/2026",
                             "Última actualización: 20/12/2099")

        portal = FakePortal(file_content=b"periodo;anio\n149;2027\n")
        harvest(source, portal=portal, pages={"sesiones": newer})

        fetched = [u for u, _ in portal.file_calls()]
        assert len(fetched) == 5 and DIARIOS_CSV in fetched
        csv = call_action("package_show", id="diarios-de-sesion")["resources"][0]
        assert csv["hash"] == hashlib.sha256(b"periodo;anio\n149;2027\n").hexdigest()
        assert csv["source_last_modified"] == "2099-12-20"

    def test_a_failed_copy_stays_a_link_and_is_retried(self, source):
        harvest(source, portal=FakePortal(file_status=500))
        csv = call_action("package_show", id="diarios-de-sesion")["resources"][0]
        assert csv.get("url_type") != "upload" and csv["url"] == DIARIOS_CSV

        portal, results = harvest(source)

        assert results["diarios-de-sesion"] == "unchanged"
        assert len(portal.file_calls()) == 20
        assert call_action("resource_show", id=csv["id"])["url_type"] == "upload"


class TestPieces:

    def test_parse_page(self):
        datasets = legislatura.parse_page(PAGES["sesiones"][0])
        assert [d["slug"] for d in datasets] == [
            "asistencia-de-legisladores-a-sesiones-2020", "diarios-de-sesion", "ruta-de-los-expedientes"]
        diarios = datasets[1]
        assert diarios["url"] == DIARIOS and diarios["links"] == []
        assert [f["label"] for f in diarios["files"]][:3] == [".csv", ".xls", ".xml"]
        assert diarios["files"][4]["label"] == "metadato.txt"
        # the last heading of the page does not swallow the "back to the
        # portal" links after it
        assert datasets[2]["links"] == []

    def test_parse_metadata(self):
        assert legislatura.parse_metadata(METADATA) == {
            "tema": "Sesiones",
            "frecuencia": "semanal",
            "fuente": "Secretaría Legislativa de la Legislatura de la Provincia de Córdoba",
        }
        assert legislatura.parse_metadata("sin campos") == {}

    def test_iso_date(self):
        assert legislatura.iso_date("Última actualización: 4/9/2026") == "2026-09-04"
        assert legislatura.iso_date("Última actualización:31/8/2026") == "2026-08-31"
        assert legislatura.iso_date("Última actualización: 22/10/25") == "2025-10-22"
        assert legislatura.iso_date("sin fecha") == ""

    def test_sentence_case(self):
        assert legislatura.sentence_case("DIARIOS DE SESIÓN (2002 – 2026)") == "Diarios de sesión (2002 – 2026)"
        assert legislatura.sentence_case("Histórico de Autoridades") == "Histórico de Autoridades"
        assert legislatura.sentence_case("organigrama") == "organigrama"

    def test_file_name(self):
        n = LegislaturaHarvester._file_name
        assert n("T", {"url": PORTAL + "/wp-content/uploads/a.xlsx", "label": ".xls"}) == "T - XLSX"
        assert n("T", {"url": PORTAL + "/wp-content/uploads/a.zip", "label": ".xml"}) == "T - XML"
        assert n("T", {"url": PORTAL + "/wp-content/uploads/a.json", "label": ".st0{fill:#320EE2;}"}) == "T - JSON"
        assert n("T", {"url": PORTAL + "/wp-content/uploads/META.docx", "label": "metadato.txt"}) == "T - Metadatos"
        # a label that names the file (Bell Ville's budget page)
        assert n("Presupuesto", {"url": PORTAL + "/wp-content/uploads/2784_2025.pdf",
                                 "label": "Ordenanza Presupuestaria 2026"}) == \
            "Presupuesto - Ordenanza Presupuestaria 2026"
        assert n("T", {"url": PORTAL + "/wp-content/uploads/a.pdf", "label": ""}) == "T - PDF"

    def test_the_pages_api_is_asked_by_slug(self):
        portal = FakePortal()
        h = LegislaturaHarvester()
        h.config = {"pause": 0}
        with mock.patch("ckanext.cordoba_portal.legislatura.requests.get", portal):
            page = h._page(PORTAL, "sesiones")
        assert page["slug"] == "sesiones"
        url, params, _, _ = portal.calls[0]
        assert url == PORTAL + "/wp-json/wp/v2/pages"
        assert parse_qs("slug=%s&_fields=%s" % (params["slug"], params["_fields"]))["_fields"][0] \
            == legislatura.PAGE_FIELDS
