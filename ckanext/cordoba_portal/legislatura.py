"""Harvester of the open data portal of the Legislature of Cordoba.

The portal (https://legislaturacba.gob.ar/portal-de-datos-abiertos/) is a
handful of WordPress pages, one per section (sessions, committees...). On
each page every dataset is a heading followed by a description, the same
table in several formats (CSV, XLSX, XML zipped, JSON), a metadata text
file and "Última actualización: d/m/yyyy". ``legislatura`` reads the pages
through the WordPress REST API and brings each heading here as one CKAN
dataset:

- title (sentence case when the portal shouts it), description, the
  section as a group (``legislatura-sesiones``) and as an extra; topic,
  update frequency and data source from the metadata file;
- every file of the heading is one resource ("Diarios de sesión - CSV");
  links elsewhere (Google Drive, other sites) stay links;
- the files are copied here (``FileCopyMixin``) and fetched again only
  when the portal shows a newer date for the dataset.

Datasets that disappear from the portal are left as they are. Nothing is
requested faster than ``pause`` seconds apart.

Source config (JSON), all keys optional::

    {"single_org": "legislatura",    # organization of the datasets
                                      # (default: the one of the source)
     "pages": ["sesiones", ...],      # slugs of the section pages
                                      # (default: the five of the portal)
     "user_agent": "Mozilla/5.0 ... cbadatos.com.ar",
     "pause": 1,                      # seconds between two requests
     "groups": true,                  # the section as a group
     "copy_files": true,
     "copy_pause": 3,
     "copy_max_mb": 200}
"""
import json
import logging
import re
import time
import uuid
from urllib.parse import unquote, urljoin, urlparse

import lxml.html
import requests

from ckan import model
from ckan.lib.munge import munge_title_to_name
from ckan.plugins import toolkit
from ckanext.harvest.harvesters.base import HarvesterBase
from ckanext.harvest.model import HarvestObject

from ckanext.cordoba_portal.harvester import FileCopyMixin
from ckanext.cordoba_portal.plugin import cordoba_portal_source

log = logging.getLogger(__name__)

PAGES_API = "/wp-json/wp/v2/pages"
PAGE_FIELDS = "id,slug,modified,title,link,content"
UPLOADS = "/wp-content/uploads/"
DATE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{2,4})")
UPDATED = re.compile(r"ltima actualizaci")
LABEL = re.compile(r"^\.?(\w+)$")
# a label that names the file ("Ordenanza Presupuestaria 2026"), as
# opposed to an extension or the CSS of an icon the theme leaked
NAME_LABEL = re.compile(r"^[^.{}<>;][^{}<>;]{1,79}$")
# lines of the metadata file: "2. Frecuencia de actualización: mensual."
META_FIELDS = (("tema", "tema"), ("frecuencia", "frecuencia"), ("fuente", "fuente"))
META_LINE = re.compile(r"^\s*\d+\.\s*([^:]+):\s*(.*)$")


# -- the pages, parsed -----------------------------------------------------

def parse_page(page):
    """The datasets of one section page (a WordPress REST page dict):
    [{title, url, notes, updated, files: [{url, label}], links: [url]}]."""
    doc = lxml.html.fromstring(page["content"]["rendered"])
    host = urlparse(page.get("link", "")).netloc
    datasets = []
    current = None
    for el in doc.iter():
        if el.tag == "h2":
            link = el.find(".//a")
            href = urljoin(page.get("link", ""), link.get("href")) if link is not None else ""
            title = " ".join(el.text_content().split())
            current = {
                "title": title,
                # the dataset's own page; a heading that points elsewhere
                # (a site of another body) has none, that is a link
                "url": href if href and urlparse(href).netloc == host else page.get("link", ""),
                # the id here: the title changes every year (the range of
                # years is in it), the page does not
                "slug": _slug(href, host) or munge_title_to_name(re.sub(r"\(.*?\)", "", title)),
                "notes": "",
                "updated": "",
                "files": [],
                "links": [href] if href and urlparse(href).netloc != host else [],
            }
            datasets.append(current)
        elif current is None:
            continue
        elif el.tag == "p":
            text = " ".join(el.text_content().split())
            if text and not current["notes"]:
                current["notes"] = text
        elif el.tag == "a" and el.get("href"):
            _add_link(current, el)
        elif el.tag == "div" and el.text and UPDATED.search(el.text):
            current["updated"] = iso_date(el.text)
    return datasets


def _slug(href, host):
    """The last part of the path of a page of the portal, else ''."""
    parsed = urlparse(href)
    if parsed.netloc != host or UPLOADS in parsed.path:
        return ""
    return unquote(parsed.path).strip("/").rsplit("/", 1)[-1]


