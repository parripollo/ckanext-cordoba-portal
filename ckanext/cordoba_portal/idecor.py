"""Harvester of IDECOR, the geoportal of the province (Mapas Córdoba).

The portal (https://www.mapascordoba.gob.ar) is a single-page app; its
two catalogs are static JSON files: ``datos/descargas.json`` (a tree of
themes with the downloadable layers: WFS links for the vectors, GeoTIFFs,
symbology, data dictionary and metadata PDFs) and ``datos/geoservicios.json``
(the WMS / WFS / WCS endpoints per layer). ``idecor`` joins both by layer
name and brings each layer here as one CKAN dataset:

- title (with the city or theme when several layers share it), the theme
  path, the layer name and the kind (vector, raster, external) as extras;
  the first level of the tree becomes a group (``idecor-catastro``);
- one copied file per kind of content, this is what makes the site a
  backup: the GeoJSON of a vector layer (fetched from the WFS), the GeoTIFF
  of a raster one (when it is not bigger than ``copy_max_mb``), the
  symbology, the data dictionary and the metadata;
- links for what is a service and not a file: the WMS, WFS and WCS of the
  layer, the Shapefile and KML the WFS serves on demand (the same data as
  the GeoJSON), and the layers that are only a viewer elsewhere.

The catalogs carry no dates: a copy is asked for again after
``recheck_days`` (the bucket answers 304 to an ETag, the WFS is downloaded
again and compared by hash). Nothing is requested faster than ``pause``
seconds apart; the WFS builds each file on request, give it time.

Source config (JSON), all keys optional::

    {"single_org": "idecor",         # organization of the datasets
                                      # (default: the one of the source)
     "categories": ["catastro"],      # first-level categories to harvest
                                      # (default: all of them)
     "geoservicios": true,            # service links, and the layers that
                                      # are only a geoservice
     "user_agent": "Mozilla/5.0 ... cbadatos.com.ar",
     "pause": 1,
     "groups": true,
     "copy_files": true,
     "copy_pause": 3,
     "copy_max_mb": 200,
     "recheck_days": 30}
"""
import json
import logging
import re
import time
import uuid
from collections import Counter
from datetime import datetime, timedelta
from urllib.parse import unquote, urlparse

import requests

from ckan import model
from ckan.lib.munge import munge_title_to_name
from ckan.plugins import toolkit
from ckanext.harvest.harvesters.base import HarvesterBase
from ckanext.harvest.model import HarvestObject

from ckanext.cordoba_portal.harvester import FileCopyMixin
from ckanext.cordoba_portal.plugin import cordoba_portal_source

log = logging.getLogger(__name__)

PORTAL = "idecor"
DOWNLOADS = "/datos/descargas.json"
GEOSERVICES = "/datos/geoservicios.json"
# where the relative paths of the downloads catalog live
FILES_BASE = "https://obs-idecor-lib.obs.sa-argentina-1.myhuaweicloud.com"
LAYER_NAME = re.compile(r"typeName=(?:[^:&]+:)?([^&]+)")
SERVICE = re.compile(r"/geoserver/([^/]+)/([^/?]+)/(wms|wfs|wcs)\b")
GEOJSON_WFS = ("%(host)s/geoserver/%(workspace)s/ows?service=WFS&version=1.0.0"
               "&request=GetFeature&typeName=%(workspace)s:%(layer)s&outputFormat=json")
# GeoJSON is WGS84 by definition; the portal's WFS links give the native
# projection (POSGAR 2007), which the map previews do not know
WGS84 = "&srsName=EPSG:4326"

# key in the catalog -> (what the resource is called, its format, copied?)
FILES = {
    "json": ("GeoJSON", "GeoJSON", True),
    "tiff": ("GeoTIFF", "GeoTIFF", True),
    "qml": ("Simbología (QGIS)", "QML", True),
    "lyr": ("Simbología (ArcGIS)", "LYR", True),
    "dd": ("Diccionario de datos", None, True),
    "metadatos": ("Metadatos", None, True),
    "shp": ("Shapefile (WFS)", "SHP", False),
    "kml": ("KML (WFS)", "KML", False),
}
SERVICES = (("wms", "WMS"), ("wfs", "WFS"), ("wcs", "WCS"))
KINDS = {"raster": "Ráster", "vector": "Vectorial", "external": "Enlace externo"}


# -- the catalogs, parsed --------------------------------------------------

