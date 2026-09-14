"""The villaallende harvester: the WordPress post types and their files."""
import hashlib
import json
import os
from datetime import datetime
from unittest import mock
from urllib.parse import urlparse

import pytest
import requests

from ckan.tests import factories
from ckan.tests.helpers import call_action
from ckanext.harvest.model import HarvestObject
from ckanext.harvest.tests.factories import HarvestSourceObj, HarvestJobObj

from ckanext.cordoba_portal import villaallende
from ckanext.cordoba_portal.villaallende import VillaAllendeHarvester

SITE = "https://www.villaallende.gov.ar"
HERE = os.path.dirname(__file__)


def fixture(name):
    with open(os.path.join(HERE, "data", "villaallende-" + name + ".json"), encoding="utf-8") as f:
        return json.load(f)   # real answers of the site's REST API, trimmed


POSTS = {t: fixture(t) for t in ("va_boletin", "va_licitacion", "compra-publica")}
MEDIA = {m["id"]: m for m in fixture("media")}
FILE = b"%PDF-1.7 fake\n"
SHA = hashlib.sha256(FILE).hexdigest()
CONFIG = {"pause": 0, "copy_pause": 0, "user_agent": "Mozilla/5.0 test cbadatos.com.ar"}
BOLETIN_607 = SITE + "/wp-content/uploads/2026/09/BOLETIN-607-08-09-26-A-11-09-26.pdf"


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


class FakeSite:
    """requests.get of the site: the REST API from the fixtures, the uploads."""

    def __init__(self, file_status=200, file_content=FILE):
        self.calls = []
        self.file_status = file_status
        self.file_content = file_content

    def __call__(self, url, params=None, headers=None, **kwargs):
        self.calls.append((url, params or {}, headers or {}, kwargs.get("stream", False)))
        path = urlparse(url).path
        if path.startswith("/wp-json/wp/v2/"):
            kind = path.rsplit("/", 1)[-1]
            if kind == "media":
                wanted = [int(i) for i in params["include"].split(",")]
                return FakeResponse([MEDIA[i] for i in wanted if i in MEDIA],
                                    headers={"X-WP-TotalPages": "1"})
            if kind in POSTS:
                return FakeResponse(POSTS[kind], headers={"X-WP-TotalPages": "1"})
            return FakeResponse(status_code=404)
        if "/wp-content/uploads/" in path:
            return FakeResponse(status_code=self.file_status, content=self.file_content, headers={
                "Content-Type": "application/pdf", "Content-Length": str(len(self.file_content)),
                "ETag": '"va-1"', "Last-Modified": "Sun, 14 Sep 2026 00:55:08 GMT"})
        return FakeResponse(status_code=404)

    def api_calls(self):
        return [(urlparse(u).path.rsplit("/", 1)[-1], p) for u, p, _, s in self.calls if not s]

    def file_calls(self):
        return [(u, h) for u, _, h, stream in self.calls if stream]


@pytest.fixture(autouse=True)
def forget_the_cached_harvest_user():
    VillaAllendeHarvester()._user_name = None


@pytest.fixture
def source(with_plugins, clean_db):
    org = factories.Organization(name="villaallende", title="Municipalidad de Villa Allende",
                                 source_portal="villaallende")
    return HarvestSourceObj(url=SITE, source_type="villaallende", owner_org=org["id"],
                            config=json.dumps(CONFIG), title="Villa Allende")


def harvest(source, site=None, copy_files=True, posts=None):
    """One whole run: gather, fetch, import. Returns (site, results)."""
    site = site or FakeSite()
    job = HarvestJobObj(source=source)
    harvester = VillaAllendeHarvester()
    results = {}
    with mock.patch("ckanext.cordoba_portal.villaallende.requests.get", site), \
            mock.patch("ckanext.cordoba_portal.harvester.requests.get", site), \
            mock.patch.dict(POSTS, posts or {}):
        source.config = json.dumps(dict(CONFIG, copy_files=copy_files))
        ids = harvester.gather_stage(job)
        for object_id in ids or []:
            obj = HarvestObject.get(object_id)
            assert harvester.fetch_stage(obj)
            results[obj.guid] = harvester.import_stage(obj)
    job.status = "Finished"
    job.finished = datetime.utcnow()
    job.save()
    return site, results


