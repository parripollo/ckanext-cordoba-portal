"""The villamaria harvester: the sitemap, the dataset pages and their files."""
import hashlib
import json
import os
from datetime import datetime
from unittest import mock
from urllib.parse import urlparse

import pytest
import requests

from ckan import model
from ckan.tests import factories
from ckan.tests.helpers import call_action
from ckanext.harvest.model import HarvestGatherError, HarvestObject
from ckanext.harvest.tests.factories import HarvestSourceObj, HarvestJobObj

from ckanext.cordoba_portal import villamaria
from ckanext.cordoba_portal.villamaria import VillaMariaHarvester

PORTAL = "https://datos.villamaria.gob.ar"
HERE = os.path.dirname(__file__)


def fixture(name):
    with open(os.path.join(HERE, "data", "villamaria-" + name), encoding="utf-8") as f:
        return f.read()   # real pages of the portal, trimmed


PAGES = {
    "/sitemap.xml": fixture("sitemap.xml"),
    "/datasets/vacunaciones-por-vacuna": fixture("vacunaciones.html"),
    "/datasets/parcelas": fixture("parcelas.html"),
}
PAGE_2 = fixture("vacunaciones-page2.html")
FILE = b"PK\x03\x04 fake xlsx\n"
SHA = hashlib.sha256(FILE).hexdigest()
CONFIG = {"pause": 0, "copy_pause": 0, "user_agent": "Mozilla/5.0 test cbadatos.com.ar"}
CSRF = "aEJrrKdUFEWLj6i6Q2GDO3zorihc1ZRK"


class FakeResponse:
    def __init__(self, text="", status_code=200, content=b"", headers=None, cookies=None):
        self.text = text
        self.status_code = status_code
        self._content = content
        self.headers = headers or {}
        self.cookies = cookies or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("HTTP %s" % self.status_code)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, chunk_size):
        for i in range(0, len(self._content), chunk_size):
            yield self._content[i:i + chunk_size]


class FakePortal:
    """requests.get and requests.post of the portal: the pages from the
    fixtures, the htmx fragment, the downloads."""

    def __init__(self, file_status=200, file_content=FILE):
        self.gets = []
        self.posts = []
        self.file_status = file_status
        self.file_content = file_content

    def get(self, url, headers=None, **kwargs):
        self.gets.append((url, headers or {}))
        path = urlparse(url).path
        if path.startswith("/recurso/"):
            name = "vacunaciones-por-vacuna-%s.xlsx" % path.split("/")[2][:8]
            return FakeResponse(status_code=self.file_status, content=self.file_content, headers={
                "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "Content-Length": str(len(self.file_content)),
                "Content-Disposition": 'attachment; filename="%s"' % name,
            })
        if path in PAGES:
            return FakeResponse(PAGES[path], cookies={"__Secure-csrftoken": CSRF})
        return FakeResponse(status_code=404)

    def post(self, url, data=None, headers=None, cookies=None, **kwargs):
        self.posts.append((url, data, headers or {}, cookies or {}))
        if (headers or {}).get("X-CSRFToken") != CSRF:
            return FakeResponse(status_code=403)
        return FakeResponse(PAGE_2)   # the last page, for every page asked

    def file_calls(self):
        return [(u, h) for u, h in self.gets if "/recurso/" in u]


@pytest.fixture(autouse=True)
def forget_the_cached_harvest_user():
    VillaMariaHarvester()._user_name = None


@pytest.fixture
def source(with_plugins, clean_db):
    org = factories.Organization(name="villamaria", title="Municipalidad de Villa María",
                                 source_portal="villamaria")
    return HarvestSourceObj(url=PORTAL, source_type="villamaria", owner_org=org["id"],
                            config=json.dumps(CONFIG), title="Villa María")


def harvest(source, portal=None, copy_files=True, pages=None):
    """One whole run: gather, fetch, import. Returns (portal, results)."""
    portal = portal or FakePortal()
    job = HarvestJobObj(source=source)
    harvester = VillaMariaHarvester()
    results = {}
    with mock.patch("ckanext.cordoba_portal.villamaria.requests.get", portal.get), \
            mock.patch("ckanext.cordoba_portal.harvester.requests.get", portal.get), \
            mock.patch("ckanext.cordoba_portal.villamaria.requests.post", portal.post), \
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


