"""The idecor harvester: the two catalogs, the layers and their files."""
import hashlib
import json
import os
from datetime import datetime, timedelta
from unittest import mock
from urllib.parse import urlparse

import pytest
import requests

from ckan.tests import factories
from ckan.tests.helpers import call_action
from ckanext.harvest.model import HarvestGatherError, HarvestObject
from ckanext.harvest.tests.factories import HarvestSourceObj, HarvestJobObj

from ckanext.cordoba_portal import idecor
from ckanext.cordoba_portal.idecor import IdecorHarvester

PORTAL = "https://www.mapascordoba.gob.ar"
WFS = "https://idecor-ws.mapascordoba.gob.ar/geoserver/idecor"
BUCKET = idecor.FILES_BASE
HERE = os.path.dirname(__file__)


def fixture(name):
    with open(os.path.join(HERE, "data", "idecor-" + name), encoding="utf-8") as f:
        return json.load(f)   # the real catalogs, trimmed to a few layers


CATALOGS = {"/datos/descargas.json": fixture("descargas.json"),
            "/datos/geoservicios.json": fixture("geoservicios.json")}
FILE = b'{"type": "FeatureCollection", "features": []}'
SHA = hashlib.sha256(FILE).hexdigest()
CONFIG = {"pause": 0, "copy_pause": 0, "user_agent": "Mozilla/5.0 test cbadatos.com.ar"}
MANI_JSON = (WFS + "/ows?service=WFS&version=1.0.0&request=GetFeature"
             "&typeName=idecor:cultivos_mani_historico&outputFormat=json&srsName=EPSG:4326")


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
    """requests.get of the portal: the catalogs, the WFS, the bucket."""

    def __init__(self, file_status=200, file_content=FILE, tiff_bytes=100):
        self.calls = []
        self.file_status = file_status
        self.file_content = file_content
        self.tiff_bytes = tiff_bytes

    def __call__(self, url, params=None, headers=None, **kwargs):
        self.calls.append((url, headers or {}, kwargs.get("stream", False)))
        parsed = urlparse(url)
        if parsed.path in CATALOGS:
            return FakeResponse(CATALOGS[parsed.path])
        if "GetFeature" in url:
            return FakeResponse(status_code=self.file_status, content=self.file_content, headers={
                "Content-Type": "application/json", "Content-Length": str(len(self.file_content))})
        if url.startswith(BUCKET):
            if (headers or {}).get("If-None-Match") == '"etag-1"':
                return FakeResponse(status_code=304)
            content = b"x" * self.tiff_bytes if url.endswith(".tif") else b"%PDF-or-qml"
            return FakeResponse(status_code=self.file_status, content=content, headers={
                "Content-Type": "application/octet-stream", "Content-Length": str(len(content)),
                "ETag": '"etag-1"', "Last-Modified": "Wed, 19 Nov 2025 13:15:45 GMT"})
        return FakeResponse(status_code=404)

    def file_calls(self):
        return [(u, h) for u, h, stream in self.calls if stream]


@pytest.fixture(autouse=True)
def forget_the_cached_harvest_user():
    IdecorHarvester()._user_name = None


@pytest.fixture
def source(with_plugins, clean_db):
    org = factories.Organization(name="idecor", title="IDECOR", source_portal="idecor")
    return HarvestSourceObj(url=PORTAL, source_type="idecor", owner_org=org["id"],
                            config=json.dumps(CONFIG), title="IDECOR")


def harvest(source, portal=None, copy_files=True, config=None):
    """One whole run: gather, fetch, import. Returns (portal, results)."""
    portal = portal or FakePortal()
    job = HarvestJobObj(source=source)
    harvester = IdecorHarvester()
    results = {}
    with mock.patch("ckanext.cordoba_portal.idecor.requests.get", portal), \
            mock.patch("ckanext.cordoba_portal.harvester.requests.get", portal):
        source.config = json.dumps(dict(CONFIG, copy_files=copy_files, **(config or {})))
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
        assert IdecorHarvester().validate_config("") == ""

    def test_numbers_categories_and_org_are_checked(self):
        with pytest.raises(ValueError):
            IdecorHarvester().validate_config(json.dumps({"recheck_days": "soon"}))
        with pytest.raises(ValueError, match="categories"):
            IdecorHarvester().validate_config(json.dumps({"categories": "catastro"}))
        with pytest.raises(ValueError, match="single_org"):
            IdecorHarvester().validate_config(json.dumps({"single_org": "nope"}))

    def test_info(self):
        assert IdecorHarvester().info()["name"] == "idecor"