@pytest.mark.usefixtures("with_plugins", "clean_db")
class TestConfig:

    def test_empty_is_fine(self):
        assert VillaAllendeHarvester().validate_config("") == ""

    def test_numbers_and_org_are_checked(self):
        with pytest.raises(ValueError):
            VillaAllendeHarvester().validate_config(json.dumps({"pause": "slow"}))
        with pytest.raises(ValueError, match="single_org"):
            VillaAllendeHarvester().validate_config(json.dumps({"single_org": "nope"}))

    def test_info(self):
        assert VillaAllendeHarvester().info()["name"] == "villaallende"


class TestGather:

    def test_one_object_per_post_type(self, source):
        job = HarvestJobObj(source=source)
        site = FakeSite()
        with mock.patch("ckanext.cordoba_portal.villaallende.requests.get", site):
            ids = VillaAllendeHarvester().gather_stage(job)

        assert [HarvestObject.get(i).guid for i in ids] == ["va_boletin", "va_licitacion", "compra-publica"]
        content = json.loads(HarvestObject.get(ids[0]).content)
        assert content["title"] == "Boletín Oficial municipal"
        assert content["url"] == SITE + "/transparencia/"
        assert content["resources"][0] == {
            "key": "boletin:7813", "name": "Boletín N° 607 (08/09 al 11/09/2026)",
            "description": "Boletín Oficial del 08/09 al 11/09/2026.",
            "url": BOLETIN_607, "modified": "2026-09-14T00:55:08", "date": "2026-09-08"}
        # the posts of each type, then their media in one request; the
        # purchases point at files directly (no media)
        assert [k for k, _ in site.api_calls()] == \
            ["va_boletin", "media", "va_licitacion", "media", "compra-publica"]
        assert site.api_calls()[1][1]["include"] == "7769,7814"
        assert all(h["User-Agent"] == CONFIG["user_agent"] for _, _, h, _ in site.calls)

    def test_a_post_type_that_fails_is_an_error_the_rest_goes_on(self, source):
        job = HarvestJobObj(source=source)
        with mock.patch.dict(POSTS), \
                mock.patch("ckanext.cordoba_portal.villaallende.requests.get", FakeSite()):
            del POSTS["va_licitacion"]
            ids = VillaAllendeHarvester().gather_stage(job)
        assert len(ids) == 2

    def test_site_down_is_a_gather_error(self, source):
        job = HarvestJobObj(source=source)
        with mock.patch("ckanext.cordoba_portal.villaallende.requests.get",
                        return_value=FakeResponse(status_code=500)):
            assert VillaAllendeHarvester().gather_stage(job) is None

    def test_paginated_listings_are_read_whole(self, source):
        job = HarvestJobObj(source=source)
        pages = {1: POSTS["va_boletin"][:1], 2: POSTS["va_boletin"][1:]}

        def site(url, params=None, headers=None, **kwargs):
            if url.endswith("/va_boletin"):
                return FakeResponse(pages[params["page"]], headers={"X-WP-TotalPages": "2"})
            return FakeSite()(url, params, headers, **kwargs)

        with mock.patch("ckanext.cordoba_portal.villaallende.requests.get", site):
            ids = VillaAllendeHarvester().gather_stage(job)
        content = json.loads(HarvestObject.get(ids[0]).content)
        assert len(content["resources"]) == 2