@pytest.mark.usefixtures("with_plugins", "clean_db")
class TestConfig:

    def test_empty_is_fine(self):
        assert VillaMariaHarvester().validate_config("") == ""

    def test_numbers_and_org_are_checked(self):
        with pytest.raises(ValueError):
            VillaMariaHarvester().validate_config(json.dumps({"pause": "slow"}))
        with pytest.raises(ValueError, match="single_org"):
            VillaMariaHarvester().validate_config(json.dumps({"single_org": "nope"}))

    def test_info(self):
        assert VillaMariaHarvester().info()["name"] == "villamaria"


class TestGather:

    def test_one_object_per_dataset_of_the_sitemap(self, source):
        job = HarvestJobObj(source=source)
        portal = FakePortal()
        with mock.patch("ckanext.cordoba_portal.villamaria.requests.get", portal.get):
            ids = VillaMariaHarvester().gather_stage(job)

        assert sorted(HarvestObject.get(i).guid for i in ids) == \
            ["parcelas", "vacunaciones-por-vacuna"]      # not the home, not "acerca"
        content = json.loads(HarvestObject.get(ids[0]).content)
        assert content["url"] == PORTAL + "/datasets/parcelas"
        assert content["lastmod"] == "2026-02-26"
        assert portal.gets == [(PORTAL + "/sitemap.xml", {"User-Agent": CONFIG["user_agent"]})]

    def test_portal_down_is_a_gather_error(self, source):
        job = HarvestJobObj(source=source)
        with mock.patch("ckanext.cordoba_portal.villamaria.requests.get",
                        return_value=FakeResponse(status_code=500)):
            assert VillaMariaHarvester().gather_stage(job) is None
        errors = model.Session.query(HarvestGatherError).filter_by(harvest_job_id=job.id).all()
        assert errors and "500" in errors[0].message


class TestFetch:

    def test_the_pages_of_resources_are_asked_with_the_csrf_cookie(self, source):
        portal, _ = harvest(source, copy_files=False)

        # one GET per dataset page; one POST for the second (last) page of
        # vacunaciones, none for parcelas (no "Cargar más")
        assert [u for u, _ in portal.gets] == [
            PORTAL + "/sitemap.xml",
            PORTAL + "/datasets/parcelas",
            PORTAL + "/datasets/vacunaciones-por-vacuna",
        ]
        assert len(portal.posts) == 1
        url, data, headers, cookies = portal.posts[0]
        assert url == PORTAL + "/datasets/vacunaciones-por-vacuna"
        assert data == {"page": "2", "search": ""}
        assert headers["Referer"] == url and headers["User-Agent"] == CONFIG["user_agent"]
        assert cookies == {"__Secure-csrftoken": CSRF}

    def test_a_page_without_a_dataset_is_a_fetch_error(self, source):
        job = HarvestJobObj(source=source)
        obj = HarvestObject(guid="nada", job=job, content=json.dumps(
            {"slug": "nada", "url": PORTAL + "/datasets/nada", "lastmod": ""}))
        obj.save()
        with mock.patch("ckanext.cordoba_portal.villamaria.requests.get", FakePortal().get):
            assert VillaMariaHarvester().fetch_stage(obj) is False