class TestGather:

    def test_one_object_per_layer_of_both_catalogs(self, source):
        job = HarvestJobObj(source=source)
        portal = FakePortal()
        with mock.patch("ckanext.cordoba_portal.idecor.requests.get", portal):
            ids = IdecorHarvester().gather_stage(job)

        assert sorted(HarvestObject.get(i).guid for i in ids) == [
            "chili_parcelas", "coberturas_estivales_2425", "cultivos_mani_historico",
            "externa/hidrografia", "localidad_punto", "parcelas"]
        layer = json.loads(next(HarvestObject.get(i).content for i in ids
                                if HarvestObject.get(i).guid == "cultivos_mani_historico"))
        assert layer["title"] == "Lotes con cultivo de maní - Campañas 2016/17 a 2023/24"
        assert layer["path"] == ["Información Agropecuaria", "Cultivos y Estimaciones"]
        assert layer["category"] == "agropecuario" and layer["kind"] == "vector"
        assert layer["files"]["json"] == MANI_JSON
        assert layer["files"]["qml"] == BUCKET + "/simbologia/biota_land_cover/sty_cultivos_mani_historico.qml"
        assert layer["services"] == {
            "wms": WFS + "/cultivos_mani_historico/wms?request=GetCapabilities",
            "wfs": WFS + "/cultivos_mani_historico/wfs?request=GetCapabilities"}
        # the two catalogs, once each, with the browser's User-Agent
        assert [urlparse(u).path for u, _, _ in portal.calls] == \
            ["/datos/descargas.json", "/datos/geoservicios.json"]
        assert all(h["User-Agent"] == CONFIG["user_agent"] for _, h, _ in portal.calls)

    def test_only_some_categories(self, source):
        job = HarvestJobObj(source=source)
        source.config = json.dumps(dict(CONFIG, categories=["catastro", "ciudades"]))
        with mock.patch("ckanext.cordoba_portal.idecor.requests.get", FakePortal()):
            ids = IdecorHarvester().gather_stage(job)
        assert sorted(HarvestObject.get(i).guid for i in ids) == ["chili_parcelas", "parcelas"]

    def test_without_the_geoservices(self, source):
        job = HarvestJobObj(source=source)
        source.config = json.dumps(dict(CONFIG, geoservicios=False))
        portal = FakePortal()
        with mock.patch("ckanext.cordoba_portal.idecor.requests.get", portal):
            ids = IdecorHarvester().gather_stage(job)
        assert len(ids) == 5 and len(portal.calls) == 1     # not localidad_punto
        layer = json.loads(HarvestObject.get(ids[0]).content)
        assert layer["services"] == {}

    def test_portal_down_is_a_gather_error(self, source):
        job = HarvestJobObj(source=source)
        with mock.patch("ckanext.cordoba_portal.idecor.requests.get",
                        return_value=FakeResponse(status_code=500)):
            assert IdecorHarvester().gather_stage(job) is None
        errors = job.gather_errors if hasattr(job, "gather_errors") else \
            HarvestGatherError.filter(harvest_job_id=job.id).all()
        assert errors and "500" in errors[0].message


