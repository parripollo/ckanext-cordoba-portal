"""The ckan_with_files harvester: provenance, organizations, groups, extras."""
import copy
import io
import json
import os
from types import SimpleNamespace
from unittest import mock
from urllib.parse import parse_qs, urlparse

import pytest

from ckan import model
from ckan.plugins import toolkit
from ckan.tests import factories
from ckan.tests.helpers import call_action

from werkzeug.datastructures import FileStorage

from ckanext.cordoba_portal.harvester import CordobaCKANHarvester

# a 1x1 PNG
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63f8ffff3f0300050001019a6f2cb50000000049454e44ae426082")

GESTION = "https://datosgestionabierta.cba.gov.ar"
HERE = os.path.dirname(__file__)

with open(os.path.join(HERE, "data", "gestion-dataset.json")) as f:
    REMOTE = json.load(f)   # a real dataset of the portal, as package_show gives it

REMOTE_ORG = {
    "name": REMOTE["organization"]["name"], "title": REMOTE["organization"]["title"],
    "id": REMOTE["organization"]["id"],
    "description": "El ministerio.",
    "image_url": "logo.png",
    "image_display_url": GESTION + "/uploads/group/logo.png",
}


def remote_dataset(**changes):
    d = copy.deepcopy(REMOTE)
    d.update(changes)
    return d


def fake_harvest_object(guid, config, url=GESTION):
    source = SimpleNamespace(url=url, config=json.dumps(config), title="Gestion",
                             id="source-id")
    return SimpleNamespace(guid=guid, source=source,
                           job=SimpleNamespace(source=source, id="job-id"), id="obj-id")


def harvester_with(config):
    h = CordobaCKANHarvester()
    h._set_config(json.dumps(config))
    return h


def admin():
    return {"context": {"user": factories.Sysadmin()["name"]}}


@pytest.fixture(autouse=True)
def forget_the_cached_harvest_user():
    # the harvester is a singleton and caches the site user's name, which
    # clean_db recreates
    CordobaCKANHarvester()._user_name = None


@pytest.mark.usefixtures("with_plugins", "clean_db")
class TestConfig:

    def test_source_portal_is_required(self):
        h = CordobaCKANHarvester()
        with pytest.raises(ValueError, match="source_portal"):
            h.validate_config("")
        with pytest.raises(ValueError, match="source_portal"):
            h.validate_config(json.dumps({"user_agent": "x"}))
        with pytest.raises(ValueError, match="source_portal"):
            h.validate_config(json.dumps({"source_portal": "otro"}))
        # our own site is not a source
        with pytest.raises(ValueError, match="source_portal"):
            h.validate_config(json.dumps({"source_portal": "cbadatos"}))
        assert h.validate_config(json.dumps({"source_portal": "datosgestionabierta"}))

    def test_single_org_must_exist(self):
        h = CordobaCKANHarvester()
        with pytest.raises(ValueError, match="does not exist"):
            h.validate_config(json.dumps(
                {"source_portal": "datosestadistica", "single_org": "nadie"}))
        factories.Organization(name="estadistica-dgeyc", source_portal="datosestadistica")
        assert h.validate_config(json.dumps(
            {"source_portal": "datosestadistica", "single_org": "estadistica-dgeyc"}))
        with pytest.raises(ValueError, match="single_org"):
            h.validate_config(json.dumps(
                {"source_portal": "datosestadistica", "remote_orgs_as_groups": True}))