def _add_link(dataset, el):
    # some sites link their files by path
    href = urljoin(dataset["url"], el.get("href").strip())
    if UPLOADS in href:
        if href not in [f["url"] for f in dataset["files"]]:
            label = " ".join(el.text_content().split())
            dataset["files"].append({"url": href, "label": label})
    elif "://" in href and urlparse(href).netloc != urlparse(dataset["url"]).netloc \
            and href not in dataset["links"]:
        # a site elsewhere (Google Drive, another body); the portal's own
        # pages are navigation
        dataset["links"].append(href)


def parse_metadata(text):
    """{tema, frecuencia, fuente} out of the portal's metadata text file
    ("1. Tema: Sesiones." ...); only the fields that are there."""
    found = {}
    for line in text.splitlines():
        match = META_LINE.match(line)
        if not match:
            continue
        key = match.group(1).strip().lower()
        for word, name in META_FIELDS:
            if key.startswith(word) and match.group(2).strip():
                found[name] = match.group(2).strip().rstrip(".")
    return found


def iso_date(text):
    """'Última actualización: 4/9/2026' -> '2026-09-04' ('' if none)."""
    match = DATE.search(text)
    if not match:
        return ""
    day, month, year = (int(g) for g in match.groups())
    if year < 100:
        year += 2000
    return "%04d-%02d-%02d" % (year, month, day)


def sentence_case(title):
    """The portal shouts some titles; 'DIARIOS DE SESIÓN' -> 'Diarios de
    sesión'. Mixed case is left alone."""
    if title.upper() != title:
        return title
    return title[:1].upper() + title[1:].lower()


def file_format(url):
    path = unquote(urlparse(url).path)
    return path.rsplit(".", 1)[-1].upper() if "." in path.rsplit("/", 1)[-1] else ""