class TestImport:

    def test_the_dataset(self, source):
        harvest(source, copy_files=False)

        dataset = call_action("package_show", id="lotes-con-cultivo-de-mani-campanas-2016-17-a-2023-24")
        assert dataset["title"] == "Lotes con cultivo de maní - Campañas 2016/17 a 2023/24"
        assert dataset["notes"] == ("Información Agropecuaria / Cultivos y Estimaciones: "
                                    "Distribución de cultivos.")
        assert dataset["source_portal"] == "idecor"
        assert dataset["source_url"] == PORTAL + "/#/descargas"
        assert dataset["organization"]["name"] == "idecor"
        extras = {e["key"]: e["value"] for e in dataset["extras"] if not e["key"].startswith("harvest_")}
        assert extras == {
            "tema": "Información Agropecuaria / Cultivos y Estimaciones",
            "tipo": "Vectorial",
            "capa": "cultivos_mani_historico",
        }
        assert [g["name"] for g in dataset["groups"]] == ["idecor-agropecuario"]
        group = call_action("group_show", id="idecor-agropecuario")
        assert group["title"] == "Información Agropecuaria"
        assert group["description"] == "Capas Temáticas del Sector Agropecuario."
        assert call_action("package_search", q="")["count"] == 6

    def test_layers_that_share_a_title_are_told_apart(self, source):
        harvest(source, copy_files=False)

        catastro = call_action("package_show", id="parcelas-catastro")
        assert catastro["title"] == "Parcelas - Catastro"
        assert [g["name"] for g in catastro["groups"]] == ["idecor-catastro"]
        city = call_action("package_show", id="parcelas-chilibroste")
        assert city["title"] == "Parcelas - Chilibroste"
        assert {e["key"]: e["value"] for e in city["extras"]}["tema"] == "Ciudades / Chilibroste"
        assert [g["name"] for g in city["groups"]] == ["idecor-ciudades"]

    def test_the_resources_of_a_vector_layer(self, source):
        harvest(source, copy_files=False)

        dataset = call_action("package_show", id="lotes-con-cultivo-de-mani-campanas-2016-17-a-2023-24")
        by_name = {r["name"].split(" - ", 2)[-1]: r for r in dataset["resources"]}
        assert list(by_name) == ["GeoJSON", "Simbología (QGIS)", "Diccionario de datos", "Metadatos",
                                 "Shapefile (WFS)", "KML (WFS)", "WMS", "WFS"]
        geojson = by_name["GeoJSON"]
        assert geojson["format"] == "GeoJSON" and geojson["url"] == MANI_JSON
        assert geojson["source_url"] == MANI_JSON                   # copied
        assert by_name["Diccionario de datos"]["format"] == "PDF"
        assert by_name["Diccionario de datos"]["source_url"].startswith(BUCKET + "/dicdatos/")
        assert by_name["Simbología (QGIS)"]["format"] == "QML"
        shp = by_name["Shapefile (WFS)"]
        assert shp["format"] == "SHP" and "SHAPE-ZIP" in shp["url"] and not shp.get("source_url")
        wms = by_name["WMS"]
        assert wms["format"] == "WMS" and wms["url"].endswith("/cultivos_mani_historico/wms?request=GetCapabilities")
        assert not wms.get("source_url")

    def test_a_raster_an_external_and_a_geoservice_only_layer(self, source):
        harvest(source, copy_files=False)

        raster = call_action("package_show", id="coberturas-agricolas-estivales-2024-2025")
        assert {e["key"]: e["value"] for e in raster["extras"]}["tipo"] == "Ráster"
        names = [r["name"].split(" - ", 1)[-1] for r in raster["resources"]]
        assert names == ["GeoTIFF", "Simbología (QGIS)", "Metadatos", "WMS", "WCS"]
        tiff = raster["resources"][0]
        assert tiff["format"] == "GeoTIFF" and tiff["source_url"] == \
            BUCKET + "/download_raster/rendimiento/coberturas_estivales_2425.tif"

        external = call_action("package_show", id="hidrografia")
        extras = {e["key"]: e["value"] for e in external["extras"]}
        assert extras["tipo"] == "Enlace externo"
        assert extras["organismo"] == "Administración Provincial de Recursos Hídricos"
        assert len(external["resources"]) == 1
        assert external["resources"][0]["format"] == "HTML"
        assert external["resources"][0]["url"].startswith("https://experience.arcgis.com/")
        assert external["resources"][0]["name"].endswith("Administración Provincial de Recursos Hídricos")

        # only in the geoservices catalog: the GeoJSON the WFS can give, and the links
        points = call_action("package_show", id="localidades-puntos-asentamiento")
        assert {e["key"]: e["value"] for e in points["extras"]}["tema"] == "Geografía Social / Asentamiento"
        names = [r["name"].rsplit(" - ", 1)[-1] for r in points["resources"]]
        assert names == ["GeoJSON", "WMS", "WFS"]
        assert points["resources"][0]["source_url"] == \
            WFS + ("/ows?service=WFS&version=1.0.0&request=GetFeature&typeName=idecor:localidad_punto"
                   "&outputFormat=json&srsName=EPSG:4326")

    def test_second_run_is_unchanged_and_stable(self, source):
        harvest(source, copy_files=False)
        before = call_action("package_show", id="parcelas-catastro")

        portal, results = harvest(source, copy_files=False)

        assert set(results.values()) == {"unchanged"}
        after = call_action("package_show", id="parcelas-catastro")
        assert after["id"] == before["id"]
        assert [r["id"] for r in after["resources"]] == [r["id"] for r in before["resources"]]
        assert after["metadata_modified"] == before["metadata_modified"]

    def test_a_changed_catalog_updates_the_dataset(self, source):
        harvest(source, copy_files=False)
        changed = json.loads(json.dumps(CATALOGS["/datos/descargas.json"]))
        changed[0]["childs"][0]["childs"][0]["title"] = "Lotes con cultivo de maní - Campañas 2016/17 a 2024/25"

        with mock.patch.dict(CATALOGS, {"/datos/descargas.json": changed}):
            portal, results = harvest(source, copy_files=False)

        assert results["cultivos_mani_historico"] is True and results["parcelas"] == "unchanged"
        dataset = call_action("package_show", id="lotes-con-cultivo-de-mani-campanas-2016-17-a-2023-24")
        assert dataset["title"].endswith("2024/25")


