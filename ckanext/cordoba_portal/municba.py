"""Harvester of the open data portal of the city of Cordoba (Muni CBA).

The portal (https://gobiernoabierto.cordoba.gob.ar) is not CKAN. Its open
API lists the published datasets, and for each one its *versions* and the
*resources* of every version; the files live on S3 behind signed URLs that
last an hour. ``municba`` brings each dataset here as one CKAN dataset:

- title, slug, description and tags as they are; the category and its
  parent category become groups (``municba-<slug>``); the update frequency,
  the category and the sources are kept as extras; the license comes from
  the resources (CC BY-SA on the portal);
- every resource of every version is one resource here, named after the
  version ("Mapa de barrios - 2016 (CSV)"); external links stay links;
- the files are copied here (``FileCopyMixin``): a fresh signed URL is
  asked for right before each download, and a copy is checked again only
  when its version changed or after ``recheck_days``.

Datasets that disappear from the portal are left as they are. Nothing is
requested faster than ``pause`` seconds apart.

Source config (JSON), all keys optional::

    {"single_org": "municba",        # organization of the datasets
                                      # (default: the one of the source)
     "user_agent": "Mozilla/5.0 ... cbadatos.com.ar",
     "pause": 1,                      # seconds between two API requests
     "groups": true,                  # categories as groups
     "copy_files": true,
     "copy_pause": 3,
     "copy_max_mb": 200,
     "recheck_days": 30}              # re-ask the portal for a copied file
"""
import json
import logging
import re
import time
import uuid
from datetime import datetime, timedelta
from urllib.parse import urlparse, unquote

import requests

from ckan import model
from ckan.lib.munge import munge_title_to_name
from ckan.plugins import toolkit
from ckanext.harvest.harvesters.base import HarvesterBase
from ckanext.harvest.model import HarvestObject

from ckanext.cordoba_portal.harvester import FileCopyMixin
from ckanext.cordoba_portal.plugin import cordoba_portal_source

log = logging.getLogger(__name__)

PORTAL = "municba"
API = "/api/datos-abiertos"
PAGE_SIZE = 100
PUBLISHED = "Publicado"
FILE_TYPE = "Archivo"
# descriptions the portal uses as "none"
EMPTY_NOTES = {None, "", "."}
# the portal's icon names that are not a file extension
ICON_FORMATS = {"web": "HTML", "googlesheet": "Google Sheets", "drive": "Google Sheets", "map": "KML"}
KNOWN_EXTENSIONS = {
    "csv", "xls", "xlsx", "ods", "pdf", "kml", "kmz", "shp", "zip", "rar",
    "json", "geojson", "txt", "doc", "docx", "odt", "xml",
}
TAG_CHARS = re.compile(r"[^\w\s\-.]", re.UNICODE)