def parse_downloads(tree, files_base=FILES_BASE):
    """The layers of the downloads catalog, one dict each: guid, title,
    path (titles of the tree above), description (of the theme the layer
    is in), category (first level), kind, layer, files {key: absolute
    url}, link, organismo."""
    layers = []

    def walk(node, path, group):
        for child in node.get("childs", []):
            if "childs" in child:
                walk(child, path + [child], group)
            else:
                layers.append(_layer(child, path, group, files_base))

    for top in tree:
        walk(top, [top], top)
    _disambiguate(layers)
    return layers


def _layer(leaf, path, group, files_base):
    files = {}
    for key in FILES:
        url = leaf.get(key)
        if url:
            files[key] = url if "://" in url else files_base + url
    if "json" in files and "GetFeature" in files["json"] and "srsName" not in files["json"]:
        files["json"] += WGS84
    name = None
    for key in ("json", "shp", "kml"):
        match = LAYER_NAME.search(leaf.get(key) or "")
        if match:
            name = match.group(1)
            break
    if not name and leaf.get("tiff"):
        # a raster is only a file; its GeoServer layer is named like it
        name = unquote(urlparse(leaf["tiff"]).path).rsplit("/", 1)[-1].rsplit(".", 1)[0]
    kind = "external" if leaf.get("externa") else \
        "raster" if leaf.get("category") == "raster" or "tiff" in files else "vector"
    title = " ".join((leaf.get("title") or name or "").split())
    return {
        "guid": name or "externa/" + munge_title_to_name(title),
        "title": title,
        "path": [p.get("title", "") for p in path],
        "description": (path[-1].get("description") or "").strip() if path else "",
        "category": group.get("category", ""),
        "group": {"category": group.get("category", ""), "title": group.get("title", ""),
                  "description": group.get("description", "")},
        "kind": kind,
        "layer": name,
        "files": files,
        "services": {},
        "link": leaf.get("link") if leaf.get("externa") else None,
        "organismo": leaf.get("organismo") or "",
    }


def _disambiguate(layers, always=()):
    """'Parcelas' is a layer of fourteen cities: the ones that share a
    title get the theme they are in appended; so do the layers in
    ``always`` (the geoservices name theirs 'Año 2025', 'Rural')."""
    counts = Counter(layer["title"] for layer in layers)
    for layer in layers:
        theme = layer["path"][-1] if layer["path"] else ""
        if theme and (counts[layer["title"]] > 1 or layer["guid"] in always) \
                and not layer["title"].endswith(" - " + theme):
            layer["title"] = "%s - %s" % (layer["title"], theme)


def parse_geoservices(tree):
    """{layer name: {title, path, services {wms|wfs|wcs: GetCapabilities
    url}}} out of the geoservices catalog. The general endpoints (no
    layer in the URL) are left out."""
    layers = {}

    def walk(node, path):
        for child in node.get("children", []):
            match = SERVICE.search(child.get("href") or "")
            if match:
                entry = layers.setdefault(match.group(2), {
                    # path ends with this node, the layer itself
                    "title": node.get("label", ""), "path": path[:-1], "workspace": match.group(1),
                    "host": "%s://%s" % urlparse(child["href"])[:2], "services": {}})
                entry["services"][match.group(3)] = child["href"]
            if child.get("children"):
                walk(child, path + [child.get("label", "")])

    for top in tree:
        walk(top, [top.get("label", "")])
    return layers


def join_geoservices(layers, services):
    """The service links onto the layers of the downloads; a layer that is
    only a geoservice becomes a layer of its own, with the GeoJSON the WFS
    can give."""
    by_name = {layer["layer"]: layer for layer in layers if layer["layer"]}
    # the themes of the downloads, by title: the geoservices tree names
    # the same themes ("Por Temas" > "Geografía Social")
    groups = {layer["group"]["title"]: layer["group"] for layer in layers if layer["group"]["title"]}
    extra = []
    for name, entry in services.items():
        if name in by_name:
            by_name[name]["services"] = entry["services"]
            continue
        path = [p for p in entry["path"] if p and p != "Por Temas"]
        group = next((groups[p] for p in path if p in groups), None) or {
            "category": munge_title_to_name(path[0]) if path else "geoservicios",
            "title": path[0] if path else "Geoservicios", "description": ""}
        files = {}
        if "wfs" in entry["services"]:
            files["json"] = GEOJSON_WFS % dict(entry, layer=name) + WGS84
        extra.append({
            "guid": name,
            "title": " ".join(entry["title"].split()) or name,
            "path": path,
            "description": "",
            "category": group["category"],
            "group": group,
            "kind": "raster" if "wcs" in entry["services"] else "vector",
            "layer": name,
            "files": files,
            "services": entry["services"],
            "link": None,
            "organismo": "",
        })
    joined = layers + extra
    _disambiguate(joined, always={layer["guid"] for layer in extra})
    return joined