class TestCopy:

    def test_files_are_copied_services_are_not(self, source):
        portal, results = harvest(source)

        fetched = sorted(u for u, _ in portal.file_calls())
        # 4 vector layers: GeoJSON (+ qml, dd, metadatos for the 3 of the
        # downloads); the raster: tiff, qml, metadatos. No WMS, no shp, no viewer.
        assert len(fetched) == 4 + 9 + 3
        assert not any("GetCapabilities" in u or "SHAPE-ZIP" in u or "arcgis" in u for u in fetched)
        dataset = call_action("package_show", id="lotes-con-cultivo-de-mani-campanas-2016-17-a-2023-24")
        geojson = dataset["resources"][0]
        assert geojson["url_type"] == "upload"
        assert geojson["url"].endswith("/download/cultivos_mani_historico.geojson")
        assert geojson["hash"] == SHA and geojson["source_url"] == MANI_JSON
        qml = dataset["resources"][1]
        assert qml["url_type"] == "upload" and qml["url"].endswith("/sty_cultivos_mani_historico.qml")
        assert qml["source_etag"] == '"etag-1"'
        assert dataset["resources"][4].get("url_type") != "upload"     # the shapefile link
        tiff = call_action("package_show", id="coberturas-agricolas-estivales-2024-2025")["resources"][0]
        assert tiff["url_type"] == "upload" and tiff["url"].endswith("/coberturas_estivales_2425.tif")

    def test_a_raster_too_big_stays_a_link(self, source):
        portal = FakePortal(tiff_bytes=3 * 1024 * 1024)
        harvest(source, portal=portal, config={"copy_max_mb": 2})

        tiff = call_action("package_show", id="coberturas-agricolas-estivales-2024-2025")["resources"][0]
        assert tiff.get("url_type") != "upload"
        assert tiff["url"] == BUCKET + "/download_raster/rendimiento/coberturas_estivales_2425.tif"
        # a big Content-Length is enough to say no: the body is not read
        assert (tiff["url"], {"User-Agent": CONFIG["user_agent"]}) in portal.file_calls()

    def test_copies_are_not_asked_again_until_recheck_days(self, source):
        harvest(source)
        geojson = call_action("package_show", id="parcelas-catastro")["resources"][0]

        portal, results = harvest(source)

        assert portal.file_calls() == []
        assert results["parcelas"] == "unchanged"
        assert call_action("resource_show", id=geojson["id"])["url"] == geojson["url"]

    def test_after_recheck_days_the_bucket_says_304_and_the_wfs_is_compared(self, source):
        harvest(source)
        dataset = call_action("package_show", id="parcelas-catastro")
        old = (datetime.utcnow() - timedelta(days=40)).replace(microsecond=0).isoformat()
        for r in dataset["resources"]:
            if r.get("source_url"):
                call_action("resource_patch", id=r["id"], source_downloaded=old)

        portal, results = harvest(source)

        fetched = {u: h for u, h in portal.file_calls()}
        assert len(fetched) == 4      # the four copies of that dataset only
        qml = next(u for u in fetched if u.endswith(".qml"))
        assert fetched[qml]["If-None-Match"] == '"etag-1"'      # -> 304, nothing downloaded
        after = call_action("package_show", id="parcelas-catastro")
        for r in after["resources"]:
            if r.get("source_url"):
                assert r["source_downloaded"] > old
        assert after["resources"][0]["hash"] == SHA               # same GeoJSON, kept

    def test_a_failed_copy_stays_a_link_and_is_retried(self, source):
        harvest(source, portal=FakePortal(file_status=500))
        geojson = call_action("package_show", id="parcelas-catastro")["resources"][0]
        assert geojson.get("url_type") != "upload" and geojson["url"] == geojson["source_url"]

        portal, results = harvest(source)

        assert results["parcelas"] == "unchanged"
        assert len(portal.file_calls()) == 16
        assert call_action("resource_show", id=geojson["id"])["url_type"] == "upload"


