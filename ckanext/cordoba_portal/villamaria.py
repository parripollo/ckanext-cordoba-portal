"""Harvester of the open data portal of Villa Maria.

The portal (https://datos.villamaria.gob.ar) is a site of its own, not
CKAN, and has no API. What it has: a sitemap with every dataset and the
date it changed, one HTML page per dataset, and the resources of the page
loaded six at a time (an htmx POST with a CSRF cookie). ``villamaria``
reads those pages and brings each dataset here as one CKAN dataset:

- title, description, category, update frequency and the municipal area
  that publishes it; the category becomes a group (``villamaria-salud``),
  the rest are extras;
- every resource of the page is one resource here, named after the dataset
  and the period ("Vacunaciones por vacuna - Agosto 2026"); the external
  links ("Ver", a map viewer) stay links;
- the files are copied here (``FileCopyMixin``): the portal's download
  URLs are stable, so a file is fetched again only when the portal shows
  a newer date for it.

Datasets that disappear from the portal are left as they are. Nothing is
requested faster than ``pause`` seconds apart.

Source config (JSON), all keys optional::

    {"single_org": "villamaria",     # organization of the datasets
                                      # (default: the one of the source)
     "user_agent": "Mozilla/5.0 ... cbadatos.com.ar",
     "pause": 1,                      # seconds between two page requests
     "groups": true,                  # the category as a group
     "copy_files": true,
     "copy_pause": 3,
     "copy_max_mb": 200}
"""
import json
import logging
import re
import time
import uuid
from urllib.parse import urljoin

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

PORTAL = "villamaria"
DATASET_PATH = "/datasets/"
DATE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")


# -- the pages of the portal, parsed ---------------------------------------

def parse_sitemap(xml):
    """[(slug, lastmod)] of the datasets in the sitemap."""
    found = []
    for url, lastmod in re.findall(r"<loc>([^<]+)</loc>(?:<lastmod>([^<]+)</lastmod>)?", xml):
        if DATASET_PATH in url:
            found.append((url.rstrip("/").rsplit("/", 1)[-1], lastmod))
    return found


def parse_dataset(html):
    """The header of a dataset page: title, notes, category, date,
    frequency and area (missing ones are '')."""
    doc = lxml.html.fromstring(html)
    title = doc.findtext(".//h1") or ""
    notes = doc.xpath("string(.//h1/following-sibling::p[1])")
    category = doc.xpath("string(.//h1/preceding::span[@class='uppercase'][1])")
    meta = [t.strip() for t in doc.xpath(".//h1/following-sibling::div[1]//span/text()")]
    meta = [t for t in meta if t]
    return {
        "title": title.strip(),
        "notes": notes.strip(),
        "category": category.strip(),
        "date": iso_date(meta[0]) if meta else "",
        "frequency": meta[1] if len(meta) > 1 else "",
        "area": meta[2] if len(meta) > 2 else "",
    }


def parse_resources(html, base_url):
    """(resources, next page number or None) of a dataset page or of one
    of its htmx fragments. Each resource: title, period, date, format,
    url; ``file`` says whether the url is a download of the portal."""
    doc = lxml.html.fromstring(html)
    lists = doc.xpath("//div[@id='resource_list']")
    if not lists:
        return [], None
    resources = []
    for card in lists[0]:
        link = card.xpath(".//a[@href]")
        if not link:
            continue
        url = urljoin(base_url, link[0].get("href"))
        lines = [t.strip() for t in card.xpath("./div[1]/div/text()")]
        spans = [t.strip() for t in card.xpath(".//span/text()")]
        fmt = card.xpath("string(.//span[@class='uppercase'])").strip()
        date = next((iso_date(s) for s in spans if DATE.match(s)), "")
        resources.append({
            "title": lines[0] if lines else "",
            "period": lines[1] if len(lines) > 1 else "",
            "date": date,
            "format": fmt.upper(),
            "url": url,
            "file": url.startswith(base_url),
        })
    next_page = None
    button = doc.xpath("//*[@id='resource_list_more']//button[@hx-vals]")
    if button:
        try:
            next_page = int(json.loads(button[0].get("hx-vals")).get("page"))
        except (ValueError, TypeError):
            next_page = None
    return resources, next_page


def iso_date(text):
    """'10/09/2026' -> '2026-09-10'; anything else as it came."""
    match = DATE.match(text.strip())
    return "%s-%s-%s" % (match.group(3), match.group(2), match.group(1)) if match else text