class TestImport:

    def test_the_datasets(self, source):
        harvest(source, copy_files=False)

        dataset = call_action("package_show", id="boletin-oficial-municipal")
        assert dataset["title"] == "Boletín Oficial municipal"
        assert dataset["source_portal"] == "villaallende"
        assert dataset["source_url"] == SITE + "/transparencia/"
        assert dataset["organization"]["name"] == "villaallende"
        extras = {e["key"]: e["value"] for e in dataset["extras"] if not e["key"].startswith("harvest_")}
        assert extras == {"documentos": "2", "ultimo_documento": "2026-09-08"}
        assert dataset["groups"] == []
        assert sorted(d["name"] for d in call_action("package_search", q="")["results"]) == \
            ["boletin-oficial-municipal", "compras-publicas", "licitaciones-y-compulsas"]

    def test_the_resources(self, source):
        harvest(source, copy_files=False)

        bulletins = call_action("package_show", id="boletin-oficial-municipal")["resources"]
        assert [r["name"] for r in bulletins] == \
            ["Boletín N° 607 (08/09 al 11/09/2026)", "Boletín N° 606 (01/09 al 08/09/2026)"]
        assert bulletins[0]["url"] == BOLETIN_607 and bulletins[0]["source_url"] == BOLETIN_607
        assert bulletins[0]["format"] == "PDF"
        assert bulletins[0]["source_last_modified"] == "2026-09-14T00:55:08"

        tenders = call_action("package_show", id="licitaciones-y-compulsas")["resources"]
        assert [r["name"] for r in tenders] == [
            "Bacheo superficial de carpeta de rodamiento con mezcla asfáltica en caliente - Compulsa Abreviada",
            "Bacheo superficial de carpeta de rodamiento con mezcla asfáltica en caliente - Nota Aclaratoria",
            "La Obra Pavimento de Calle Pablo cabrera de la Ciudad de Villa Allende - Licitación Pública",
        ]
        assert tenders[0]["description"] == ("Expediente 11893/2026. Compulsa abreviada. Estado: "
                                             "llamado vigente. Monto: $75.000.000. "
                                             "Fecha de apertura: 2026-08-27.")
        assert tenders[0]["url"].endswith("/CompulsaAbreviadaBacheoSuperficialPuntosVillaAllende.pdf")
        assert tenders[2]["description"].startswith("Expediente EXPTE-11847/2026. Licitación pública. "
                                                    "Estado: en proceso.")

        purchases = call_action("package_show", id="compras-publicas")["resources"]
        assert purchases[0]["name"] == "Compulsa abreviada: Obra de Bacheo"
        assert purchases[0]["description"].startswith("La Municipalidad llama a Compulsa Abreviada")
        assert purchases[0]["url"].endswith("-de-la-Ciudad.pdf") and purchases[0]["format"] == "PDF"
        assert purchases[0]["source_last_modified"] == "2026-05-20T14:03:05"

    def test_a_purchase_that_points_at_a_page_is_a_link(self, source):
        posts = json.loads(json.dumps(POSTS["compra-publica"]))
        posts[1]["acf"]["url_licitaciones"] = SITE + "/compras-y-licitaciones/"

        harvest(source, posts={"compra-publica": posts})

        page = call_action("package_show", id="compras-publicas")["resources"][1]
        assert page["format"] == "HTML" and page["url"] == SITE + "/compras-y-licitaciones/"
        assert not page.get("source_url") and page.get("url_type") != "upload"

    def test_second_run_is_unchanged_and_stable(self, source):
        harvest(source, copy_files=False)
        before = call_action("package_show", id="boletin-oficial-municipal")

        site, results = harvest(source, copy_files=False)

        assert set(results.values()) == {"unchanged"}
        after = call_action("package_show", id="boletin-oficial-municipal")
        assert after["id"] == before["id"]
        assert [r["id"] for r in after["resources"]] == [r["id"] for r in before["resources"]]
        assert after["metadata_modified"] == before["metadata_modified"]

    def test_a_new_bulletin_updates_the_dataset(self, source):
        harvest(source, copy_files=False)
        newer = json.loads(json.dumps(POSTS["va_boletin"]))
        newer.insert(0, dict(newer[0], id=9999, slug="boletin-n-608", acf={
            "va_boletin_numero": "608", "va_boletin_periodo": "11/09 al 18/09/2026",
            "va_boletin_pdf": 7814, "va_boletin_fecha": "20260918"}))

        site, results = harvest(source, copy_files=False, posts={"va_boletin": newer})

        assert results["va_boletin"] is True and results["va_licitacion"] == "unchanged"
        dataset = call_action("package_show", id="boletin-oficial-municipal")
        assert dataset["resources"][0]["name"] == "Boletín N° 608 (11/09 al 18/09/2026)"
        assert {e["key"]: e["value"] for e in dataset["extras"]}["ultimo_documento"] == "2026-09-18"