class TestPieces:

    def test_parse_downloads(self):
        layers = idecor.parse_downloads(CATALOGS["/datos/descargas.json"])
        assert [(x["guid"], x["title"]) for x in layers] == [
            ("cultivos_mani_historico", "Lotes con cultivo de maní - Campañas 2016/17 a 2023/24"),
            ("coberturas_estivales_2425", "Coberturas Agrícolas Estivales 2024/2025"),
            ("externa/hidrografia", "Hidrografía"),
            ("parcelas", "Parcelas - Catastro"),
            ("chili_parcelas", "Parcelas - Chilibroste"),
        ]
        raster = layers[1]
        assert raster["kind"] == "raster" and raster["layer"] == "coberturas_estivales_2425"
        assert raster["files"]["tiff"].startswith(BUCKET + "/download_raster/")
        assert layers[2]["kind"] == "external" and layers[2]["files"] == {}

    def test_parse_geoservices(self):
        services = idecor.parse_geoservices(CATALOGS["/datos/geoservicios.json"])
        assert sorted(services) == ["chili_parcelas", "coberturas_estivales_2425",
                                    "cultivos_mani_historico", "localidad_punto", "parcelas"]
        assert services["localidad_punto"]["title"] == "Localidades (puntos)"
        assert services["localidad_punto"]["path"] == ["Por Temas", "Geografía Social", "Asentamiento"]
        assert sorted(services["coberturas_estivales_2425"]["services"]) == ["wcs", "wms"]
        assert services["parcelas"]["workspace"] == "idecor"
        assert services["parcelas"]["host"] == "https://idecor-ws.mapascordoba.gob.ar"

    def test_join(self):
        layers = idecor.parse_downloads(CATALOGS["/datos/descargas.json"])
        joined = idecor.join_geoservices(layers, idecor.parse_geoservices(CATALOGS["/datos/geoservicios.json"]))
        assert len(joined) == 6
        assert sorted(joined[0]["services"]) == ["wfs", "wms"]
        only = joined[-1]
        assert only["guid"] == "localidad_punto" and only["kind"] == "vector"
        assert only["title"] == "Localidades (puntos) - Asentamiento"    # geoservice names are short
        assert only["path"] == ["Geografía Social", "Asentamiento"]
        assert only["category"] == "geografia-social"     # no such theme in the trimmed downloads
        assert list(only["files"]) == ["json"]

    def test_copy_filename(self):
        h = IdecorHarvester()
        assert h._copy_filename(FakeResponse(), MANI_JSON) == "cultivos_mani_historico.geojson"
        assert h._copy_filename(FakeResponse(), MANI_JSON.replace("=json", "=SHAPE-ZIP")) == \
            "cultivos_mani_historico.zip"
        assert h._copy_filename(FakeResponse(), BUCKET + "/simbologia/x/sty.qml") == "sty.qml"