@pytest.mark.usefixtures("with_plugins", "clean_db")
class TestModifyPackageDict:

    def test_provenance_and_prefixed_organization(self):
        h = harvester_with({"source_portal": "datosgestionabierta"})
        obj = fake_harvest_object(REMOTE["id"], {"source_portal": "datosgestionabierta"})

        logo = FileStorage(io.BytesIO(PNG), filename="logo.png", content_type="image/png")
        with mock.patch.object(CordobaCKANHarvester, "_get_organization",
                               return_value=REMOTE_ORG), \
                mock.patch.object(CordobaCKANHarvester, "_fetch_file", return_value=logo) as fetch:
            result = h.modify_package_dict(remote_dataset(), obj)
        fetch.assert_called_once_with(GESTION + "/uploads/group/logo.png")

        assert result["source_portal"] == "datosgestionabierta"
        assert result["source_url"] == GESTION + "/dataset/" + REMOTE["id"]

        org = call_action("organization_show", id=result["owner_org"])
        assert org["name"] == "gestion-" + REMOTE["organization"]["name"]
        assert org["title"] == REMOTE["organization"]["title"]
        assert org["description"] == REMOTE["organization"]["description"]
        # the logo is a copy of ours, not a link to the portal
        assert org["image_url"].endswith("logo.png") and "://" not in org["image_url"]
        assert org["image_display_url"].startswith("http://test.ckan.net/")
        assert "datosgestionabierta" not in org["image_display_url"]
        assert org["source_portal"] == "datosgestionabierta"
        assert org["source_url"] == GESTION + "/organization/" + REMOTE["organization"]["name"]

        # the organization is created once
        with mock.patch.object(CordobaCKANHarvester, "_get_organization") as remote:
            again = h.modify_package_dict(remote_dataset(), obj)
        assert again["owner_org"] == result["owner_org"]
        remote.assert_not_called()
        assert len(call_action("organization_list")) == 1

    def test_a_logo_ckan_rejects_is_linked_instead(self):
        h = harvester_with({"source_portal": "datosgestionabierta"})
        obj = fake_harvest_object(REMOTE["id"], {"source_portal": "datosgestionabierta"})
        svg = FileStorage(io.BytesIO(b"<svg xmlns='http://www.w3.org/2000/svg'/>"),
                          filename="logo.svg", content_type="image/svg+xml")
        remote_org = dict(REMOTE_ORG, image_display_url=GESTION + "/uploads/group/logo.svg")

        with mock.patch.object(CordobaCKANHarvester, "_get_organization",
                               return_value=remote_org), \
                mock.patch.object(CordobaCKANHarvester, "_fetch_file", return_value=svg):
            result = h.modify_package_dict(remote_dataset(), obj)

        org = call_action("organization_show", id=result["owner_org"])
        assert org["image_url"] == GESTION + "/uploads/group/logo.svg"

    def test_resources_keep_their_original(self):
        h = harvester_with({"source_portal": "datosgestionabierta"})
        obj = fake_harvest_object(REMOTE["id"], {"source_portal": "datosgestionabierta"})
        dataset = remote_dataset()
        dataset["resources"].append({"id": "r-link", "name": "Instagram", "format": "ENLACE",
                                     "url": "https://www.instagram.com/gobdecordoba/"})

        with mock.patch.object(CordobaCKANHarvester, "_get_organization",
                               return_value=REMOTE_ORG), \
                mock.patch.object(CordobaCKANHarvester, "_fetch_file", return_value=None):
            result = h.modify_package_dict(dataset, obj)

        hosted = [r for r in result["resources"] if r["id"] != "r-link"]
        assert hosted and all(r["source_url"] == r["url"] for r in hosted)
        assert all(r["source_last_modified"] == r["last_modified"] for r in hosted)
        link = [r for r in result["resources"] if r["id"] == "r-link"][0]
        assert "source_url" not in link

    def test_extras_that_clash_with_our_fields_are_dropped(self):
        h = harvester_with({"source_portal": "datosgestionabierta"})
        obj = fake_harvest_object(REMOTE["id"], {"source_portal": "datosgestionabierta"})
        dataset = remote_dataset()
        dataset["extras"] += [{"key": "title", "value": "x"},
                              {"key": "source_portal", "value": "x"},
                              {"key": "metadata_modified", "value": "2026-01-01"}]

        with mock.patch.object(CordobaCKANHarvester, "_get_organization",
                               return_value=REMOTE_ORG), \
                mock.patch.object(CordobaCKANHarvester, "_fetch_file", return_value=None):
            result = h.modify_package_dict(dataset, obj)

        assert [e["key"] for e in result["extras"]] == ["Frecuencia de actualización"]

    def test_single_org_and_remote_orgs_as_groups(self):
        org = factories.Organization(name="estadistica-dgeyc", source_portal="datosestadistica")
        config = {"source_portal": "datosestadistica", "single_org": "estadistica-dgeyc",
                  "remote_orgs_as_groups": True}
        h = harvester_with(config)
        obj = fake_harvest_object("abc", config, url="https://datosestadistica.cba.gov.ar/")
        dataset = remote_dataset(organization={"name": "territorio", "title": "Territorio",
                                               "id": "t-1"}, groups=[])

        result = h.modify_package_dict(dataset, obj)

        assert result["owner_org"] == "estadistica-dgeyc"
        assert result["source_url"] == "https://datosestadistica.cba.gov.ar/dataset/abc"
        group = call_action("group_show", id="territorio")
        assert group["title"] == "Territorio"
        assert result["groups"] == [{"id": group["id"], "name": "territorio"}]
        assert call_action("organization_list") == [org["name"]]


