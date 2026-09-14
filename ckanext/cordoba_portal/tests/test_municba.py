"""The municba harvester: the city's portal, its versions and files."""
import hashlib
import json
import os
from datetime import datetime, timedelta
from unittest import mock
from urllib.parse import urlparse

import pytest
import requests

from ckan import model
from ckan.tests import factories
from ckan.tests.helpers import call_action
from ckanext.harvest.model import HarvestGatherError, HarvestObject
from ckanext.harvest.tests.factories import HarvestSourceObj, HarvestJobObj

from ckanext.cordoba_portal.municba import MuniCBAHarvester

PORTAL = "https://gobiernoabierto.cordoba.gob.ar"
API = PORTAL + "/api/datos-abiertos"
HERE = os.path.dirname(__file__)
with open(os.path.join(HERE, "data", "municba-api.json")) as f:
    FIXTURE = json.load(f)   # real answers of the portal's API, trimmed

FILE = b"%PDF-1.4 fake\n"
SHA = hashlib.sha256(FILE).hexdigest()
CONFIG = {"pause": 0, "copy_pause": 0, "user_agent": "Mozilla/5.0 test cbadatos.com.ar"}


class FakeResponse:
    def __init__(self, data=None, status_code=200, content=b"", headers=None):
        self._data = data
        self.status_code = status_code
        self._content = content
        self.headers = headers or {"ETag": '"abc"', "Content-Type": "application/pdf",
                                   "Content-Length": str(len(content))}

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


def page(results):
    return {"count": len(results), "page": 1, "size": 100, "next": None, "results": results}


class FakePortal:
    """requests.get of the portal: the API from the fixture, S3 files."""

    def __init__(self, file_status=200, file_content=FILE):
        self.calls = []
        self.file_status = file_status
        self.file_content = file_content

    def __call__(self, url, params=None, headers=None, **kwargs):
        self.calls.append((url, headers or {}))
        path = urlparse(url).path
        if "s3.amazonaws.com" in url:
            assert "X-Amz-Signature" in url, "files are fetched with a signed URL"
            return FakeResponse(status_code=self.file_status, content=self.file_content)
        if path == "/api/datos-abiertos/categoria":
            return FakeResponse(page(FIXTURE["categorias"]))
        if path == "/api/datos-abiertos/dato":
            return FakeResponse(page(FIXTURE["datos"]))
        parts = path.split("/")
        if path.endswith("/version-dato"):
            return FakeResponse(page(FIXTURE["versiones"].get(parts[-2], [])))
        if path.endswith("/recurso"):
            return FakeResponse(page(FIXTURE["recursos"].get(parts[-2], [])))
        return FakeResponse(status_code=404)

    def api_calls(self):
        return [u for u, _ in self.calls if "s3.amazonaws.com" not in u]

    def file_calls(self):
        return [(u, h) for u, h in self.calls if "s3.amazonaws.com" in u]


@pytest.fixture(autouse=True)
def forget_the_cached_harvest_user():
    MuniCBAHarvester()._user_name = None


@pytest.fixture
def source(with_plugins, clean_db):
    org = factories.Organization(name="municba", title="Municipalidad de Córdoba",
                                 source_portal="municba")
    return HarvestSourceObj(url=PORTAL, source_type="municba", owner_org=org["id"],
                            config=json.dumps(CONFIG), title="Muni CBA")


def harvest(source, portal=None, copy_files=True):
    """One whole run: gather, fetch, import. Returns (portal, results)."""
    portal = portal or FakePortal()
    job = HarvestJobObj(source=source)
    harvester = MuniCBAHarvester()
    results = {}
    with mock.patch("ckanext.cordoba_portal.municba.requests.get", portal):
        config = dict(CONFIG, copy_files=copy_files)
        source.config = json.dumps(config)
        ids = harvester.gather_stage(job)
        for object_id in ids or []:
            obj = HarvestObject.get(object_id)
            assert harvester.fetch_stage(obj)
            results[obj.guid] = harvester.import_stage(obj)
    job.status = "Finished"
    job.finished = datetime.utcnow()
    job.save()
    return portal, results


