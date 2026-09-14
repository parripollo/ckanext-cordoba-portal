"""The harvester copies the files of the portal of origin, once, and again
only when they change."""
import hashlib
import json
from unittest import mock

import pytest

from ckan.tests import factories
from ckan.tests.helpers import call_action

from ckanext.cordoba_portal.harvester import CordobaCKANHarvester

GESTION = "https://datosgestionabierta.cba.gov.ar"
FILE_URL = GESTION + "/dataset/d1/resource/r1/download/datos.csv"
CONTENT = b"a,b\n1,2\n"
SHA = hashlib.sha256(CONTENT).hexdigest()
CONFIG = {"source_portal": "datosgestionabierta", "copy_pause": 0,
          "user_agent": "Mozilla/5.0 test cbadatos.com.ar"}


class FakeResponse:
    def __init__(self, status_code=200, content=CONTENT, headers=None):
        self.status_code = status_code
        self._content = content
        self.headers = headers or {"ETag": '"abc"', "Content-Type": "text/csv",
                                   "Content-Length": str(len(content))}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, chunk_size):
        for i in range(0, len(self._content), chunk_size):
            yield self._content[i:i + chunk_size]


def harvester():
    h = CordobaCKANHarvester()
    h._user_name = None
    h._set_config(json.dumps(CONFIG))
    return h


@pytest.fixture
def linked(with_plugins, clean_db):
    """A harvested dataset whose file is still a link to the portal."""
    org = factories.Organization(name="gestion-o-salud", source_portal="datosgestionabierta")
    dataset = factories.Dataset(owner_org=org["id"], source_portal="datosgestionabierta",
                                source_url=GESTION + "/dataset/d1")
    resource = factories.Resource(package_id=dataset["id"], name="datos.csv", format="CSV",
                                  url=FILE_URL, source_url=FILE_URL,
                                  source_last_modified="2026-09-01T10:00:00")
    return dataset, resource


def copy(dataset):
    with mock.patch("ckanext.cordoba_portal.harvester.requests.get") as get:
        get.return_value = FakeResponse()
        harvester()._copy_files(dataset["id"])
    return get