class TestCopy:

    def test_files_are_copied(self, source):
        site, results = harvest(source)

        fetched = [u for u, _ in site.file_calls()]
        assert len(fetched) == 2 + 3 + 2
        assert BOLETIN_607 in fetched
        assert all(h["User-Agent"] == CONFIG["user_agent"] for _, h in site.file_calls())
        bulletin = call_action("package_show", id="boletin-oficial-municipal")["resources"][0]
        assert bulletin["url_type"] == "upload"
        assert bulletin["url"].endswith("/download/boletin-607-08-09-26-a-11-09-26.pdf")
        assert bulletin["hash"] == SHA and bulletin["source_etag"] == '"va-1"'
        assert bulletin["source_url"] == BOLETIN_607

    def test_copies_survive_the_next_run_without_downloads(self, source):
        harvest(source)
        bulletin = call_action("package_show", id="boletin-oficial-municipal")["resources"][0]

        site, results = harvest(source)

        assert site.file_calls() == []
        assert results["va_boletin"] == "unchanged"
        assert call_action("resource_show", id=bulletin["id"])["url"] == bulletin["url"]

    def test_a_newer_file_on_the_site_is_fetched_again(self, source):
        harvest(source)
        newer = {m: dict(v) for m, v in MEDIA.items()}
        newer[7814]["modified"] = "2099-01-01T00:00:00"

        site = FakeSite(file_content=b"%PDF new\n")
        with mock.patch.dict(MEDIA, newer):
            harvest(source, site=site)

        assert [u for u, _ in site.file_calls()] == [BOLETIN_607]
        bulletin = call_action("package_show", id="boletin-oficial-municipal")["resources"][0]
        assert bulletin["hash"] == hashlib.sha256(b"%PDF new\n").hexdigest()
        assert bulletin["source_last_modified"] == "2099-01-01T00:00:00"

    def test_a_failed_copy_stays_a_link_and_is_retried(self, source):
        harvest(source, site=FakeSite(file_status=500))
        bulletin = call_action("package_show", id="boletin-oficial-municipal")["resources"][0]
        assert bulletin.get("url_type") != "upload" and bulletin["url"] == BOLETIN_607

        site, results = harvest(source)

        assert results["va_boletin"] == "unchanged"
        assert len(site.file_calls()) == 7
        assert call_action("resource_show", id=bulletin["id"])["url_type"] == "upload"


class TestPieces:

    def test_iso_date(self):
        assert villaallende.iso_date("20260908") == "2026-09-08"
        assert villaallende.iso_date("2026-05-20 00:00:00") == "2026-05-20"
        assert villaallende.iso_date("") == "" and villaallende.iso_date(None) == ""
        assert villaallende.iso_date("mañana") == ""

    def test_media_ids(self):
        assert villaallende.media_ids(POSTS["va_boletin"] + POSTS["va_licitacion"]) == \
            {7814, 7769, 7647, 7666, 7639}
        assert villaallende.media_ids(POSTS["compra-publica"]) == set()

    def test_tender_without_a_known_file_is_skipped(self):
        post = json.loads(json.dumps(POSTS["va_licitacion"][0]))
        post["acf"]["va_lic_documentos"] = [{"label": "Pliego", "url": 1}, {"label": "Anexo", "url": "https://x/a.pdf"}]
        resources = villaallende.tender_resources([post], {})
        assert [r["url"] for r in resources] == ["https://x/a.pdf"]
        assert resources[0]["name"].endswith(" - Anexo")

    def test_bulletin_without_a_file_is_skipped(self):
        post = json.loads(json.dumps(POSTS["va_boletin"][0]))
        post["acf"]["va_boletin_pdf"] = None
        assert villaallende.bulletin_resources([post], MEDIA) == []
