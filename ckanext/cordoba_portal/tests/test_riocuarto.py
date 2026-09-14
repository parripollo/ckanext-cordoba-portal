"""The riocuarto harvester: the Next.js section data, its lists and files."""
import hashlib
import json
import os
from datetime import datetime, timedelta
from unittest import mock
from urllib.parse import urlparse, parse_qs

import pytest
import requests

from ckan.tests import factories
from ckan.tests.helpers import call_action
from ckanext.harvest.model import HarvestObject
from ckanext.harvest.tests.factories import HarvestSourceObj, HarvestJobObj

from ckanext.cordoba_portal import riocuarto
from ckanext.cordoba_portal.riocuarto import RioCuartoHarvester

PORTAL = "https://economiariocuarto.gob.ar"
BUILD = "Dq4WjjaG2MXzyGOWA-LPI"
HERE = os.path.dirname(__file__)


def fixture(name):
    with open(os.path.join(HERE, "data", "riocuarto-" + name), encoding="utf-8") as f:
        return f.read()   # real answers of the portal, trimmed


PAGE = fixture("transparencia.html")
SECTIONS = {slug: json.loads(fixture(slug + ".json")) for slug in riocuarto.SECTIONS}
FILE = b"%PDF-1.7 fake\n"
SHA = hashlib.sha256(FILE).hexdigest()
CONFIG = {"pause": 0, "copy_pause": 0, "user_agent": "Mozilla/5.0 test cbadatos.com.ar"}
PRESUPUESTO_VIEW = "https://drive.google.com/file/d/14-hD-VdluVnyBiHczDZCjT-vG2bUpabh/view?usp=sharing"
PRESUPUESTO_DOWNLOAD = "https://drive.google.com/uc?export=download&id=14-hD-VdluVnyBiHczDZCjT-vG2bUpabh"
DDJJ = "https://prod.ddjj.riocuarto.gob.ar/ddjj_publicas/01M11MF3BK0ZJT2V4EZ1V48XHB.pdf"


class FakeResponse:
    def __init__(self, data=None, text="", status_code=200, content=b"", headers=None):
        self._data = data
        self.text = text
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
    """requests.get of the portal: the page, the section JSON, Drive and
    the sworn statements' server."""

    def __init__(self, file_status=200, file_content=FILE, drive_html=False):
        self.calls = []
        self.file_status = file_status
        self.file_content = file_content
        self.drive_html = drive_html

    def __call__(self, url, params=None, headers=None, **kwargs):
        self.calls.append((url, headers or {}, kwargs.get("stream", False)))
        parsed = urlparse(url)
        if parsed.netloc == "economiariocuarto.gob.ar":
            if parsed.path == "/transparencia":
                return FakeResponse(text=PAGE)
            if parsed.path.startswith("/_next/data/%s/transparencia/" % BUILD):
                slug = parsed.path.rsplit("/", 1)[-1][:-5]
                return FakeResponse(SECTIONS[slug]) if slug in SECTIONS else FakeResponse(status_code=404)
            return FakeResponse(status_code=404)
        if parsed.netloc == "drive.google.com" and parsed.path == "/uc":
            if self.drive_html:
                return FakeResponse(content=b"<html>Google Drive can't scan this file</html>",
                                    headers={"Content-Type": "text/html; charset=utf-8"})
            file_id = parse_qs(parsed.query)["id"][0]
            return FakeResponse(status_code=self.file_status, content=self.file_content, headers={
                "Content-Type": "application/octet-stream",
                "Content-Length": str(len(self.file_content)),
                "Content-Disposition": 'attachment; filename="Documento %s.pdf"' % file_id[:4],
                "Last-Modified": "Wed, 05 Feb 2025 15:33:05 GMT"})
        if parsed.netloc == "prod.ddjj.riocuarto.gob.ar":
            return FakeResponse(status_code=self.file_status, content=self.file_content, headers={
                "Content-Type": "application/pdf", "Content-Length": str(len(self.file_content)),
                "ETag": '"ddjj-1"'})
        return FakeResponse(status_code=404)

    def file_calls(self):
        return [(u, h) for u, h, stream in self.calls if stream]


@pytest.fixture(autouse=True)
def forget_the_cached_harvest_user():
    RioCuartoHarvester()._user_name = None


@pytest.fixture
def source(with_plugins, clean_db):
    org = factories.Organization(name="riocuarto", title="Municipalidad de Río Cuarto",
                                 source_portal="riocuarto")
    return HarvestSourceObj(url=PORTAL, source_type="riocuarto", owner_org=org["id"],
                            config=json.dumps(CONFIG), title="Río Cuarto")