@pytest.mark.usefixtures("with_plugins", "clean_db")
class TestConfig:

    def test_empty_is_fine(self):
        assert MuniCBAHarvester().validate_config("") == ""

    def test_numbers_and_org_are_checked(self):
        with pytest.raises(ValueError):
            MuniCBAHarvester().validate_config(json.dumps({"pause": "slow"}))
        with pytest.raises(ValueError, match="single_org"):
            MuniCBAHarvester().validate_config(json.dumps({"single_org": "nope"}))

    def test_info(self):
        assert MuniCBAHarvester().info()["name"] == "municba"


class TestGather:

    def test_one_object_per_published_dataset(self, source):
        job = HarvestJobObj(source=source)
        portal = FakePortal()
        with mock.patch("ckanext.cordoba_portal.municba.requests.get", portal):
            ids = MuniCBAHarvester().gather_stage(job)

        assert len(ids) == 2    # the "No Publicado" one is left out
        guids = sorted(HarvestObject.get(i).guid for i in ids)
        assert guids == ["118", "3486"]
        content = json.loads(HarvestObject.get(ids[0]).content)
        assert content["dato"]["id"] == 118
        assert content["categoria"]["slug"] == "geografia-y-mapas"
        assert content["categoria"]["categoria_superior"]["slug"] == "urbanismo-y-territorio"
        # two requests: the categories and the datasets, one page each
        assert portal.api_calls() == [API + "/categoria", API + "/dato"]
        assert all(h["User-Agent"] == CONFIG["user_agent"] for _, h in portal.calls)

    def test_portal_down_is_a_gather_error(self, source):
        job = HarvestJobObj(source=source)
        with mock.patch("ckanext.cordoba_portal.municba.requests.get",
                        return_value=FakeResponse(status_code=500)):
            assert MuniCBAHarvester().gather_stage(job) is None
        errors = model.Session.query(HarvestGatherError).filter_by(harvest_job_id=job.id).all()
        assert errors and "500" in errors[0].message