class LegislaturaHarvester(FileCopyMixin, HarvesterBase):
    """Also the base of the harvesters of other WordPress sites that list
    their documents the same way (a heading, its files): a subclass names
    its portal and its pages."""

    config = {}
    portal = "legislatura"
    # the section pages of the portal
    default_pages = ["composicion-de-la-camara", "administracion", "comisiones-2",
                     "sesiones", "participacion-ciudadana"]

    def info(self):
        return {
            "name": "legislatura",
            "title": "Legislatura de Córdoba (datos abiertos)",
            "description": (
                "Harvest del portal de datos abiertos de la Legislatura de la "
                "Provincia de Córdoba, copiando los archivos."
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
        for key in ("pause", "copy_pause", "copy_max_mb"):
            if key in config_obj:
                float(config_obj[key])
        if "pages" in config_obj and not isinstance(config_obj["pages"], list):
            raise ValueError("pages must be a list of page slugs")
        if config_obj.get("single_org"):
            try:
                toolkit.get_action("organization_show")(
                    {"ignore_auth": True}, {"id": config_obj["single_org"]})
            except toolkit.ObjectNotFound:
                raise ValueError("single_org: organization %s does not exist"
                                 % config_obj["single_org"])
        return config

    # -- the portal --------------------------------------------------------

    def _get(self, url, params=None):
        time.sleep(float(self.config.get("pause", 1)))
        response = requests.get(url, params=params, headers=self._request_headers(),
                                timeout=(30, 120))
        response.raise_for_status()
        return response

    def _page(self, base, slug):
        """The WordPress page with that slug, or None."""
        pages = self._get(base + PAGES_API, {"slug": slug, "_fields": PAGE_FIELDS}).json()
        return pages[0] if pages else None

    # -- gather: one object per heading of the section pages ---------------

    def gather_stage(self, harvest_job):
        self._set_config(harvest_job.source.config)
        base = harvest_job.source.url.rstrip("/")
        object_ids = []
        for slug in self.config.get("pages", self.default_pages):
            try:
                page = self._page(base, slug)
            except (requests.RequestException, ValueError) as e:
                self._save_gather_error("Could not read the page %s of %s: %s"
                                        % (slug, base, e), harvest_job)
                continue
            if not page:
                self._save_gather_error("No page %s at %s" % (slug, base), harvest_job)
                continue
            section = {"slug": slug, "title": page["title"]["rendered"], "url": page["link"]}
            for dataset in parse_page(page):
                content = {"section": section, "dataset": dataset}
                obj = HarvestObject(guid=dataset["slug"], job=harvest_job,
                                    content=json.dumps(content))
                obj.save()
                object_ids.append(obj.id)
        if not object_ids:
            self._save_gather_error("No datasets found at %s" % base, harvest_job)
            return None
        return object_ids

    # -- fetch: the metadata file ------------------------------------------

    def fetch_stage(self, harvest_object):
        self._set_config(harvest_object.source.config)
        content = json.loads(harvest_object.content)
        content["metadata"] = {}
        meta = self._metadata_file(content["dataset"])
        if meta:
            try:
                # UTF-8 with a BOM, served without a charset
                text = self._get(meta["url"]).content.decode("utf-8-sig", errors="replace")
                content["metadata"] = parse_metadata(text)
            except requests.RequestException as e:
                # the dataset is worth having without its topic and frequency
                log.warning("Could not read the metadata file %s: %s", meta["url"], e)
        harvest_object.content = json.dumps(content)
        harvest_object.save()
        return True

    @staticmethod
    def _metadata_file(dataset):
        for f in dataset["files"]:
            if f["label"].lower().startswith("metadato") and file_format(f["url"]) == "TXT":
                return f
        return None

    # -- import ------------------------------------------------------------

    def import_stage(self, harvest_object):
        self._set_config(harvest_object.source.config)
        content = json.loads(harvest_object.content or "{}")
        if "metadata" not in content:
            self._save_object_error("Object %s was not fetched" % harvest_object.id,
                                    harvest_object, "Import")
            return False

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
        metadata = content.get("metadata") or {}
        portal = cordoba_portal_source(self.portal)
        # a dataset with a page of its own is that page; the ones that
        # share the section page are told apart by their heading
        own = dataset["url"] if dataset["url"] != section["url"] else \
            "%s#%s" % (section["url"], dataset["slug"])
        package_id = str(uuid.uuid5(uuid.NAMESPACE_URL, own))
        title = sentence_case(dataset["title"])
        extras = [{"key": "seccion", "value": section["title"]}]
        extras += [{"key": key, "value": metadata[key]}
                   for key in ("tema", "frecuencia", "fuente") if metadata.get(key)]
        if dataset["updated"]:
            extras.append({"key": "actualizado_en_origen", "value": dataset["updated"]})
        return {
            "id": package_id,
            "name": munge_title_to_name(re.sub(r"\(.*?\)", "", title))[:100],
            "title": title,
            "notes": dataset["notes"],
            "owner_org": self._owner_org(harvest_object),
            "license_id": "notspecified",
            "source_portal": portal["value"],
            "source_url": dataset["url"],
            "extras": extras,
            "groups": self._groups(section, portal) if self.config.get("groups", True) else [],
            "resources": self._resources(dataset, title, package_id),
        }

    def _owner_org(self, harvest_object):
        if self.config.get("single_org"):
            return self.config["single_org"]
        source = toolkit.get_action("package_show")(
            self._context(), {"id": harvest_object.source.id})
        return source.get("owner_org")

    def _groups(self, section, portal):
        """The section of the portal as a group, created the first time."""
        name = ("%s-%s" % (portal["prefix"], munge_title_to_name(section["title"])))[:100]
        try:
            group = toolkit.get_action("group_show")(self._context(), {"id": name})
        except toolkit.ObjectNotFound:
            group = toolkit.get_action("group_create")(self._context(), {
                "name": name, "title": section["title"], "description": ""})
            log.info("Group %s created from a section of the portal", name)
        return [{"id": group["id"], "name": group["name"]}]

    def _resources(self, dataset, title, package_id):
        copies = self._existing_copies(package_id)
        resources = []
        for f in dataset["files"]:
            resource = {
                "id": str(uuid.uuid5(uuid.NAMESPACE_URL, "%s#%s" % (dataset["url"], f["url"]))),
                "name": self._file_name(title, f)[:100],
                "format": file_format(f["url"]),
                "url": f["url"],
                "source_url": f["url"],
                "source_last_modified": dataset["updated"],
            }
            resource.update(copies.get(resource["id"], {}))
            resources.append(resource)
        for url in dataset["links"]:
            resources.append({
                "id": str(uuid.uuid5(uuid.NAMESPACE_URL, "%s#%s" % (dataset["url"], url))),
                "name": ("%s - %s" % (title, urlparse(url).netloc))[:100],
                "format": "HTML",
                "url": url,
            })
        return resources

    @staticmethod
    def _file_name(title, f):
        """'Diarios de sesión - CSV'; the metadata file is 'Metadatos'. A
        label that is just an extension says what the file holds (".xml"
        for a zipped XML), the URL what it is; a label that is a name
        ('Ordenanza Presupuestaria 2026') is the name."""
        label = " ".join(f["label"].split())
        if label.lower().startswith("metadato"):
            return "%s - Metadatos" % title
        what = file_format(f["url"])
        match = LABEL.match(label.lower())
        if not match and NAME_LABEL.match(label):
            what = label
        elif match and what in ("ZIP", "RAR"):
            what = match.group(1).upper()
        return "%s - %s" % (title, what) if what else title
