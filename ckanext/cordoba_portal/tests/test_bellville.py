"""The bellville harvester: the Legislature's one pointed at the budget page."""
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

from ckanext.cordoba_portal.bellville import BellVilleHarvester

SITE = "https://bellville.gob.ar"
HERE = os.path.dirname(__file__)
with open(os.path.join(HERE, "data", "bellville-presupuesto.json"), encoding="utf-8") as f:
    PAGE = json.load(f)   # the real page, through the REST API, trimmed
FILE = b"%PDF-1.7 fake\n"
CONFIG = {"pause": 0, "copy_pause": 0, "user_agent": "Mozilla/5.0 test cbadatos.com.ar"}


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
    def __init__(self):
        self.calls = []

    def __call__(self, url, params=None, headers=None, **kwargs):
        self.calls.append((url, params or {}, kwargs.get("stream", False)))
        path = urlparse(url).path
        if path == "/wp-json/wp/v2/pages":
            return FakeResponse(PAGE if params["slug"] == "presupuesto" else [])
        if "/wp-content/uploads/" in path:
            return FakeResponse(content=FILE, headers={
                "Content-Type": "application/pdf", "Content-Length": str(len(FILE))})
        return FakeResponse(status_code=404)

    def file_calls(self):
        return [u for u, _, stream in self.calls if stream]


@pytest.fixture(autouse=True)
def forget_the_cached_harvest_user():
    BellVilleHarvester()._user_name = None


@pytest.fixture
def source(with_plugins, clean_db):
    org = factories.Organization(name="bellville", title="Municipalidad de Bell Ville",
                                 source_portal="bellville")
    return HarvestSourceObj(url=SITE, source_type="bellville", owner_org=org["id"],
                            config=json.dumps(CONFIG), title="Bell Ville")


def harvest(source, copy_files=True):
    site = FakeSite()
    job = HarvestJobObj(source=source)
    harvester = BellVilleHarvester()
    results = {}
    with mock.patch("ckanext.cordoba_portal.legislatura.requests.get", site), \
            mock.patch("ckanext.cordoba_portal.harvester.requests.get", site):
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


class TestBellVille:

    def test_info_and_pages(self):
        assert BellVilleHarvester().info()["name"] == "bellville"
        assert BellVilleHarvester.default_pages == ["presupuesto"]

    def test_the_budget_page_is_three_datasets(self, source):
        site, results = harvest(source, copy_files=False)

        assert sorted(results) == ["ordenanzas", "presupuesto", "regimen-de-contratacion",
                                   "regimen-tarifario-e-impositivo"]
        assert [p["slug"] for _, p, _ in site.calls] == ["presupuesto"]    # the one page
        budget = call_action("package_show", id="presupuesto")
        assert budget["title"] == "Presupuesto"
        assert budget["source_portal"] == "bellville"
        assert budget["source_url"] == SITE + "/presupuesto/"
        assert budget["organization"]["name"] == "bellville"
        assert [g["name"] for g in budget["groups"]] == ["bellville-presupuesto"]
        names = [r["name"] for r in budget["resources"]]
        assert names[:3] == ["Presupuesto - Ordenanza Presupuesto",
                             "Presupuesto - Presupuesto Vigente y Anexos",
                             "Presupuesto - Ejecutado 2024"]
        assert budget["resources"][0]["format"] == "PDF"
        # the site links its files by path
        assert budget["resources"][0]["source_url"] == \
            SITE + "/wp-content/uploads/publicaciones/ordenanza_2782_presupuesto.pdf"
        tariff = call_action("package_show", id="regimen-tarifario-e-impositivo")
        assert len(tariff["resources"]) == 7
        # the heading with only a link to the council's digest, elsewhere
        ordinances = call_action("package_show", id="ordenanzas")
        assert [(r["format"], r["url"]) for r in ordinances["resources"]] == \
            [("HTML", "https://concejobellville.gob.ar/digesto/")]

    def test_files_are_copied(self, source):
        site, results = harvest(source)

        assert len(site.file_calls()) == 6 + 7 + 1
        budget = call_action("package_show", id="presupuesto")["resources"][0]
        assert budget["url_type"] == "upload" and budget["url"].endswith("/download/ordenanza_2782_presupuesto.pdf")

        site, results = harvest(source)
        assert site.file_calls() == [] and set(results.values()) == {"unchanged"}