class TestImport:

    def test_the_dataset(self, source):
        harvest(source, copy_files=False)

        dataset = call_action("package_show", id="barrios-de-la-ciudad")
        assert dataset["title"] == "Barrios de la ciudad"
        assert dataset["source_portal"] == "municba"
        assert dataset["source_url"] == \
            PORTAL + "/data/datos-abiertos/categoria/geografia-y-mapas/barrios-de-la-ciudad/118"
        assert dataset["organization"]["name"] == "municba"
        assert dataset["license_id"] == "cc-by-sa"
        assert dataset["notes"].startswith("Polígonos de barrios de la ciudad")
        extras = {e["key"]: e["value"] for e in dataset["extras"]}
        assert extras["periodicidad"] == "A demanda"
        assert extras["categoria"] == "Urbanismo y Territorio / Geografía y mapas"
        assert extras["fuente"] == "Dirección de Catastro"
        assert extras["creado_en_origen"] == "2016-11-02"
        assert sorted(g["name"] for g in dataset["groups"]) == \
            ["municba-geografia-y-mapas", "municba-urbanismo-y-territorio"]
        assert call_action("group_show", id="municba-geografia-y-mapas")["title"] == "Geografía y mapas"

        other = call_action("package_show", id="inversion-publica-en-ninez-y-adolescencia-muna-unicef")
        assert other["notes"] == ""     # "." on the portal means none
        assert sorted(t["name"] for t in other["tags"]) == ["niñez", "presupuesto"]
        assert [g["name"] for g in other["groups"]] == ["municba-administracion-publica"]
        assert call_action("package_search", q="")["count"] == 2

    def test_the_resources(self, source):
        harvest(source, copy_files=False)
        dataset = call_action("package_show", id="barrios-de-la-ciudad")
        by_name = {r["name"]: r for r in dataset["resources"]}

        # 3 versions: 6 + 2 + 0 resources (the 2022 one has none in the fixture)
        assert len(dataset["resources"]) == 8
        excel = by_name["Mapa de barrios de la ciudad - 2016 (Excel)"]
        assert excel["format"] == "XLS"
        assert excel["source_api"] == API + "/dato/118/version-dato/220/recurso/647"
        assert excel["url"] == dataset["source_url"]        # no lasting URL on the portal
        assert excel["source_url"] == dataset["source_url"]
        assert excel["source_last_modified"] == "2020-11-27T14:59:34.213763-03:00"
        assert excel["description"].startswith("Versión publicada el 2016-11-02.")
        assert "Fuente: Dirección de Catastro." in excel["description"]
        assert by_name["Mapa de barrios de la ciudad - 2016 (CSV con WKT)"]["format"] == "CSV"
        assert by_name["Mapa de barrios de la ciudad - 2016 (KML Barrios de Córdoba)"]["format"] == "KML"
        assert by_name["Mapa de barrios de la ciudad - 2016 (SHP Barrios)"]["format"] == "RAR"
        assert by_name["Mapa de barrios de la ciudad - 2023"]["format"] == "KMZ"
        link = by_name["Mapa de barrios de la ciudad - 2023 (Barrios online)"]
        assert link["format"] == "HTML"
        assert link["url"] == "https://comunidadinteligente.cordoba.gob.ar/catastro/"
        assert not link.get("source_api") and not link.get("source_url")

    def test_second_run_is_unchanged_and_stable(self, source):
        harvest(source, copy_files=False)
        before = call_action("package_show", id="barrios-de-la-ciudad")

        portal, results = harvest(source, copy_files=False)

        assert results == {"118": "unchanged", "3486": "unchanged"}
        after = call_action("package_show", id="barrios-de-la-ciudad")
        assert after["id"] == before["id"]
        assert sorted(r["id"] for r in after["resources"]) == sorted(r["id"] for r in before["resources"])
        assert after["metadata_modified"] == before["metadata_modified"]

    def test_a_changed_version_updates_the_dataset(self, source):
        harvest(source, copy_files=False)
        changed = json.loads(json.dumps(FIXTURE))
        changed["versiones"]["118"][0]["titulo"] = "Mapa de barrios de la ciudad - 2027"

        with mock.patch.dict("ckanext.cordoba_portal.tests.test_municba.FIXTURE", changed):
            portal, results = harvest(source, copy_files=False)

        assert results["118"] is True and results["3486"] == "unchanged"
        names = [r["name"] for r in call_action("package_show", id="barrios-de-la-ciudad")["resources"]]
        assert any(n.startswith("Mapa de barrios de la ciudad - 2027") for n in names)
        assert "Mapa de barrios de la ciudad - 2023" not in names

    def test_a_name_taken_by_another_portal(self, source):
        org = factories.Organization()
        factories.Dataset(name="barrios-de-la-ciudad", owner_org=org["id"],
                          source_portal="datosgestionabierta")

        harvest(source, copy_files=False)

        ours = call_action("package_search", fq="source_portal:municba")["results"]
        names = sorted(d["name"] for d in ours)
        assert names[0].startswith("barrios-de-la-ciudad") and names[0] != "barrios-de-la-ciudad"