class MuniCBAHarvester(FileCopyMixin, HarvesterBase):

    config = {}

    def info(self):
        return {
            "name": "municba",
            "title": "Muni CBA (Gobierno Abierto)",
            "description": (
                "Harvest del portal de datos abiertos de la Municipalidad de "
                "Córdoba, copiando los archivos."
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
        if config_obj.get("single_org"):
            try:
                toolkit.get_action("organization_show")(
                    {"ignore_auth": True}, {"id": config_obj["single_org"]})
            except toolkit.ObjectNotFound:
                raise ValueError("single_org: organization %s does not exist"
                                 % config_obj["single_org"])
        return config

    # -- the portal's API --------------------------------------------------

    def _get_json(self, url, params=None):
        time.sleep(float(self.config.get("pause", 1)))
        response = requests.get(url, params=params, headers=self._request_headers(),
                                timeout=(30, 120))
        response.raise_for_status()
        return response.json()

    def _get_all(self, url):
        """Every result of a paginated endpoint, following ``next``."""
        results = []
        params = {"size": PAGE_SIZE}
        while url:
            page = self._get_json(url, params)
            results.extend(page.get("results", []))
            url = page.get("next")
            params = None
        return results

    # -- gather: one object per published dataset --------------------------

    def gather_stage(self, harvest_job):
        self._set_config(harvest_job.source.config)
        base = harvest_job.source.url.rstrip("/")
        try:
            categories = {c["id"]: c for c in self._get_all(base + API + "/categoria")}
            datasets = self._get_all(base + API + "/dato")
        except (requests.RequestException, ValueError) as e:
            self._save_gather_error("Could not list the datasets of %s: %s" % (base, e),
                                    harvest_job)
            return None

        object_ids = []
        for dato in datasets:
            if dato.get("estado") != PUBLISHED:
                continue
            content = {"dato": dato, "categoria": categories.get(self._category_id(dato))}
            obj = HarvestObject(guid=str(dato["id"]), job=harvest_job,
                                content=json.dumps(content))
            obj.save()
            object_ids.append(obj.id)
        if not object_ids:
            self._save_gather_error("No published datasets found at %s" % base, harvest_job)
        return object_ids

    @staticmethod
    def _category_id(dato):
        link = (dato.get("links") or {}).get("categoria") or ""
        tail = link.rstrip("/").rsplit("/", 1)[-1]
        return int(tail) if tail.isdigit() else None

    # -- fetch: the versions and their resources ---------------------------

    def fetch_stage(self, harvest_object):
        self._set_config(harvest_object.source.config)
        content = json.loads(harvest_object.content)
        try:
            versions = self._get_all(content["dato"]["links"]["versiones"])
            for version in versions:
                version["recursos"] = self._get_all(version["links"]["recursos"])
        except (requests.RequestException, ValueError, KeyError) as e:
            self._save_object_error("Could not fetch the versions of %s: %s"
                                    % (harvest_object.guid, e), harvest_object, "Fetch")
            return False
        content["versiones"] = versions
        harvest_object.content = json.dumps(content)
        harvest_object.save()
        return True

    # -- import ------------------------------------------------------------

    def import_stage(self, harvest_object):
        self._set_config(harvest_object.source.config)
        if not harvest_object.content:
            self._save_object_error("Empty content for object %s" % harvest_object.id,
                                    harvest_object, "Import")
            return False
        content = json.loads(harvest_object.content)
        if "versiones" not in content:
            self._save_object_error("Object %s was not fetched" % harvest_object.id,
                                    harvest_object, "Import")
            return False

        previous = self._previous_object(harvest_object)
        if previous and previous.package_id and \
                self._fingerprint(previous.content) == self._fingerprint(harvest_object.content):
            log.info("Dataset %s did not change on the portal", harvest_object.guid)
            result, package_id = "unchanged", previous.package_id
        else:
            try:
                package_dict = self._package_dict(content, harvest_object)
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

    @staticmethod
    def _fingerprint(content):
        """The content without the signatures of the file URLs, which change
        on every request."""
        if not content:
            return None
        return re.sub(r'\?X-Amz-[^"]*', "", content)

    # -- the dataset -------------------------------------------------------

    def _package_dict(self, content, harvest_object):
        dato = content["dato"]
        category = content.get("categoria") or {}
        versions = content["versiones"]
        portal = cordoba_portal_source(PORTAL)
        package_id = str(uuid.uuid5(uuid.NAMESPACE_URL, dato["links"]["Self"]))

        package_dict = {
            "id": package_id,
            "name": (dato.get("slug") or munge_title_to_name(dato["titulo"]))[:100],
            "title": dato["titulo"],
            "notes": "" if dato.get("descripcion") in EMPTY_NOTES else dato["descripcion"],
            "owner_org": self._owner_org(harvest_object),
            "license_id": self._license(versions),
            "source_portal": portal["value"],
            "source_url": dato["url"],
            "tags": [{"name": t} for t in self._tags(dato.get("tags") or [])],
            "extras": self._extras(dato, category, versions),
            "groups": self._groups(category, portal) if self.config.get("groups", True) else [],
            "resources": self._resources(dato, versions, package_id),
        }
        return package_dict

    def _owner_org(self, harvest_object):
        if self.config.get("single_org"):
            return self.config["single_org"]
        source = toolkit.get_action("package_show")(
            self._context(), {"id": harvest_object.source.id})
        return source.get("owner_org")

    @staticmethod
    def _license(versions):
        titles = set()
        free = set()
        for version in versions:
            for resource in version.get("recursos", []):
                licence = resource.get("licencia") or {}
                if licence.get("licencia"):
                    titles.add(licence["licencia"].upper())
                    free.add(bool(licence.get("licencia_libre")))
        if not titles:
            return "notspecified"
        if any("BY-SA" in t for t in titles):
            return "cc-by-sa"
        if any("CC-BY" in t or "CC BY" in t for t in titles):
            return "cc-by"
        return "other-open" if free == {True} else "notspecified"

    @staticmethod
    def _tags(tags):
        seen = []
        for tag in tags:
            name = TAG_CHARS.sub("", str(tag)).strip()[:100]
            if len(name) >= 2 and name not in seen:
                seen.append(name)
        return seen

    @staticmethod
    def _extras(dato, category, versions):
        extras = []
        if dato.get("periodicidad"):
            extras.append({"key": "periodicidad", "value": dato["periodicidad"]})
        if category.get("nombre"):
            parent = (category.get("categoria_superior") or {}).get("nombre")
            value = "%s / %s" % (parent, category["nombre"]) if parent else category["nombre"]
            extras.append({"key": "categoria", "value": value})
        sources = []
        fuentes = [dato.get("fuente")] + [f for v in versions for f in v.get("fuentes") or []]
        for fuente in fuentes:
            name = fuente.get("nombre") if isinstance(fuente, dict) else fuente
            if name and str(name) not in sources:
                sources.append(str(name))
        if sources:
            extras.append({"key": "fuente", "value": ", ".join(sources)})
        if dato.get("creado"):
            extras.append({"key": "creado_en_origen", "value": dato["creado"][:10]})
        return extras

    def _groups(self, category, portal):
        """The category of the dataset and its parent, as groups."""
        groups = []
        for cat in (category.get("categoria_superior"), category):
            if not cat or not cat.get("slug"):
                continue
            name = ("%s-%s" % (portal["prefix"], cat["slug"]))[:100]
            try:
                group = toolkit.get_action("group_show")(self._context(), {"id": name})
            except toolkit.ObjectNotFound:
                group = toolkit.get_action("group_create")(self._context(), {
                    "name": name,
                    "title": cat.get("nombre") or cat["slug"],
                    "description": "",
                })
                log.info("Group %s created from a category of the portal", name)
            groups.append({"id": group["id"], "name": group["name"]})
        return groups

    # -- the resources -----------------------------------------------------

    def _resources(self, dato, versions, package_id):
        copies = self._existing_copies(package_id)
        resources = []
        for version in versions:
            for remote in version.get("recursos", []):
                resource_id = str(uuid.uuid5(uuid.NAMESPACE_URL, remote["links"]["Self"]))
                resource = {
                    "id": resource_id,
                    "name": self._resource_name(version, remote)[:100],
                    "description": self._resource_description(version),
                    "format": self._format(remote),
                }
                if self._is_file(remote):
                    # the file has no lasting URL on the portal: the link
                    # points at the dataset there until the copy is made,
                    # and source_api is how the harvester asks for it
                    resource["url"] = dato["url"]
                    resource["source_url"] = dato["url"]
                    resource["source_api"] = remote["links"]["Self"]
                    resource["source_last_modified"] = \
                        version.get("ultima_modificacion") or version.get("creado") or ""
                else:
                    resource["url"] = remote.get("url") or dato["url"]
                copy = copies.get(resource_id)
                if copy:
                    resource.update(copy)
                resources.append(resource)
        return resources

    @staticmethod
    def _is_file(remote):
        return remote.get("tipo") == FILE_TYPE and bool(remote.get("url"))

    @staticmethod
    def _resource_name(version, remote):
        title = (remote.get("titulo") or "").strip()
        version_title = (version.get("titulo") or "").strip()
        if not version_title:
            return title or "Recurso"
        if not title or title == version_title:
            return version_title
        return "%s (%s)" % (version_title, title)

    @staticmethod
    def _resource_description(version):
        parts = []
        date = (version.get("creado") or "")[:10]
        if date:
            parts.append("Versión publicada el %s." % date)
        sources = [f.get("nombre") for f in version.get("fuentes") or [] if f.get("nombre")]
        if sources:
            parts.append("Fuente: %s." % ", ".join(sources))
        if version.get("novedades"):
            parts.append(str(version["novedades"]))
        html = version.get("html") or ""
        if html:
            text = re.sub(r"<[^>]+>", " ", html)
            text = re.sub(r"\s+", " ", text).strip()
            if text:
                parts.append(text)
        return "\n\n".join(parts)

    @staticmethod
    def _format(remote):
        url = remote.get("url") or ""
        path = unquote(urlparse(url).path)
        extension = path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""
        if extension in KNOWN_EXTENSIONS:
            return extension.upper()
        icon = (remote.get("icono") or "").lower()
        if icon in ICON_FORMATS:
            return ICON_FORMATS[icon]
        return icon.upper()

    # -- the copies (FileCopyMixin hooks) ----------------------------------

    def _copyable(self, resource):
        return bool(resource.get("source_api"))

    def _download_url(self, resource):
        """A fresh signed URL of the file: the portal's URLs last an hour.
        One resource alone cannot be asked for, so the list of its version
        is."""
        api = resource.get("source_api")
        if not api:
            return None
        list_url = api.rsplit("/", 1)[0]
        for remote in self._get_all(list_url):
            if (remote.get("links") or {}).get("Self") == api:
                return remote.get("url")
        log.warning("Resource %s is no longer on the portal", api)
        return None

    def _needs_copy(self, resource):
        if resource.get("url_type") != "upload" or not resource.get("source_downloaded"):
            return True
        downloaded = resource["source_downloaded"]
        remote = resource.get("source_last_modified")
        if remote and remote > downloaded:
            return True
        recheck = timedelta(days=float(self.config.get("recheck_days", 30)))
        try:
            since = datetime.fromisoformat(downloaded[:19])
        except ValueError:
            return True
        return datetime.utcnow() - since > recheck