class TestImport:

    def test_the_dataset(self, source):
        harvest(source, copy_files=False)

        dataset = call_action("package_show", id="vacunaciones-por-vacuna")
        assert dataset["title"] == "Vacunaciones por vacuna"
        assert dataset["notes"] == \
            "Cantidad de vacunas aplicadas y personas vacunadas, clasificado por vacuna."
        assert dataset["source_portal"] == "villamaria"
        assert dataset["source_url"] == PORTAL + "/datasets/vacunaciones-por-vacuna"
        assert dataset["organization"]["name"] == "villamaria"
        assert dataset["license_id"] == "notspecified"
        extras = {e["key"]: e["value"] for e in dataset["extras"] if not e["key"].startswith("harvest_")}
        assert extras == {
            "periodicidad": "Mensual",
            "categoria": "Salud",
            "area": "Secretaría de Salud",
            "actualizado_en_origen": "2026-09-10",
        }
        assert [g["name"] for g in dataset["groups"]] == ["villamaria-salud"]
        assert call_action("group_show", id="villamaria-salud")["title"] == "Salud"

        other = call_action("package_show", id="parcelas")
        assert [g["name"] for g in other["groups"]] == ["villamaria-desarrollo-urbano"]
        assert call_action("package_search", q="")["count"] == 2

    def test_the_resources(self, source):
        harvest(source, copy_files=False)

        dataset = call_action("package_show", id="vacunaciones-por-vacuna")
        names = [r["name"] for r in dataset["resources"]]
        # 3 on the page + 2 on the second page, newest first
        assert names == [
            "Vacunaciones por vacuna - Agosto 2026",
            "Vacunaciones por vacuna - Julio 2026",
            "Vacunaciones por vacuna - Junio 2026",
            "Vacunaciones por vacuna - Julio 2020",
            "Vacunaciones por vacuna - Junio 2020",
        ]
        first = dataset["resources"][0]
        assert first["format"] == "XLSX"
        assert first["url"] == PORTAL + "/recurso/98cffc77-cff3-43f2-8ad5-d9b92a67f7a8/descargar"
        assert first["source_url"] == first["url"]
        assert first["source_last_modified"] == "2026-09-10"

        parcelas = {r["name"]: r for r in call_action("package_show", id="parcelas")["resources"]}
        viewer = parcelas["Parcelas - 2026"]
        assert viewer["url"] == "https://mapascordoba.gob.ar/viewer/mapa/328"
        assert viewer["format"] == "HTML"
        assert not viewer.get("source_url")
        assert parcelas["Parcelas - 2021 (Shapefile)"]["format"] == "ZIP"

    def test_second_run_is_unchanged_and_stable(self, source):
        harvest(source, copy_files=False)
        before = call_action("package_show", id="vacunaciones-por-vacuna")

        portal, results = harvest(source, copy_files=False)

        assert results == {"parcelas": "unchanged", "vacunaciones-por-vacuna": "unchanged"}
        after = call_action("package_show", id="vacunaciones-por-vacuna")
        assert after["id"] == before["id"]
        assert [r["id"] for r in after["resources"]] == [r["id"] for r in before["resources"]]
        assert after["metadata_modified"] == before["metadata_modified"]

    def test_a_changed_page_updates_the_dataset(self, source):
        harvest(source, copy_files=False)
        changed = PAGES["/datasets/parcelas"].replace(
            "Parcelas de la ciudad de Villa María", "Parcelas de Villa María")

        portal, results = harvest(source, copy_files=False,
                                  pages={"/datasets/parcelas": changed})

        assert results["parcelas"] is True and results["vacunaciones-por-vacuna"] == "unchanged"
        assert call_action("package_show", id="parcelas")["notes"].startswith("Parcelas de Villa María")

    def test_a_name_taken_by_another_portal(self, source):
        org = factories.Organization()
        factories.Dataset(name="parcelas", owner_org=org["id"], source_portal="datosgestionabierta")

        harvest(source, copy_files=False)

        ours = call_action("package_search", fq="source_portal:villamaria")["results"]
        names = sorted(d["name"] for d in ours)
        assert names[0].startswith("parcelas") and names[0] != "parcelas"


