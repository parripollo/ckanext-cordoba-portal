"""Harvester of the transparency section of Villa Allende.

The city's site (https://www.villaallende.gov.ar/transparencia/) is
WordPress; the bulletins, the tenders and the public purchase calls are
custom post types with their fields (ACF) open in the REST API:
``wp-json/wp/v2/va_boletin``, ``va_licitacion`` and ``compra-publica``,
the files being media ids resolved through ``wp-json/wp/v2/media``.
``villaallende`` brings each post type here as one CKAN dataset:

- "Boletín Oficial" (one resource per bulletin, named by number and
  period), "Licitaciones y compulsas" (one per document of every tender,
  with code, kind, state, amount and date in the description) and
  "Compras públicas" (one per call);
- the files are copied here (``FileCopyMixin``) and fetched again when
  the site shows a newer modification date.

Source config (JSON), all keys optional::

    {"single_org": "villaallende",   # organization of the datasets
                                      # (default: the one of the source)
     "user_agent": "Mozilla/5.0 ... cbadatos.com.ar",
     "pause": 1,
     "copy_files": true,
     "copy_pause": 3,
     "copy_max_mb": 200}
"""
import json
import logging
import re
import time
import uuid
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

PORTAL = "villaallende"
API = "/wp-json/wp/v2/"
PAGE_SIZE = 100
POST_FIELDS = "id,slug,date,modified,title,link,acf"
MEDIA_FIELDS = "id,source_url,mime_type,modified"
SECTION_URL = "/transparencia/"
# the post types, as datasets
DATASETS = [
    ("va_boletin", "Boletín Oficial municipal",
     "Boletines oficiales de la Municipalidad de Villa Allende, por número y período."),
    ("va_licitacion", "Licitaciones y compulsas",
     "Llamados a licitación, compulsas abreviadas y concesiones, con sus pliegos y "
     "notas aclaratorias."),
    ("compra-publica", "Compras públicas",
     "Llamados a compras públicas de la Municipalidad de Villa Allende."),
]
STATES = {"call": "llamado vigente", "open": "en proceso", "closed": "adjudicada",
          "finished": "finalizada", "voided": "no adjudicada"}
KINDS = {"compulsa": "Compulsa abreviada", "licitacion": "Licitación pública",
         "concesion": "Concesión"}
COMPACT_DATE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")


# -- the posts, as resources -----------------------------------------------