def file_format(url):
    path = unquote(urlparse(url).path)
    return path.rsplit(".", 1)[-1].upper() if "." in path.rsplit("/", 1)[-1] else ""


class IdecorHarvester(FileCopyMixin, HarvesterBase):

    config = {}

    def info(self):
        return {
            "name": "idecor",
            "title": "IDECOR (Mapas Córdoba)",
            "description": (
                "Harvest del geoportal de la Infraestructura de Datos Espaciales de "
                "Córdoba: una capa, un dataset; se copian GeoJSON, GeoTIFF, simbología "
                "y metadatos, los geoservicios quedan como enlaces."
            ),
            "form_config_interface": "Text",
        }

    # -- config ------------------------------------------------------------

    def _set_config(self, config_str):
        self.config = json.loads(config_str) if config_str else {}

    def validate_config(self, config):
        if not config:
            return config
        config_obj = json.loads(config)
        for key in ("pause", "copy_pause", "copy_max_mb", "recheck_days"):
            if key in config_obj:
                float(config_obj[key])
        if "categories" in config_obj and not isinstance(config_obj["categories"], list):
            raise ValueError("categories must be a list of first-level categories")
        if config_obj.get("single_org"):
            try:
                toolkit.get_action("organization_show")(
                    {"ignore_auth": True}, {"id": config_obj["single_org"]})
            except toolkit.ObjectNotFound:
                raise ValueError("single_org: organization %s does not exist"
                                 % config_obj["single_org"])
        return config

    # -- the catalogs ------------------------------------------------------

    def _get_json(self, url):
        time.sleep(float(self.config.get("pause", 1)))
        response = requests.get(url, headers=self._request_headers(), timeout=(30, 120))
        response.raise_for_status()
        return response.json()

    # -- gather: one object per layer --------------------------------------

    def gather_stage(self, harvest_job):
        self._set_config(harvest_job.source.config)
        base = harvest_job.source.url.rstrip("/")
        try:
            layers = parse_downloads(self._get_json(base + DOWNLOADS),
                                     self.config.get("files_base", FILES_BASE))
            if self.config.get("geoservicios", True):
                layers = join_geoservices(layers, parse_geoservices(self._get_json(base + GEOSERVICES)))
        except (requests.RequestException, ValueError, KeyError, TypeError) as e:
            self._save_gather_error("Could not read the catalogs of %s: %s" % (base, e), harvest_job)
            return None

        wanted = self.config.get("categories")
        object_ids = []
        seen = set()
        for layer in layers:
            if wanted and layer["category"] not in wanted:
                continue
            if layer["guid"] in seen:
                # the same layer listed under two themes: once is enough
                log.info("Layer %s is listed twice in the catalog", layer["guid"])
                continue
            seen.add(layer["guid"])
            obj = HarvestObject(guid=layer["guid"], job=harvest_job, content=json.dumps(layer))
            obj.save()
            object_ids.append(obj.id)
        if not object_ids:
            self._save_gather_error("No layers found at %s" % base, harvest_job)
            return None
        return object_ids

    # -- fetch: nothing more to ask, the catalog has it all ----------------

    def fetch_stage(self, harvest_object):
        return True

    # -- import ------------------------------------------------------------

    def import_stage(self, harvest_object):
        self._set_config(harvest_object.source.config)
        if not harvest_object.content:
            self._save_object_error("Empty content for object %s" % harvest_object.id,
                                    harvest_object, "Import")
            return False
        layer = json.loads(harvest_object.content)

        previous = self._previous_object(harvest_object)
        if previous and previous.package_id and previous.content == harvest_object.content:
            log.info("Layer %s did not change in the catalog", harvest_object.guid)
            result, package_id = "unchanged", previous.package_id
        else:
            try:
                package_dict = self._package_dict(layer, harvest_object)
            except Exception as e:
                log.exception(e)
                self._save_object_error("Could not build the dataset %s: %s"
                                        % (harvest_object.guid, e), harvest_object, "Import")
                return False
            result = self._create_or_update_package(
                package_dict, harvest_object, package_dict_form="package_show")
            package_id = harvest_object.package_id or package_dict["id"]

        if result in (True, "unchanged") and self.config.get("copy_files", True):
            try:
                self._copy_files(package_id)
            except Exception as e:
                # the dataset is in; the files are tried again next time
                log.exception("Copying the files of %s failed: %s", package_id, e)
        return result

    @staticmethod
    def _previous_object(harvest_object):
        return model.Session.query(HarvestObject) \
            .filter(HarvestObject.guid == harvest_object.guid,
                    HarvestObject.harvest_source_id == harvest_object.harvest_source_id,
                    HarvestObject.current == True,  # noqa: E712
                    HarvestObject.id != harvest_object.id) \
            .first()

    # -- the dataset -------------------------------------------------------

    def _package_dict(self, layer, harvest_object):
        portal = cordoba_portal_source(PORTAL)
        base = harvest_object.source.url.rstrip("/")
        package_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "%s/%s" % (base, layer["guid"])))
        extras = [{"key": "tema", "value": " / ".join(p for p in layer["path"] if p)},
                  {"key": "tipo", "value": KINDS[layer["kind"]]}]
        if layer["layer"]:
            extras.append({"key": "capa", "value": layer["layer"]})
        if layer["organismo"]:
            extras.append({"key": "organismo", "value": layer["organismo"]})
        return {
            "id": package_id,
            "name": munge_title_to_name(layer["title"])[:100],
            "title": layer["title"],
            "notes": self._notes(layer),
            "owner_org": self._owner_org(harvest_object),
            "license_id": "notspecified",
            "source_portal": portal["value"],
            "source_url": base + "/#/descargas",
            "extras": extras,
            "groups": self._groups(layer["group"], portal) if self.config.get("groups", True) else [],
            "resources": self._resources(layer, package_id),
        }

    @staticmethod
    def _notes(layer):
        """The theme the layer is in and what the catalog says of it."""
        path = " / ".join(p for p in layer["path"] if p)
        description = layer.get("description") or ""
        if path and description:
            return "%s: %s" % (path, description)
        return path or description

    def _owner_org(self, harvest_object):
        if self.config.get("single_org"):
            return self.config["single_org"]
        source = toolkit.get_action("package_show")(
            self._context(), {"id": harvest_object.source.id})
        return source.get("owner_org")

    def _groups(self, group, portal):
        """The first level of the catalog as a group, created the first time."""
        if not group.get("category"):
            return []
        name = ("%s-%s" % (portal["prefix"], munge_title_to_name(group["category"])))[:100]
        try:
            found = toolkit.get_action("group_show")(self._context(), {"id": name})
        except toolkit.ObjectNotFound:
            found = toolkit.get_action("group_create")(self._context(), {
                "name": name, "title": group.get("title") or group["category"],
                "description": group.get("description") or ""})
            log.info("Group %s created from the catalog", name)
        return [{"id": found["id"], "name": found["name"]}]

    def _resources(self, layer, package_id):
        copies = self._existing_copies(package_id)
        title = layer["title"]
        resources = []

        def add(key, name, fmt, url, copied):
            resource = {
                "id": str(uuid.uuid5(uuid.NAMESPACE_URL, "%s#%s" % (layer["guid"], key))),
                "name": ("%s - %s" % (title, name))[:100],
                "format": fmt,
                "url": url,
            }
            if copied:
                resource["source_url"] = url
                resource.update(copies.get(resource["id"], {}))
            resources.append(resource)

        for key, (name, fmt, copied) in FILES.items():
            if key in layer["files"]:
                url = layer["files"][key]
                add(key, name, fmt or file_format(url), url, copied)
        for key, fmt in SERVICES:
            if key in layer["services"]:
                add(key, fmt, fmt, layer["services"][key], False)
        if layer["link"]:
            add("link", layer["organismo"] or urlparse(layer["link"]).netloc, "HTML", layer["link"], False)
        return resources

    # -- the copies (FileCopyMixin hooks) ----------------------------------

    def _copy_filename(self, response, url):
        """A WFS answer is 'ows' by URL: name it after the layer."""
        match = LAYER_NAME.search(url)
        if "GetFeature" in url and match:
            fmt = (re.search(r"outputFormat=([^&]+)", url) or [None, "json"])[1].lower()
            return "%s.%s" % (match.group(1), {"json": "geojson", "shape-zip": "zip"}.get(fmt, fmt))
        return FileCopyMixin._copy_filename(response, url)

    def _needs_copy(self, resource):
        """The catalog has no dates: a copy is asked for again after
        ``recheck_days`` (with its ETag, so the bucket answers 304)."""
        if resource.get("url_type") != "upload" or not resource.get("source_downloaded"):
            return True
        recheck = timedelta(days=float(self.config.get("recheck_days", 30)))
        try:
            since = datetime.fromisoformat(resource["source_downloaded"][:19])
        except ValueError:
            return True
        return datetime.utcnow() - since > recheck