class VillaMariaHarvester(FileCopyMixin, HarvesterBase):

    config = {}

    def info(self):
        return {
            "name": "villamaria",
            "title": "Villa María (datos abiertos)",
            "description": (
                "Harvest del portal de datos abiertos de la Municipalidad de "
                "Villa María, copiando los archivos."
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
        if config_obj.get("single_org"):
            try:
                toolkit.get_action("organization_show")(
                    {"ignore_auth": True}, {"id": config_obj["single_org"]})
            except toolkit.ObjectNotFound:
                raise ValueError("single_org: organization %s does not exist"
                                 % config_obj["single_org"])
        return config

    # -- the portal's pages ------------------------------------------------

    def _get(self, url):
        time.sleep(float(self.config.get("pause", 1)))
        response = requests.get(url, headers=self._request_headers(), timeout=(30, 120))
        response.raise_for_status()
        return response

    def _post_page(self, url, page, cookies):
        """The htmx fragment with one more page of resources. Django wants
        the CSRF cookie back in a header, and the page as referer."""
        time.sleep(float(self.config.get("pause", 1)))
        token = next((v for k, v in cookies.items() if k.endswith("csrftoken")), "")
        headers = dict(self._request_headers(), Referer=url, **{
            "X-CSRFToken": token, "HX-Request": "true"})
        response = requests.post(url, data={"page": str(page), "search": ""},
                                 headers=headers, cookies=cookies, timeout=(30, 120))
        response.raise_for_status()
        return response.text

    def _read_dataset(self, url):
        """The dataset at url, with all of its resources."""
        response = self._get(url)
        dataset = parse_dataset(response.text)
        base = url.split(DATASET_PATH, 1)[0]
        resources, next_page = parse_resources(response.text, base)
        seen = {r["url"] for r in resources}
        while next_page:
            more, next_page = parse_resources(self._post_page(url, next_page, response.cookies), base)
            new = [r for r in more if r["url"] not in seen]
            if not new:
                break   # the portal answers the last page again past the end
            resources.extend(new)
            seen.update(r["url"] for r in new)
        dataset["resources"] = resources
        return dataset

    # -- gather: one object per dataset of the sitemap ---------------------

    def gather_stage(self, harvest_job):
        self._set_config(harvest_job.source.config)
        base = harvest_job.source.url.rstrip("/")
        try:
            datasets = parse_sitemap(self._get(base + "/sitemap.xml").text)
        except requests.RequestException as e:
            self._save_gather_error("Could not read the sitemap of %s: %s" % (base, e),
                                    harvest_job)
            return None
        if not datasets:
            self._save_gather_error("No datasets in the sitemap of %s" % base, harvest_job)
            return None

        object_ids = []
        for slug, lastmod in datasets:
            content = {"slug": slug, "url": base + DATASET_PATH + slug, "lastmod": lastmod}
            obj = HarvestObject(guid=slug, job=harvest_job, content=json.dumps(content))
            obj.save()
            object_ids.append(obj.id)
        return object_ids

    # -- fetch: the page and its resources ---------------------------------

    def fetch_stage(self, harvest_object):
        self._set_config(harvest_object.source.config)
        content = json.loads(harvest_object.content)
        try:
            content["dataset"] = self._read_dataset(content["url"])
        except (requests.RequestException, ValueError) as e:
            self._save_object_error("Could not read the page of %s: %s"
                                    % (harvest_object.guid, e), harvest_object, "Fetch")
            return False
        if not content["dataset"]["title"]:
            self._save_object_error("No dataset found at %s" % content["url"],
                                    harvest_object, "Fetch")
            return False
        harvest_object.content = json.dumps(content)
        harvest_object.save()
        return True

    # -- import ------------------------------------------------------------

    def import_stage(self, harvest_object):
        self._set_config(harvest_object.source.config)
        content = json.loads(harvest_object.content or "{}")
        if "dataset" not in content:
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
        portal = cordoba_portal_source(PORTAL)
        package_id = str(uuid.uuid5(uuid.NAMESPACE_URL, content["url"]))
        extras = [
            {"key": key, "value": dataset[field]}
            for key, field in (("periodicidad", "frequency"), ("categoria", "category"),
                               ("area", "area"), ("actualizado_en_origen", "date"))
            if dataset.get(field)
        ]
        return {
            "id": package_id,
            "name": content["slug"][:100],
            "title": dataset["title"],
            "notes": dataset["notes"],
            "owner_org": self._owner_org(harvest_object),
            "license_id": "notspecified",
            "source_portal": portal["value"],
            "source_url": content["url"],
            "extras": extras,
            "groups": self._groups(dataset["category"], portal)
            if self.config.get("groups", True) else [],
            "resources": self._resources(dataset, package_id),
        }

    def _owner_org(self, harvest_object):
        if self.config.get("single_org"):
            return self.config["single_org"]
        source = toolkit.get_action("package_show")(
            self._context(), {"id": harvest_object.source.id})
        return source.get("owner_org")

    def _groups(self, category, portal):
        """The category of the dataset as a group, created the first time."""
        if not category:
            return []
        name = ("%s-%s" % (portal["prefix"], munge_title_to_name(category)))[:100]
        try:
            group = toolkit.get_action("group_show")(self._context(), {"id": name})
        except toolkit.ObjectNotFound:
            group = toolkit.get_action("group_create")(self._context(), {
                "name": name, "title": category, "description": ""})
            log.info("Group %s created from a category of the portal", name)
        return [{"id": group["id"], "name": group["name"]}]

    def _resources(self, dataset, package_id):
        copies = self._existing_copies(package_id)
        resources = []
        for remote in dataset["resources"]:
            resource_id = str(uuid.uuid5(uuid.NAMESPACE_URL, remote["url"]))
            name = " - ".join(t for t in (remote["title"] or dataset["title"], remote["period"]) if t)
            resource = {
                "id": resource_id,
                "name": name[:100],
                "format": remote["format"] or ("" if remote["file"] else "HTML"),
                "url": remote["url"],
            }
            if remote["file"]:
                resource["source_url"] = remote["url"]
                resource["source_last_modified"] = remote["date"]
            copy = copies.get(resource_id)
            if copy:
                resource.update(copy)
            resources.append(resource)
        return resources