def harvest(source, portal=None, copy_files=True, sections=None):
    """One whole run: gather, fetch, import. Returns (portal, results)."""
    portal = portal or FakePortal()
    job = HarvestJobObj(source=source)
    harvester = RioCuartoHarvester()
    results = {}
    with mock.patch("ckanext.cordoba_portal.riocuarto.requests.get", portal), \
            mock.patch("ckanext.cordoba_portal.harvester.requests.get", portal), \
            mock.patch.dict(SECTIONS, sections or {}):
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
        assert RioCuartoHarvester().validate_config("") == ""

    def test_numbers_and_org_are_checked(self):
        with pytest.raises(ValueError):
            RioCuartoHarvester().validate_config(json.dumps({"copy_pause": "slow"}))
        with pytest.raises(ValueError, match="single_org"):
            RioCuartoHarvester().validate_config(json.dumps({"single_org": "nope"}))

    def test_info(self):
        assert RioCuartoHarvester().info()["name"] == "riocuarto"


class TestGather:

    def test_one_object_per_dataset_of_the_sections(self, source):
        job = HarvestJobObj(source=source)
        portal = FakePortal()
        with mock.patch("ckanext.cordoba_portal.riocuarto.requests.get", portal):
            ids = RioCuartoHarvester().gather_stage(job)

        assert sorted(HarvestObject.get(i).guid for i in ids) == [
            "boletin-oficial/items", "declaraciones-juradas/*", "escala-salarial/items",
            "informacion-economica-financiera/deudas", "informacion-economica-financiera/presupuesto",
            "informacion-economica-financiera/recaudacion"]
        content = json.loads(HarvestObject.get(ids[0]).content)
        assert content["section"] == {"slug": "informacion-economica-financiera",
                                      "title": "Información económica y financiera",
                                      "url": PORTAL + "/transparencia/informacion-economica-financiera"}
        assert content["dataset"]["title"] == "Presupuesto municipal"
        assert content["dataset"]["items"][1] == {
            "title": "Presupuesto 2026", "url": PRESUPUESTO_VIEW,
            "status": "Vigente", "category": "presupuesto"}
        # the page for the build id, then one JSON per section
        paths = [urlparse(u).path for u, _, _ in portal.calls]
        assert paths[0] == "/transparencia"
        assert paths[1:] == ["/_next/data/%s/transparencia/%s.json" % (BUILD, s) for s in riocuarto.SECTIONS]
        assert all(h["User-Agent"] == CONFIG["user_agent"] for _, h, _ in portal.calls)

    def test_a_section_that_fails_is_an_error_the_rest_goes_on(self, source):
        job = HarvestJobObj(source=source)
        with mock.patch.dict(SECTIONS), mock.patch("ckanext.cordoba_portal.riocuarto.requests.get", FakePortal()):
            del SECTIONS["boletin-oficial"]
            ids = RioCuartoHarvester().gather_stage(job)
        assert len(ids) == 5

    def test_portal_down_is_a_gather_error(self, source):
        job = HarvestJobObj(source=source)
        with mock.patch("ckanext.cordoba_portal.riocuarto.requests.get",
                        return_value=FakeResponse(status_code=500)):
            assert RioCuartoHarvester().gather_stage(job) is None

    def test_no_build_id_is_a_gather_error(self, source):
        job = HarvestJobObj(source=source)
        with mock.patch("ckanext.cordoba_portal.riocuarto.requests.get",
                        return_value=FakeResponse(text="<html>no next here</html>")):
            assert RioCuartoHarvester().gather_stage(job) is None


