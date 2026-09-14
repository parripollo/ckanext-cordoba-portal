"""Harvester of the transparency portal of the city of Rio Cuarto.

The portal (https://economiariocuarto.gob.ar/transparencia) is a Next.js
site: every section page has its data as JSON at
``/_next/data/<buildId>/transparencia/<section>.json`` (the build id is in
the HTML of any page and changes with every deploy). Each section holds
lists of items ``{title, category, status, url}``; the files are Google
Drive documents, Drive folders and the PDFs of the sworn statements.
``riocuarto`` brings each list here as one CKAN dataset:

- one dataset per kind of document (budget, budget execution, tax
  collection, public debt, ..., salary scale, official bulletin, sworn
  statements), with the section as a group (``riocuarto-informacion-
  economica-financiera``);
- every item is one resource, named after the item; "Vigente" / "No
  Vigente" goes in the description;
- the files are copied here (``FileCopyMixin``): Drive documents through
  the direct download URL (the portal links the viewer), the PDFs as they
  are. Drive folders cannot be listed without a Google API key: they stay
  links. A copy is asked for again after ``recheck_days``.

Source config (JSON), all keys optional::

    {"single_org": "riocuarto",      # organization of the datasets
                                      # (default: the one of the source)
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
from datetime import datetime, timedelta
from urllib.parse import urlparse

import requests

from ckan import model
from ckan.lib.munge import munge_title_to_name
from ckan.plugins import toolkit
from ckanext.harvest.harvesters.base import HarvesterBase
from ckanext.harvest.model import HarvestObject

from ckanext.cordoba_portal.harvester import FileCopyMixin
from ckanext.cordoba_portal.plugin import cordoba_portal_source

log = logging.getLogger(__name__)

PORTAL = "riocuarto"
PATH = "/transparencia"
BUILD_ID = re.compile(r'"buildId":"([^"]+)"')
DRIVE_FILE = re.compile(r"drive\.google\.com/file/d/([^/?#]+)")
DRIVE_FOLDER = re.compile(r"drive\.google\.com/drive/folders/")
DRIVE_DOWNLOAD = "https://drive.google.com/uc?export=download&id=%s"

# The sections of the portal and the datasets in each: (key in the page's
# JSON, title of the dataset, description). "*" is every list of the page.
SECTIONS = {
    "informacion-economica-financiera": {
        "title": "Información económica y financiera",
        "datasets": [
            ("presupuesto", "Presupuesto municipal",
             "Ordenanzas de presupuesto, notas de elevación y anexos, por ejercicio."),
            ("ejecuciones", "Ejecución presupuestaria",
             "Informes trimestrales de ejecución presupuestaria y de gestión financiera."),
            ("ejercicios", "Cuenta general del ejercicio",
             "Cuenta general de cada ejercicio, por tomo."),
            ("recaudacion", "Informes de recaudación",
             "Informes mensuales de recaudación municipal."),
            ("deudas", "Deuda pública municipal",
             "Informes trimestrales de indicadores de la deuda pública municipal."),
            ("informes", "Calificación de riesgo",
             "Informes de calificación de riesgo del municipio."),
            ("realidad", "Realidad económica local",
             "Informes sobre la economía local: mercado laboral, actividad, precios."),
        ],
    },
    "escala-salarial": {
        "title": "Escala salarial",
        "datasets": [("items", "Escala salarial", "Escala salarial municipal, por mes.")],
    },
    "boletin-oficial": {
        "title": "Boletín Oficial",
        "datasets": [("items", "Boletín Oficial municipal",
                      "Boletín Oficial de la Municipalidad de Río Cuarto, por mes (carpetas de "
                      "Google Drive).")],
    },
    "declaraciones-juradas": {
        "title": "Declaraciones juradas",
        "datasets": [("*", "Declaraciones juradas patrimoniales",
                      "Declaraciones juradas patrimoniales de funcionarios municipales, por "
                      "cargo.")],
    },
}


# -- the portal's JSON, parsed ---------------------------------------------

def parse_section(slug, page_props):
    """The datasets of one section page: [{guid, title, notes, items}]
    following SECTIONS; an item is {title, url, status, category}."""
    section = SECTIONS.get(slug)
    if not section:
        return []
    datasets = []
    for key, title, notes in section["datasets"]:
        if key == "*":
            items = [i for k, v in page_props.items() if isinstance(v, list) for i in v]
        else:
            items = page_props.get(key) or []
        items = [_item(i) for i in items if isinstance(i, dict) and i.get("url")]
        if items:
            datasets.append({"guid": "%s/%s" % (slug, key), "title": title, "notes": notes,
                             "items": items})
    return datasets


def _item(raw):
    return {
        "title": " ".join(str(raw.get("title") or "").split()),
        "url": str(raw["url"]).strip(),
        "status": " ".join(str(raw.get("status") or "").split()),
        "category": " ".join(str(raw.get("category") or "").split()),
    }


def download_url(url):
    """Where the file of an item can be fetched from, or None for what is
    not a file (a Drive folder, a site)."""
    match = DRIVE_FILE.search(url)
    if match:
        return DRIVE_DOWNLOAD % match.group(1)
    if DRIVE_FOLDER.search(url) or "google.com" in urlparse(url).netloc:
        return None
    path = urlparse(url).path
    return url if "." in path.rsplit("/", 1)[-1] else None


def item_key(url):
    """What identifies an item across runs: a Drive file by its id (the
    ``?usp=`` part of the link changes), the rest by URL."""
    match = DRIVE_FILE.search(url)
    return "drive:" + match.group(1) if match else url.split("?")[0]


class RioCuartoHarvester(FileCopyMixin, HarvesterBase):

    config = {}

    def info(self):
        return {
            "name": "riocuarto",
            "title": "Río Cuarto (transparencia)",
            "description": (
                "Harvest del portal de transparencia de la Secretaría de Economía de la "
                "Municipalidad de Río Cuarto, copiando los archivos."
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

    # -- the portal --------------------------------------------------------

    def _get(self, url):
        time.sleep(float(self.config.get("pause", 1)))
        response = requests.get(url, headers=self._request_headers(), timeout=(30, 120))
        response.raise_for_status()
        return response

    def _build_id(self, base):
        match = BUILD_ID.search(self._get(base + PATH).text)
        if not match:
            raise ValueError("no Next.js build id in %s%s" % (base, PATH))
        return match.group(1)

    def _section(self, base, build_id, slug):
        url = "%s/_next/data/%s%s/%s.json" % (base, build_id, PATH, slug)
        return self._get(url).json().get("pageProps") or {}

    # -- gather: one object per dataset of the sections --------------------

    def gather_stage(self, harvest_job):
        self._set_config(harvest_job.source.config)
        base = harvest_job.source.url.rstrip("/")
        try:
            build_id = self._build_id(base)
        except (requests.RequestException, ValueError) as e:
            self._save_gather_error("Could not read %s: %s" % (base, e), harvest_job)
            return None

        object_ids = []
        for slug, section in SECTIONS.items():
            try:
                page_props = self._section(base, build_id, slug)
            except (requests.RequestException, ValueError) as e:
                self._save_gather_error("Could not read the section %s of %s: %s"
                                        % (slug, base, e), harvest_job)
                continue
            for dataset in parse_section(slug, page_props):
                content = {"section": {"slug": slug, "title": section["title"],
                                       "url": "%s%s/%s" % (base, PATH, slug)},
                           "dataset": dataset}
                obj = HarvestObject(guid=dataset["guid"], job=harvest_job,
                                    content=json.dumps(content))
                obj.save()
                object_ids.append(obj.id)
        if not object_ids:
            self._save_gather_error("No datasets found at %s" % base, harvest_job)
            return None
        return object_ids

    # -- fetch: nothing more to ask ----------------------------------------

    def fetch_stage(self, harvest_object):
        return True

    # -- import ------------------------------------------------------------

    def import_stage(self, harvest_object):
        self._set_config(harvest_object.source.config)
        if not harvest_object.content:
            self._save_object_error("Empty content for object %s" % harvest_object.id,
                                    harvest_object, "Import")
            return False
        content = json.loads(harvest_object.content)

        previous = self._previous_object(harvest_object)
        if previous and previous.package_id and previous.content == harvest_object.content:
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

    # -- the dataset -------------------------------------------------------

    def _package_dict(self, content, harvest_object):
        dataset = content["dataset"]
        section = content["section"]
        portal = cordoba_portal_source(PORTAL)
        package_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "%s#%s" % (section["url"], dataset["guid"])))
        current = sum(1 for i in dataset["items"] if i["status"].lower() == "vigente")
        return {
            "id": package_id,
            "name": munge_title_to_name(dataset["title"])[:100],
            "title": dataset["title"],
            "notes": dataset["notes"],
            "owner_org": self._owner_org(harvest_object),
            "license_id": "notspecified",
            "source_portal": portal["value"],
            "source_url": section["url"],
            "extras": [{"key": "seccion", "value": section["title"]},
                       {"key": "documentos_vigentes", "value": str(current)}],
            "groups": self._groups(section, portal) if self.config.get("groups", True) else [],
            "resources": self._resources(dataset, package_id),
        }

    def _owner_org(self, harvest_object):
        if self.config.get("single_org"):
            return self.config["single_org"]
        source = toolkit.get_action("package_show")(
            self._context(), {"id": harvest_object.source.id})
        return source.get("owner_org")

    def _groups(self, section, portal):
        """The section of the portal as a group, created the first time."""
        name = ("%s-%s" % (portal["prefix"], section["slug"]))[:100]
        try:
            group = toolkit.get_action("group_show")(self._context(), {"id": name})
        except toolkit.ObjectNotFound:
            group = toolkit.get_action("group_create")(self._context(), {
                "name": name, "title": section["title"], "description": ""})
            log.info("Group %s created from a section of the portal", name)
        return [{"id": group["id"], "name": group["name"]}]

    def _resources(self, dataset, package_id):
        copies = self._existing_copies(package_id)
        resources = []
        seen = {}
        for item in dataset["items"]:
            source = download_url(item["url"])
            # the same file is listed twice under two titles now and then
            key = item_key(item["url"])
            seen[key] = seen.get(key, 0) + 1
            if seen[key] > 1:
                key = "%s#%d" % (key, seen[key])
            resource = {
                "id": str(uuid.uuid5(uuid.NAMESPACE_URL, "%s#%s" % (dataset["guid"], key))),
                "name": self._resource_name(dataset, item)[:100],
                "description": self._resource_description(item),
                "format": self._format(item["url"], source),
                "url": item["url"],
            }
            if source:
                resource["source_url"] = source
                resource.update(copies.get(resource["id"], {}))
            resources.append(resource)
        return resources

    @staticmethod
    def _resource_name(dataset, item):
        """'Presupuesto 2026'; a sworn statement is 'Intendente Municipal -
        Name' (the category there is the post)."""
        if dataset["guid"].startswith("declaraciones-juradas/") and item["category"]:
            return "%s - %s" % (item["category"], item["title"])
        return item["title"] or item["url"]

    @staticmethod
    def _resource_description(item):
        status = item["status"]
        if not status:
            return ""
        return "Vigente." if status.lower() == "vigente" else "No vigente (versión anterior)."

    @staticmethod
    def _format(url, source):
        if DRIVE_FOLDER.search(url):
            return "Google Drive"
        if not source:
            return "HTML"
        path = urlparse(source).path
        tail = path.rsplit("/", 1)[-1]
        return tail.rsplit(".", 1)[-1].upper() if "." in tail else ""

    # -- the copies (FileCopyMixin hooks) ----------------------------------

    def _needs_copy(self, resource):
        """The portal has no dates: a copy is asked for again after
        ``recheck_days`` and replaced only if its content changed."""
        if resource.get("url_type") != "upload" or not resource.get("source_downloaded"):
            return True
        recheck = timedelta(days=float(self.config.get("recheck_days", 30)))
        try:
            since = datetime.fromisoformat(resource["source_downloaded"][:19])
        except ValueError:
            return True
        return datetime.utcnow() - since > recheck