class TestCopy:

    def test_files_are_copied_with_a_fresh_url(self, source):
        portal, results = harvest(source)

        # every file of the portal, once; each after asking its version's
        # resources again for a signed URL
        assert len(portal.file_calls()) == 7
        assert all(h["User-Agent"] == CONFIG["user_agent"] for _, h in portal.file_calls())
        dataset = call_action("package_show", id="barrios-de-la-ciudad")
        by_name = {r["name"]: r for r in dataset["resources"]}
        excel = by_name["Mapa de barrios de la ciudad - 2016 (Excel)"]
        assert excel["url_type"] == "upload"
        assert excel["url"].endswith("/download/barrios-simple.xls")
        assert excel["hash"] == SHA
        assert excel["source_etag"] == '"abc"'
        assert excel["source_downloaded"]
        assert excel["source_api"] == API + "/dato/118/version-dato/220/recurso/647"
        assert excel["source_url"] == dataset["source_url"]
        link = by_name["Mapa de barrios de la ciudad - 2023 (Barrios online)"]
        assert link.get("url_type") != "upload"

    def test_copies_survive_the_next_run_without_requests(self, source):
        harvest(source)
        dataset = call_action("package_show", id="barrios-de-la-ciudad")
        excel = next(r for r in dataset["resources"] if r["name"].endswith("(Excel)"))

        portal, results = harvest(source)

        assert portal.file_calls() == []
        assert results["118"] == "unchanged"
        after = call_action("resource_show", id=excel["id"])
        assert after["url_type"] == "upload" and after["hash"] == SHA
        assert after["url"] == excel["url"]

    def test_a_copy_is_rechecked_after_recheck_days(self, source):
        harvest(source)
        dataset = call_action("package_show", id="barrios-de-la-ciudad")
        old = (datetime.utcnow() - timedelta(days=40)).replace(microsecond=0).isoformat()
        for r in dataset["resources"]:
            if r.get("source_api"):
                call_action("resource_patch", id=r["id"], source_downloaded=old)

        portal = FakePortal(file_status=304, file_content=b"")
        harvest(source, portal=portal)

        assert len(portal.file_calls()) == 6     # the files of that dataset only
        assert all(h["If-None-Match"] == '"abc"' for _, h in portal.file_calls())
        excel = next(r for r in call_action("package_show", id="barrios-de-la-ciudad")["resources"]
                     if r["name"].endswith("(Excel)"))
        assert excel["hash"] == SHA and excel["source_downloaded"] > old

    def test_a_failed_copy_stays_a_link_and_is_retried(self, source):
        portal = FakePortal(file_status=500)
        harvest(source, portal=portal)
        dataset = call_action("package_show", id="barrios-de-la-ciudad")
        excel = next(r for r in dataset["resources"] if r["name"].endswith("(Excel)"))
        assert excel.get("url_type") != "upload"
        assert excel["url"] == dataset["source_url"]

        portal, results = harvest(source)

        assert results["118"] == "unchanged"
        assert len(portal.file_calls()) == 7
        assert call_action("resource_show", id=excel["id"])["url_type"] == "upload"


class TestPieces:

    def test_format(self):
        f = MuniCBAHarvester._format
        assert f({"url": "https://x/datos/a.xlsx?X-Amz=1", "icono": "xls"}) == "XLSX"
        assert f({"url": "https://x/datos/N%C3%B3mina.csv", "icono": "xls"}) == "CSV"
        assert f({"url": "https://x/datos/linea-ferrea.rar", "icono": "map"}) == "RAR"
        assert f({"url": "https://docs.google.com/spreadsheets/d/1/edit", "icono": "web"}) == "HTML"
        assert f({"url": "https://docs.google.com/x", "icono": "googlesheet"}) == "Google Sheets"
        assert f({"url": "https://x/mapa", "icono": "map"}) == "KML"
        assert f({"url": None, "icono": None}) == ""

    def test_resource_name(self):
        n = MuniCBAHarvester._resource_name
        assert n({"titulo": "Mapa 2016"}, {"titulo": "Excel"}) == "Mapa 2016 (Excel)"
        assert n({"titulo": "Mapa 2016"}, {"titulo": "Mapa 2016"}) == "Mapa 2016"
        assert n({"titulo": ""}, {"titulo": "Excel"}) == "Excel"
        assert n({"titulo": "Mapa"}, {"titulo": None}) == "Mapa"

    def test_license(self):
        lic = MuniCBAHarvester._license
        cc = {"licencia": "CC-BY-SA-AR (CBA)", "licencia_libre": True}
        assert lic([{"recursos": [{"licencia": cc}]}]) == "cc-by-sa"
        assert lic([{"recursos": [{"licencia": {"licencia": "CC BY 4.0", "licencia_libre": True}}]}]) == "cc-by"
        assert lic([{"recursos": [{"licencia": {"licencia": "Propia", "licencia_libre": True}}]}]) == "other-open"
        assert lic([{"recursos": [{"licencia": None}]}]) == "notspecified"

    def test_tags(self):
        assert MuniCBAHarvester._tags(["bicileta", " ciclovía ", "a", "x/y", "bicileta"]) == \
            ["bicileta", "ciclovía", "xy"]

    def test_fingerprint_ignores_signatures(self):
        a = '{"url": "https://s3/a.pdf?X-Amz-Date=1&X-Amz-Signature=x"}'
        b = '{"url": "https://s3/a.pdf?X-Amz-Date=2&X-Amz-Signature=y"}'
        assert MuniCBAHarvester._fingerprint(a) == MuniCBAHarvester._fingerprint(b)
        assert MuniCBAHarvester._fingerprint(a) != MuniCBAHarvester._fingerprint(a.replace("a.pdf", "b.pdf"))