class TestCopy:

    def test_files_are_copied_and_named_by_the_portal(self, source):
        portal, results = harvest(source)

        # every download of the portal, once; not the map viewer
        assert len(portal.file_calls()) == 6
        assert all(h["User-Agent"] == CONFIG["user_agent"] for _, h in portal.file_calls())
        dataset = call_action("package_show", id="vacunaciones-por-vacuna")
        first = dataset["resources"][0]
        assert first["url_type"] == "upload"
        assert first["url"].endswith("/download/vacunaciones-por-vacuna-98cffc77.xlsx")
        assert first["hash"] == SHA
        assert first["source_downloaded"]
        assert first["source_url"] == PORTAL + "/recurso/98cffc77-cff3-43f2-8ad5-d9b92a67f7a8/descargar"
        viewer = next(r for r in call_action("package_show", id="parcelas")["resources"]
                      if r["name"] == "Parcelas - 2026")
        assert viewer.get("url_type") != "upload"

    def test_copies_survive_the_next_run_without_downloads(self, source):
        harvest(source)
        first = call_action("package_show", id="vacunaciones-por-vacuna")["resources"][0]

        portal, results = harvest(source)

        assert portal.file_calls() == []
        assert results["vacunaciones-por-vacuna"] == "unchanged"
        after = call_action("resource_show", id=first["id"])
        assert after["url_type"] == "upload" and after["hash"] == SHA
        assert after["url"] == first["url"]

    def test_a_newer_date_on_the_portal_fetches_the_file_again(self, source):
        harvest(source)
        first = call_action("package_show", id="vacunaciones-por-vacuna")["resources"][0]
        newer = PAGES["/datasets/vacunaciones-por-vacuna"].replace("10/09/2026", "20/12/2099")

        portal = FakePortal(file_content=b"PK new content")
        harvest(source, portal=portal, pages={"/datasets/vacunaciones-por-vacuna": newer})

        assert [u for u, _ in portal.file_calls()] == [first["source_url"]]
        after = call_action("resource_show", id=first["id"])
        assert after["hash"] == hashlib.sha256(b"PK new content").hexdigest()
        assert after["source_last_modified"] == "2099-12-20"

    def test_a_failed_copy_stays_a_link_and_is_retried(self, source):
        portal = FakePortal(file_status=500)
        harvest(source, portal=portal)
        first = call_action("package_show", id="vacunaciones-por-vacuna")["resources"][0]
        assert first.get("url_type") != "upload"
        assert first["url"] == first["source_url"]

        portal, results = harvest(source)

        assert results["vacunaciones-por-vacuna"] == "unchanged"
        assert len(portal.file_calls()) == 6
        assert call_action("resource_show", id=first["id"])["url_type"] == "upload"


class TestPieces:

    def test_sitemap(self):
        assert villamaria.parse_sitemap(PAGES["/sitemap.xml"]) == \
            [("parcelas", "2026-02-26"), ("vacunaciones-por-vacuna", "2026-09-10")]
        assert villamaria.parse_sitemap("<urlset></urlset>") == []

    def test_dataset_page(self):
        assert villamaria.parse_dataset(PAGES["/datasets/parcelas"]) == {
            "title": "Parcelas",
            "notes": "Parcelas de la ciudad de Villa María con coordenadas geográficas.",
            "category": "Desarrollo Urbano",
            "date": "2026-02-26",
            "frequency": "Anual",
            "area": "Secretaría de Infraestructura y Desarrollo Sostenible",
        }
        assert villamaria.parse_dataset("<html><body>404</body></html>")["title"] == ""

    def test_resources_and_next_page(self):
        resources, next_page = villamaria.parse_resources(PAGES["/datasets/vacunaciones-por-vacuna"], PORTAL)
        assert next_page == 2
        assert [r["period"] for r in resources] == ["Agosto 2026", "Julio 2026", "Junio 2026"]
        assert resources[0] == {
            "title": "Vacunaciones por vacuna", "period": "Agosto 2026", "date": "2026-09-10",
            "format": "XLSX", "file": True,
            "url": PORTAL + "/recurso/98cffc77-cff3-43f2-8ad5-d9b92a67f7a8/descargar",
        }
        resources, next_page = villamaria.parse_resources(PAGE_2, PORTAL)
        assert next_page is None and len(resources) == 2
        assert villamaria.parse_resources("<div>nothing</div>", PORTAL) == ([], None)

    def test_iso_date(self):
        assert villamaria.iso_date("10/09/2026") == "2026-09-10"
        assert villamaria.iso_date("1/2/2026") == "1/2/2026"