class TestImport:

    def test_the_dataset(self, source):
        harvest(source, copy_files=False)

        dataset = call_action("package_show", id="presupuesto-municipal")
        assert dataset["title"] == "Presupuesto municipal"
        assert dataset["notes"].startswith("Ordenanzas de presupuesto")
        assert dataset["source_portal"] == "riocuarto"
        assert dataset["source_url"] == PORTAL + "/transparencia/informacion-economica-financiera"
        assert dataset["organization"]["name"] == "riocuarto"
        extras = {e["key"]: e["value"] for e in dataset["extras"] if not e["key"].startswith("harvest_")}
        assert extras == {"seccion": "Información económica y financiera", "documentos_vigentes": "2"}
        assert [g["name"] for g in dataset["groups"]] == ["riocuarto-informacion-economica-financiera"]
        assert call_action("group_show", id="riocuarto-informacion-economica-financiera")["title"] == \
            "Información económica y financiera"
        deudas = call_action("package_show", id="deuda-publica-municipal")
        assert {e["key"]: e["value"] for e in deudas["extras"]}["documentos_vigentes"] == "1"
        assert call_action("package_search", q="")["count"] == 6

    def test_the_resources(self, source):
        harvest(source, copy_files=False)

        dataset = call_action("package_show", id="presupuesto-municipal")
        by_name = {r["name"]: r for r in dataset["resources"]}
        assert list(by_name) == ["Nota Elevación Presupuesto 2026", "Presupuesto 2026"]
        budget = by_name["Presupuesto 2026"]
        assert budget["url"] == PRESUPUESTO_VIEW                 # the viewer, until copied
        assert budget["source_url"] == PRESUPUESTO_DOWNLOAD
        assert budget["format"] == ""                             # Drive does not say
        assert budget["description"] == "Vigente."
        deudas = call_action("package_show", id="deuda-publica-municipal")
        assert deudas["resources"][1]["description"] == "No vigente (versión anterior)."

        bulletin = call_action("package_show", id="boletin-oficial-municipal")
        folder = bulletin["resources"][0]
        assert folder["name"] == "Enero 2024" and folder["format"] == "Google Drive"
        assert folder["url"].startswith("https://drive.google.com/drive/folders/")
        assert not folder.get("source_url")                       # a folder is a link

        ddjj = call_action("package_show", id="declaraciones-juradas-patrimoniales")
        names = [r["name"] for r in ddjj["resources"]]
        assert names == ["Intendente Municipal - Guillermo Luis De Rivas",
                         "Secretaría de Gobierno - Roberto Ricardo Koch",
                         "Secretaría de Gestión y Participación Ciudadana - Karin Ruth Bogni"]
        assert ddjj["resources"][0]["format"] == "PDF"
        assert ddjj["resources"][0]["source_url"] == DDJJ

    def test_second_run_is_unchanged_and_stable(self, source):
        harvest(source, copy_files=False)
        before = call_action("package_show", id="presupuesto-municipal")

        portal, results = harvest(source, copy_files=False)

        assert set(results.values()) == {"unchanged"}
        after = call_action("package_show", id="presupuesto-municipal")
        assert after["id"] == before["id"]
        assert [r["id"] for r in after["resources"]] == [r["id"] for r in before["resources"]]
        assert after["metadata_modified"] == before["metadata_modified"]

    def test_a_new_document_updates_the_dataset(self, source):
        harvest(source, copy_files=False)
        changed = json.loads(json.dumps(SECTIONS["informacion-economica-financiera"]))
        changed["pageProps"]["presupuesto"].insert(0, {
            "title": "Presupuesto 2027", "category": "presupuesto", "status": "Vigente",
            "url": "https://drive.google.com/file/d/1NEW/view?usp=drive_link"})

        portal, results = harvest(source, copy_files=False,
                                  sections={"informacion-economica-financiera": changed})

        assert results["informacion-economica-financiera/presupuesto"] is True
        assert results["escala-salarial/items"] == "unchanged"
        names = [r["name"] for r in call_action("package_show", id="presupuesto-municipal")["resources"]]
        assert names[0] == "Presupuesto 2027" and len(names) == 3

    def test_the_same_file_under_two_titles_is_two_resources(self, source):
        changed = json.loads(json.dumps(SECTIONS["informacion-economica-financiera"]))
        changed["pageProps"]["presupuesto"].append({
            "title": "Presupuesto 2026 (copia)", "category": "presupuesto", "status": "Vigente",
            "url": PRESUPUESTO_VIEW.replace("usp=sharing", "usp=drive_link")})

        portal, results = harvest(source, copy_files=False,
                                  sections={"informacion-economica-financiera": changed})

        assert results["informacion-economica-financiera/presupuesto"] is True
        resources = call_action("package_show", id="presupuesto-municipal")["resources"]
        assert [r["name"] for r in resources] == ["Nota Elevación Presupuesto 2026", "Presupuesto 2026",
                                                  "Presupuesto 2026 (copia)"]
        assert len({r["id"] for r in resources}) == 3

    def test_a_drive_link_with_another_usp_is_the_same_resource(self, source):
        harvest(source, copy_files=False)
        before = call_action("package_show", id="presupuesto-municipal")["resources"][1]
        changed = json.loads(json.dumps(SECTIONS["informacion-economica-financiera"]))
        changed["pageProps"]["presupuesto"][1]["url"] = PRESUPUESTO_VIEW.replace("usp=sharing", "usp=drive_link")

        harvest(source, copy_files=False, sections={"informacion-economica-financiera": changed})

        after = call_action("package_show", id="presupuesto-municipal")["resources"][1]
        assert after["id"] == before["id"]