# --- a full harvest through the database queue against a fake portal ------

REMOTE_DATASETS = {
    REMOTE["name"]: remote_dataset(),
    "segundo": remote_dataset(id="99999999-9999-9999-9999-999999999999", name="segundo",
                              title="Segundo dataset", resources=[], groups=[],
                              extras=[{"key": "notes", "value": "clash"}]),
}


def remote_ckan(url):
    """What the portal answers to the harvester's API calls."""
    query = parse_qs(urlparse(url).query)
    if "/package_search" in url:
        start = int(query.get("start", ["0"])[0])
        results = list(REMOTE_DATASETS.values()) if start == 0 else []
        return json.dumps({"success": True, "result": {
            "count": len(REMOTE_DATASETS), "results": results}})
    if "/package_show" in url:
        return json.dumps({"success": True, "result": REMOTE_DATASETS[query["id"][0]]})
    if "/organization_show" in url:
        return json.dumps({"success": True, "result": REMOTE_ORG})
    if "/group_show" in url:
        name = query["id"][0]
        group = [g for g in REMOTE["groups"] if name in (g["name"], g["id"])][0]
        return json.dumps({"success": True, "result": {
            "name": group["name"], "title": group["title"], "id": group["id"]}})
    raise AssertionError("unexpected remote call: %s" % url)


@pytest.mark.usefixtures("with_plugins", "clean_db")
class TestFullHarvest:

    def test_full_cycle(self):
        from ckanext.harvest import queue
        from ckanext.harvest.model import HarvestQueueMessage

        source = call_action("harvest_source_create", name="gestion", url=GESTION,
                             source_type="ckan_with_files", frequency="WEEKLY",
                             config=json.dumps({"source_portal": "datosgestionabierta",
                                                "remote_groups": "create"}),
                             **admin())
        call_action("harvest_job_create", source_id=source["id"], run=True, **admin())
        assert model.Session.query(HarvestQueueMessage).count() == 1

        gather = queue.get_gather_consumer()
        fetch = queue.get_fetch_consumer()
        with mock.patch("ckanext.harvest.harvesters.ckanharvester.CKANHarvester._get_content",
                        side_effect=lambda self, url: remote_ckan(url), autospec=True), \
                mock.patch.object(CordobaCKANHarvester, "_fetch_file", return_value=None):
            queue.gather_callback(gather, *gather.basic_get("gather"))
            for _ in REMOTE_DATASETS:
                queue.fetch_callback(fetch, *fetch.basic_get("fetch"))
        assert model.Session.query(HarvestQueueMessage).count() == 0
        from ckanext.harvest.model import HarvestObjectError
        errors = [e.message for e in model.Session.query(HarvestObjectError).all()]
        assert errors == [], errors

        first = call_action("package_show", id=REMOTE["name"])
        assert first["source_portal"] == "datosgestionabierta"
        assert first["source_url"] == GESTION + "/dataset/" + REMOTE["id"]
        assert first["organization"]["name"] == "gestion-" + REMOTE["organization"]["name"]
        assert first["organization"]["title"] == REMOTE["organization"]["title"]
        assert [g["name"] for g in first["groups"]] == [g["name"] for g in REMOTE["groups"]]
        assert sorted(t["name"] for t in first["tags"]) == sorted(t["name"] for t in REMOTE["tags"])
        assert [e["key"] for e in first["extras"] if not e["key"].startswith("harvest_")] == \
            ["Frecuencia de actualización"]
        assert all(r["source_url"] == r["url"] for r in first["resources"])
        assert first["license_id"] == REMOTE["license_id"]

        # the extra named like a field was dropped instead of failing the dataset
        second = call_action("package_show", id="segundo")
        assert second["source_portal"] == "datosgestionabierta"
        assert [e["key"] for e in second["extras"] if not e["key"].startswith("harvest_")] == []

        call_action("harvest_jobs_run", source_id=source["id"], **admin())
        status = call_action("harvest_source_show", id=source["id"])["status"]
        assert status["last_job"]["stats"] == {
            "added": 2, "updated": 0, "not modified": 0, "errored": 0, "deleted": 0}

        # the facet and the pages see them
        found = call_action("package_search", fq="source_portal:datosgestionabierta")
        assert found["count"] == 2
        assert toolkit.h.cordoba_portal_counts() == {"datasets": 2, "organizations": 1}