def iso_date(text):
    """'20260908' or '2026-05-20 00:00:00' -> '2026-09-08'; '' if none."""
    text = (text or "").strip()
    match = COMPACT_DATE.match(text)
    if match:
        return "-".join(match.groups())
    return text[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", text) else ""


def _title(post):
    return " ".join(re.sub(r"<[^>]+>", "", (post.get("title") or {}).get("rendered") or "").split())


def bulletin_resources(posts, media):
    """One resource per bulletin: the PDF, named by number and period."""
    resources = []
    for post in posts:
        acf = post.get("acf") or {}
        file = media.get(acf.get("va_boletin_pdf"))
        if not file:
            continue
        number = str(acf.get("va_boletin_numero") or "").strip()
        period = str(acf.get("va_boletin_periodo") or "").strip()
        name = "Boletín N° %s" % number if number else _title(post)
        resources.append(_resource(
            key="boletin:%s" % post["id"],
            name="%s (%s)" % (name, period) if period else name,
            description="Boletín Oficial del %s." % period if period else "",
            url=file["source_url"],
            modified=file.get("modified") or post.get("modified") or "",
            date=iso_date(acf.get("va_boletin_fecha")) or post.get("date", "")[:10],
        ))
    return resources


def tender_resources(posts, media):
    """One resource per document of every tender."""
    resources = []
    for post in posts:
        acf = post.get("acf") or {}
        title = _title(post)
        parts = []
        if acf.get("va_lic_codigo"):
            parts.append("Expediente %s." % acf["va_lic_codigo"])
        kind = KINDS.get(acf.get("va_lic_tipo"), acf.get("va_lic_tipo"))
        if kind:
            parts.append("%s." % kind)
        state = STATES.get(acf.get("va_lic_estado"), acf.get("va_lic_estado"))
        if state:
            parts.append("Estado: %s." % state)
        if acf.get("va_lic_monto"):
            parts.append("Monto: $%s." % format(int(acf["va_lic_monto"]), ",d").replace(",", "."))
        date = iso_date(acf.get("va_lic_fecha"))
        if date:
            parts.append("Fecha de apertura: %s." % date)
        for i, doc in enumerate(acf.get("va_lic_documentos") or []):
            file = media.get(doc.get("url")) if isinstance(doc.get("url"), int) else None
            url = file["source_url"] if file else (doc.get("url") if isinstance(doc.get("url"), str) else None)
            if not url:
                continue
            label = str(doc.get("label") or "").strip()
            resources.append(_resource(
                key="licitacion:%s:%s" % (post["id"], i),
                name="%s - %s" % (title, label) if label else title,
                description=" ".join(parts),
                url=url,
                modified=(file or {}).get("modified") or post.get("modified") or "",
                date=date or post.get("date", "")[:10],
            ))
    return resources


def purchase_resources(posts, media):
    """One resource per public purchase call: its PDF."""
    resources = []
    for post in posts:
        acf = post.get("acf") or {}
        url = str(acf.get("url_licitaciones") or "").strip()
        if not url:
            continue
        resources.append(_resource(
            key="compra:%s" % post["id"],
            name=" ".join(str(acf.get("compra_titulo") or "").split()) or _title(post),
            description=" ".join(str(acf.get("descripcion_compra") or "").split()),
            url=url,
            modified=post.get("modified") or "",
            date=iso_date(acf.get("compra_fecha")) or post.get("date", "")[:10],
        ))
    return resources


def _resource(key, name, description, url, modified, date):
    return {"key": key, "name": name, "description": description, "url": url,
            "modified": modified[:19], "date": date}


BUILDERS = {"va_boletin": bulletin_resources, "va_licitacion": tender_resources,
            "compra-publica": purchase_resources}


def media_ids(posts):
    """The media ids the posts point at."""
    ids = set()
    for post in posts:
        acf = post.get("acf") or {}
        if isinstance(acf.get("va_boletin_pdf"), int):
            ids.add(acf["va_boletin_pdf"])
        for doc in acf.get("va_lic_documentos") or []:
            if isinstance(doc.get("url"), int):
                ids.add(doc["url"])
    return ids


def file_format(url):
    path = unquote(urlparse(url).path)
    return path.rsplit(".", 1)[-1].upper() if "." in path.rsplit("/", 1)[-1] else ""


class VillaAllendeHarvester(FileCopyMixin, HarvesterBase):

    config = {}

    def info(self):
        return {
            "name": "villaallende",
            "title": "Villa Allende (transparencia)",
            "description": (
                "Harvest de la sección de transparencia de la Municipalidad de Villa "
                "Allende (boletines, licitaciones, compras), copiando los archivos."
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

    # -- the site's REST API -----------------------------------------------

    def _get(self, url, params):
        time.sleep(float(self.config.get("pause", 1)))
        response = requests.get(url, params=params, headers=self._request_headers(),
                                timeout=(30, 120))
        response.raise_for_status()
        return response

    def _all(self, url, params):
        """Every item of a paginated WordPress listing."""
        items = []
        page = 1
        while True:
            response = self._get(url, dict(params, per_page=PAGE_SIZE, page=page))
            batch = response.json()
            items.extend(batch)
            pages = int(response.headers.get("X-WP-TotalPages") or 1)
            if page >= pages or not batch:
                return items
            page += 1

    def _media(self, base, ids):
        """{id: {source_url, mime_type, modified}} for those media ids."""
        found = {}
        ids = sorted(ids)
        for start in range(0, len(ids), PAGE_SIZE):
            chunk = ids[start:start + PAGE_SIZE]
            response = self._get(base + API + "media", {
                "include": ",".join(str(i) for i in chunk), "per_page": PAGE_SIZE,
                "_fields": MEDIA_FIELDS})
            for item in response.json():
                found[item["id"]] = item
        return found

    # -- gather: one object per post type ----------------------------------

    def gather_stage(self, harvest_job):
        self._set_config(harvest_job.source.config)
        base = harvest_job.source.url.rstrip("/")
        object_ids = []
        for post_type, title, notes in DATASETS:
            try:
                posts = self._all(base + API + post_type, {"_fields": POST_FIELDS})
                media = self._media(base, media_ids(posts))
            except (requests.RequestException, ValueError) as e:
                self._save_gather_error("Could not read %s of %s: %s" % (post_type, base, e),
                                        harvest_job)
                continue
            resources = BUILDERS[post_type](posts, media)
            if not resources:
                log.info("No %s at %s", post_type, base)
                continue
            content = {"post_type": post_type, "title": title, "notes": notes,
                       "url": base + SECTION_URL, "resources": resources}
            obj = HarvestObject(guid=post_type, job=harvest_job, content=json.dumps(content))
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
            log.info("Dataset %s did not change on the site", harvest_object.guid)
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
        portal = cordoba_portal_source(PORTAL)
        package_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "%s#%s" % (content["url"], content["post_type"])))
        latest = max((r["date"] for r in content["resources"] if r["date"]), default="")
        extras = [{"key": "documentos", "value": str(len(content["resources"]))}]
        if latest:
            extras.append({"key": "ultimo_documento", "value": latest})
        return {
            "id": package_id,
            "name": munge_title_to_name(content["title"])[:100],
            "title": content["title"],
            "notes": content["notes"],
            "owner_org": self._owner_org(harvest_object),
            "license_id": "notspecified",
            "source_portal": portal["value"],
            "source_url": content["url"],
            "extras": extras,
            "resources": self._resources(content, package_id),
        }

    def _owner_org(self, harvest_object):
        if self.config.get("single_org"):
            return self.config["single_org"]
        source = toolkit.get_action("package_show")(
            self._context(), {"id": harvest_object.source.id})
        return source.get("owner_org")

    def _resources(self, content, package_id):
        copies = self._existing_copies(package_id)
        resources = []
        for item in content["resources"]:
            fmt = file_format(item["url"])
            resource = {
                "id": str(uuid.uuid5(uuid.NAMESPACE_URL, "%s#%s" % (content["post_type"], item["key"]))),
                "name": item["name"][:100],
                "description": item["description"],
                "format": fmt or "HTML",
                "url": item["url"],
            }
            if fmt:
                # a file; a purchase call may point at a page of the site
                # instead, that is a link
                resource["source_url"] = item["url"]
                resource["source_last_modified"] = item["modified"]
                resource.update(copies.get(resource["id"], {}))
            resources.append(resource)
        return resources