class TestCopy:

    def test_files_are_copied_folders_are_not(self, source):
        portal, results = harvest(source)

        fetched = [u for u, _ in portal.file_calls()]
        # 5 Drive documents + 2 salary scales + 3 sworn statements; no folders
        assert len(fetched) == 10
        assert not any("folders" in u for u in fetched)
        assert PRESUPUESTO_DOWNLOAD in fetched and DDJJ in fetched
        budget = call_action("package_show", id="presupuesto-municipal")["resources"][1]
        assert budget["url_type"] == "upload"
        assert budget["url"].endswith("/download/documento-14-h.pdf")   # named by Drive, munged
        assert budget["format"] == "PDF"                                # from the file name
        assert budget["hash"] == SHA
        assert budget["source_url"] == PRESUPUESTO_DOWNLOAD
        ddjj = call_action("package_show", id="declaraciones-juradas-patrimoniales")["resources"][0]
        assert ddjj["url_type"] == "upload" and ddjj["source_etag"] == '"ddjj-1"'
        folder = call_action("package_show", id="boletin-oficial-municipal")["resources"][0]
        assert folder.get("url_type") != "upload"

    def test_drive_asking_to_confirm_is_not_the_file(self, source):
        portal = FakePortal(drive_html=True)
        harvest(source, portal=portal)

        budget = call_action("package_show", id="presupuesto-municipal")["resources"][1]
        assert budget.get("url_type") != "upload" and budget["url"] == PRESUPUESTO_VIEW
        ddjj = call_action("package_show", id="declaraciones-juradas-patrimoniales")["resources"][0]
        assert ddjj["url_type"] == "upload"      # the PDFs elsewhere are fine

    def test_copies_are_not_asked_again_until_recheck_days(self, source):
        harvest(source)
        budget = call_action("package_show", id="presupuesto-municipal")["resources"][1]

        portal, results = harvest(source)

        assert portal.file_calls() == []
        assert results["informacion-economica-financiera/presupuesto"] == "unchanged"
        assert call_action("resource_show", id=budget["id"])["url"] == budget["url"]

    def test_after_recheck_days_the_file_is_compared_again(self, source):
        harvest(source)
        dataset = call_action("package_show", id="presupuesto-municipal")
        old = (datetime.utcnow() - timedelta(days=40)).replace(microsecond=0).isoformat()
        for r in dataset["resources"]:
            call_action("resource_patch", id=r["id"], source_downloaded=old)

        portal, results = harvest(source)

        assert len(portal.file_calls()) == 2         # that dataset only
        after = call_action("package_show", id="presupuesto-municipal")["resources"][1]
        assert after["hash"] == SHA and after["source_downloaded"] > old

    def test_a_failed_copy_stays_a_link_and_is_retried(self, source):
        harvest(source, portal=FakePortal(file_status=500))
        budget = call_action("package_show", id="presupuesto-municipal")["resources"][1]
        assert budget.get("url_type") != "upload" and budget["url"] == PRESUPUESTO_VIEW

        portal, results = harvest(source)

        assert results["informacion-economica-financiera/presupuesto"] == "unchanged"
        assert len(portal.file_calls()) == 10
        assert call_action("resource_show", id=budget["id"])["url_type"] == "upload"


class TestPieces:

    def test_parse_section(self):
        datasets = riocuarto.parse_section("informacion-economica-financiera",
                                           SECTIONS["informacion-economica-financiera"]["pageProps"])
        assert [(d["guid"], len(d["items"])) for d in datasets] == [
            ("informacion-economica-financiera/presupuesto", 2),
            ("informacion-economica-financiera/recaudacion", 1),
            ("informacion-economica-financiera/deudas", 2)]
        everything = riocuarto.parse_section("declaraciones-juradas",
                                             SECTIONS["declaraciones-juradas"]["pageProps"])
        assert len(everything) == 1 and len(everything[0]["items"]) == 3
        assert riocuarto.parse_section("otra-cosa", {"items": [{"url": "x"}]}) == []
        assert riocuarto.parse_section("escala-salarial", {"items": [{"title": "sin url"}]}) == []

    def test_download_url(self):
        assert riocuarto.download_url(PRESUPUESTO_VIEW) == PRESUPUESTO_DOWNLOAD
        assert riocuarto.download_url("https://drive.google.com/drive/folders/1abc?usp=sharing") is None
        assert riocuarto.download_url("https://docs.google.com/spreadsheets/d/1abc/edit") is None
        assert riocuarto.download_url(DDJJ) == DDJJ
        assert riocuarto.download_url("https://www.riocuarto.gob.ar/areas") is None

    def test_item_key(self):
        assert riocuarto.item_key(PRESUPUESTO_VIEW) == \
            riocuarto.item_key(PRESUPUESTO_VIEW.replace("usp=sharing", "usp=drive_link"))
        assert riocuarto.item_key(DDJJ + "?x=1") == DDJJ

    def test_format(self):
        f = RioCuartoHarvester._format
        assert f(PRESUPUESTO_VIEW, PRESUPUESTO_DOWNLOAD) == ""
        assert f("https://drive.google.com/drive/folders/1abc", None) == "Google Drive"
        assert f(DDJJ, DDJJ) == "PDF"
        assert f("https://www.riocuarto.gob.ar/areas", None) == "HTML"