class TestCopy:

    def test_a_linked_file_is_copied(self, linked):
        dataset, resource = linked

        get = copy(dataset)

        get.assert_called_once()
        assert get.call_args[0][0] == FILE_URL
        assert get.call_args[1]["headers"] == {"User-Agent": CONFIG["user_agent"]}
        copied = call_action("resource_show", id=resource["id"])
        assert copied["url_type"] == "upload"
        assert copied["url"].endswith("/download/datos.csv")
        assert "datosgestionabierta" not in copied["url"]
        assert copied["source_url"] == FILE_URL
        assert copied["hash"] == SHA
        assert copied["size"] == len(CONTENT)
        assert copied["source_etag"] == '"abc"'
        assert copied["source_downloaded"] > "2026-09-01T10:00:00"
        assert copied["format"] == "CSV"

    def test_unchanged_on_the_portal_no_request(self, linked):
        dataset, resource = linked
        copy(dataset)

        # the remote last_modified is older than our copy
        with mock.patch("ckanext.cordoba_portal.harvester.requests.get") as get:
            harvester()._copy_files(dataset["id"])
        get.assert_not_called()

    def test_changed_on_the_portal_but_304(self, linked):
        dataset, resource = linked
        copy(dataset)
        call_action("resource_patch", id=resource["id"], source_last_modified="2026-09-03T00:00:00",
                    source_downloaded="2026-09-02T00:00:00")
        before = call_action("resource_show", id=resource["id"])

        with mock.patch("ckanext.cordoba_portal.harvester.requests.get") as get:
            get.return_value = FakeResponse(status_code=304, content=b"")
            harvester()._copy_files(dataset["id"])

        assert get.call_args[1]["headers"]["If-None-Match"] == '"abc"'
        after = call_action("resource_show", id=resource["id"])
        assert after["url"] == before["url"] and after["hash"] == SHA
        assert after["source_downloaded"] > before["source_downloaded"]

    def test_changed_on_the_portal_same_content(self, linked):
        dataset, resource = linked
        copy(dataset)
        before = call_action("resource_show", id=resource["id"])
        call_action("resource_patch", id=resource["id"], source_last_modified="2099-01-01T00:00:00")

        with mock.patch("ckanext.cordoba_portal.harvester.requests.get") as get:
            get.return_value = FakeResponse(headers={"ETag": '"new"', "Content-Type": "text/csv"})
            with mock.patch.object(CordobaCKANHarvester, "_patch", wraps=harvester()._patch) as patch:
                harvester()._copy_files(dataset["id"])

        # same SHA-256: the file is not replaced, only the bookkeeping
        assert "upload" not in patch.call_args[0][1]
        after = call_action("resource_show", id=resource["id"])
        assert after["url"] == before["url"] and after["source_etag"] == '"new"'

    def test_changed_content_is_replaced(self, linked):
        dataset, resource = linked
        copy(dataset)
        call_action("resource_patch", id=resource["id"], source_last_modified="2099-01-01T00:00:00")
        new = b"a,b\n3,4\n"

        with mock.patch("ckanext.cordoba_portal.harvester.requests.get") as get:
            get.return_value = FakeResponse(content=new, headers={"ETag": '"v2"', "Content-Type": "text/csv"})
            harvester()._copy_files(dataset["id"])

        after = call_action("resource_show", id=resource["id"])
        assert after["hash"] == hashlib.sha256(new).hexdigest()
        assert after["size"] == len(new)
        assert after["source_etag"] == '"v2"'

    def test_too_big_stays_a_link(self, linked):
        dataset, resource = linked
        h = harvester()
        h.config["copy_max_mb"] = 0.000001   # about one byte

        with mock.patch("ckanext.cordoba_portal.harvester.requests.get") as get:
            get.return_value = FakeResponse()
            h._copy_files(dataset["id"])

        same = call_action("resource_show", id=resource["id"])
        assert same["url"] == FILE_URL and not same.get("url_type")

    @pytest.mark.ckan_config("ckan.max_resource_size", 1)
    def test_bigger_than_this_ckan_accepts_stays_a_link(self, linked):
        dataset, resource = linked
        big = FakeResponse(content=b"x" * (1024 * 1024 + 1))

        with mock.patch("ckanext.cordoba_portal.harvester.requests.get") as get:
            get.return_value = big
            harvester()._copy_files(dataset["id"])   # copy_max_mb is 200 here

        same = call_action("resource_show", id=resource["id"])
        assert same["url"] == FILE_URL and not same.get("url_type")

    def test_a_link_elsewhere_is_left_alone(self, with_plugins, clean_db):
        dataset = factories.Dataset(source_portal="datosgestionabierta")
        factories.Resource(package_id=dataset["id"], url="https://www.instagram.com/x/", format="ENLACE")

        get = copy(dataset)

        get.assert_not_called()


@pytest.mark.usefixtures("with_plugins", "clean_db")
class TestUpdatesKeepTheCopy:

    def test_modify_package_dict_keeps_our_copy(self, linked):
        dataset, resource = linked
        copy(dataset)
        ours = call_action("resource_show", id=resource["id"])

        # the portal sends the dataset again, with its own URL for the file
        remote = {"id": dataset["id"], "name": dataset["name"], "title": "Nuevo título",
                  "organization": {"name": "o-salud", "title": "Ministerio de Salud", "id": "x"},
                  "resources": [{"id": resource["id"], "url": FILE_URL, "name": "datos.csv",
                                 "format": "CSV", "last_modified": "2099-01-01T00:00:00"}]}
        from types import SimpleNamespace
        source = SimpleNamespace(url=GESTION, config=json.dumps(CONFIG), title="g", id="s")
        obj = SimpleNamespace(guid="d1", source=source, job=SimpleNamespace(source=source, id="j"), id="o")

        result = harvester().modify_package_dict(remote, obj)

        res = result["resources"][0]
        assert res["url"] == ours["url"] and res["url_type"] == "upload"
        assert res["hash"] == SHA and res["source_etag"] == '"abc"'
        assert res["source_downloaded"] == ours["source_downloaded"]
        assert res["source_url"] == FILE_URL
        assert res["source_last_modified"] == "2099-01-01T00:00:00"

    def test_one_failed_copy_does_not_stop_the_others(self, linked):
        dataset, resource = linked
        other = factories.Resource(package_id=dataset["id"], name="otro.csv", format="CSV",
                                   url=FILE_URL + "2", source_url=FILE_URL + "2")
        with mock.patch("ckanext.cordoba_portal.harvester.requests.get") as get:
            get.return_value = FakeResponse()
            with mock.patch.object(CordobaCKANHarvester, "_patch",
                                   side_effect=[Exception("File upload too large"), None]) as patch:
                harvester()._copy_files(dataset["id"])

        assert patch.call_count == 2
        assert patch.call_args_list[1][0][0]["id"] == other["id"]
