"""A CKAN harvester that keeps the provenance of what it brings.

``ckan_with_files`` is ckanext-harvest's ``ckan`` harvester plus:

- every dataset gets ``source_portal`` (from the source config) and
  ``source_url`` (the dataset on the remote portal), and every resource
  ``source_url`` (the original file) and ``source_last_modified``;
- the remote organizations become local ones with a prefix per portal
  (``gestion-o-salud``), so two portals never share an organization; or,
  with ``single_org``, everything goes to one organization and the remote
  "organizations" become groups (portals that use them as themes);
- remote extras that clash with a field of our schema are dropped instead
  of failing the whole dataset;
- the files hosted by the remote portal are copied here, one at a time with
  a pause in between, and only when they changed (remote ``last_modified``,
  then a conditional request with the ETag, then the SHA-256 of the
  content); the resource becomes an upload of ours and keeps the original
  in ``source_url``. This is what makes the site a backup.

Source config (JSON), on top of the ``ckan`` harvester's own keys::

    {"source_portal": "datosgestionabierta",
     "user_agent": "Mozilla/5.0 ... cbadatos.com.ar",
     "remote_groups": "create",
     "copy_files": true,          # default
     "copy_pause": 3,             # seconds between two downloads (default)
     "copy_max_mb": 200}          # bigger files stay as links (default)

    {"source_portal": "datosestadistica",
     "single_org": "estadistica-dgeyc",
     "remote_orgs_as_groups": true,
     "user_agent": "Mozilla/5.0 ... cbadatos.com.ar"}
"""
import hashlib
import io
import json
import logging
import os
import tempfile
import time
from datetime import datetime
from urllib.parse import urlparse

import requests
from werkzeug.datastructures import FileStorage

from ckan import model
from ckan.plugins import toolkit
from ckanext.harvest.harvesters.ckanharvester import CKANHarvester, RemoteResourceError

from ckanext.cordoba_portal.plugin import cordoba_portal_source, OWN_PORTAL

log = logging.getLogger(__name__)

# What a copied resource remembers about its copy; kept across the updates
# the harvester makes with the remote dict
COPY_FIELDS = ("url", "url_type", "hash", "size", "mimetype", "source_etag",
               "source_downloaded")

# Keys a remote extra may not use: they are fields of the dataset itself
# and CKAN rejects the dataset ("There is a schema field with the same
# name"). Our scheming fields are added at runtime.
CORE_FIELDS = {
    "id", "name", "title", "notes", "url", "version", "state", "type",
    "author", "author_email", "maintainer", "maintainer_email",
    "license_id", "owner_org", "private", "metadata_created",
    "metadata_modified", "creator_user_id", "tags", "groups", "resources",
    "extras", "relationships_as_object", "relationships_as_subject",
    "organization", "num_resources", "num_tags", "isopen", "license_title",
    "license_url", "tracking_summary", "plugin_data",
}


class CordobaCKANHarvester(CKANHarvester):

    def info(self):
        return {
            "name": "ckan_with_files",
            "title": "CKAN (con procedencia)",
            "description": (
                "Cosecha un portal CKAN guardando de qué portal viene cada "
                "dato, con organizaciones propias por portal."
            ),
            "form_config_interface": "Text",
        }

    # -- config ------------------------------------------------------------

    def validate_config(self, config):
        config = super().validate_config(config)
        if not config:
            raise ValueError("source_portal is required in the config")
        config_obj = json.loads(config)

        portal = cordoba_portal_source(config_obj.get("source_portal"))
        if not portal or portal["value"] == OWN_PORTAL["value"]:
            raise ValueError(
                "source_portal must be one of the portals of "
                "ckanext.cordoba_portal.plugin.SOURCE_PORTALS"
            )
        single_org = config_obj.get("single_org")
        if single_org:
            try:
                toolkit.get_action("organization_show")(
                    {"ignore_auth": True}, {"id": single_org})
            except toolkit.ObjectNotFound:
                raise ValueError("single_org: organization %s does not exist" % single_org)
        if config_obj.get("remote_orgs_as_groups") and not single_org:
            raise ValueError("remote_orgs_as_groups needs single_org")
        return config

    # -- the remote dataset, before it is created or updated ---------------

    def modify_package_dict(self, package_dict, harvest_object):
        source = harvest_object.source
        base_url = source.url.rstrip("/")
        portal = cordoba_portal_source(self.config["source_portal"])

        package_dict["source_portal"] = portal["value"]
        package_dict["source_url"] = "%s/dataset/%s" % (base_url, harvest_object.guid)

        remote_org = package_dict.get("organization") or {}
        if self.config.get("single_org"):
            package_dict["owner_org"] = self.config["single_org"]
            if self.config.get("remote_orgs_as_groups") and remote_org.get("name"):
                self._add_group(package_dict, remote_org)
        elif remote_org.get("name"):
            package_dict["owner_org"] = self._local_organization(
                remote_org, portal, base_url)

        package_dict["extras"] = self._clean_extras(package_dict.get("extras", []))

        copies = self._existing_copies(package_dict.get("id"))
        for resource in package_dict.get("resources", []):
            if resource.get("url", "").startswith(base_url + "/"):
                resource["source_url"] = resource["url"]
            if resource.get("last_modified"):
                resource["source_last_modified"] = resource["last_modified"]
            # the remote dict would turn our copy back into a link
            copy = copies.get(resource.get("id"))
            if copy:
                resource.update(copy)

        return package_dict

    def import_stage(self, harvest_object):
        result = super().import_stage(harvest_object)
        # also for a dataset the base left as is ("unchanged"): a copy that
        # failed last time gets another chance
        if result in (True, "unchanged") and self.config.get("copy_files", True):
            package_id = harvest_object.package_id
            if not package_id and harvest_object.content:
                package_id = json.loads(harvest_object.content).get("id")
            try:
                if package_id:
                    self._copy_files(package_id)
            except Exception as e:
                # the dataset is in; the files are tried again next time
                log.exception("Copying the files of %s failed: %s", package_id, e)
        return result

    # -- the files ----------------------------------------------------------

    def _existing_copies(self, package_id):
        """{resource id: the copy fields} of the resources of the local
        dataset that are already copies of ours, or {}."""
        if not package_id:
            return {}
        try:
            existing = toolkit.get_action("package_show")(
                dict(self._context(), use_cache=False), {"id": package_id})
        except toolkit.ObjectNotFound:
            return {}
        return {
            r["id"]: {k: r[k] for k in COPY_FIELDS if r.get(k) is not None}
            for r in existing.get("resources", [])
            if r.get("url_type") == "upload" and r.get("source_url")
        }

    def _copy_files(self, package_id):
        """Copy the files of a dataset that changed since our copy."""
        package = toolkit.get_action("package_show")(
            dict(self._context(), use_cache=False), {"id": package_id})
        pause = float(self.config.get("copy_pause", 3))
        for resource in package.get("resources", []):
            if not resource.get("source_url"):
                continue
            if not self._needs_copy(resource):
                continue
            self._copy_file(resource)
            time.sleep(pause)

    @staticmethod
    def _needs_copy(resource):
        """A file is fetched when we have no copy, or when the portal says
        it changed after we copied it."""
        if resource.get("url_type") != "upload" or not resource.get("source_downloaded"):
            return True
        remote = resource.get("source_last_modified")
        return bool(remote and remote > resource["source_downloaded"])

    def _copy_file(self, resource):
        max_bytes = int(float(self.config.get("copy_max_mb", 200)) * 1024 * 1024)
        headers = {}
        if self.config.get("user_agent"):
            headers["User-Agent"] = str(self.config["user_agent"])
        if resource.get("source_etag") and resource.get("url_type") == "upload":
            headers["If-None-Match"] = resource["source_etag"]
        url = resource["source_url"]
        now = datetime.utcnow().replace(microsecond=0).isoformat()

        try:
            response = self._get_with_retry(url, headers)
        except requests.RequestException as e:
            log.warning("Could not fetch %s: %s", url, e)
            return
        with response:
            if response.status_code == 304:
                log.info("Unchanged (304): %s", url)
                self._patch(resource, {"source_downloaded": now})
                return
            if response.status_code != 200:
                log.warning("Not copying %s: HTTP %s", url, response.status_code)
                return
            length = int(response.headers.get("Content-Length") or 0)
            if length > max_bytes:
                log.warning("Not copying %s: %s bytes", url, length)
                return
            digest = hashlib.sha256()
            size = 0
            tmp = tempfile.TemporaryFile()
            for chunk in response.iter_content(chunk_size=1024 * 64):
                size += len(chunk)
                if size > max_bytes:
                    log.warning("Not copying %s: more than %s bytes", url, max_bytes)
                    tmp.close()
                    return
                digest.update(chunk)
                tmp.write(chunk)
            etag = response.headers.get("ETag") or ""
            content_type = response.headers.get("Content-Type") or ""

        sha256 = digest.hexdigest()
        if resource.get("url_type") == "upload" and resource.get("hash") == sha256:
            log.info("Same content, not replaced: %s", url)
            tmp.close()
            self._patch(resource, {"source_downloaded": now, "source_etag": etag})
            return

        tmp.seek(0)
        filename = os.path.basename(urlparse(url).path) or resource["id"]
        upload = FileStorage(tmp, filename=filename, content_type=content_type.split(";")[0])
        self._patch(resource, {
            "upload": upload,
            "url": filename,
            "hash": sha256,
            "size": size,
            "mimetype": content_type.split(";")[0] or None,
            "source_etag": etag,
            "source_downloaded": now,
        })
        tmp.close()
        log.info("Copied %s (%s bytes)", url, size)

    def _get_with_retry(self, url, headers, attempts=2):
        """GET, streaming; a slow origin gets a long read timeout and one
        more try (the file is re-read from the start)."""
        for attempt in range(1, attempts + 1):
            try:
                return requests.get(url, headers=headers, timeout=(30, 300), stream=True)
            except requests.RequestException as e:
                if attempt == attempts:
                    raise
                log.warning("Retrying %s after: %s", url, e)
                time.sleep(5)

    def _patch(self, resource, changes):
        data = dict(changes, id=resource["id"])
        toolkit.get_action("resource_patch")(self._context(), data)

    # -- organizations and groups -----------------------------------------

    def _context(self):
        """A fresh context for one action call, as the harvest user."""
        return {"model": model, "session": model.Session,
                "user": self._get_user_name()}

    def _local_organization(self, remote_org, portal, base_url):
        """The id of the local organization for a remote one: same title,
        name prefixed by the portal; created the first time it is seen."""
        name = "%s-%s" % (portal["prefix"], remote_org["name"])
        name = name[:100]
        try:
            return toolkit.get_action("organization_show")(
                self._context(), {"id": name})["id"]
        except toolkit.ObjectNotFound:
            pass

        org_dict = {
            "name": name,
            "title": remote_org.get("title") or remote_org["name"],
            "description": remote_org.get("description") or "",
            "source_portal": portal["value"],
            "source_url": "%s/organization/%s" % (base_url, remote_org["name"]),
        }
        # the logo: package_show gives a bare file name for uploaded ones,
        # organization_show gives the full URL. It is copied here (the
        # portals only serve it to browsers, and we are a backup).
        try:
            full = self._get_organization(base_url, remote_org["name"])
            image = full.get("image_display_url") or full.get("image_url") or ""
            if not org_dict["description"]:
                org_dict["description"] = full.get("description") or ""
        except RemoteResourceError:
            image = remote_org.get("image_url") or ""
        if image and "://" in image:
            logo = self._fetch_file(image)
            if logo:
                org_dict["image_upload"] = logo
            else:
                org_dict["image_url"] = image

        try:
            created = toolkit.get_action("organization_create")(self._context(), org_dict)
        except toolkit.ValidationError as e:
            if "image_upload" not in e.error_dict:
                raise
            # a logo CKAN does not accept as an upload (SVG, by default):
            # the organization is created with a link to it instead
            log.warning("Logo of %s not copied (%s), linking it", name, e.error_dict["image_upload"])
            org_dict.pop("image_upload", None)  # the uploader may have taken it
            org_dict["image_url"] = image
            created = toolkit.get_action("organization_create")(self._context(), org_dict)
        log.info("Organization %s created for %s", name, portal["value"])
        return created["id"]

    MAX_LOGO_BYTES = 5 * 1024 * 1024

    def _fetch_file(self, url, max_bytes=MAX_LOGO_BYTES):
        """The file at url as an upload for CKAN, or None if it cannot be
        fetched. Same User-Agent as the API calls (the portals need it)."""
        headers = {}
        if self.config.get("user_agent"):
            headers["User-Agent"] = str(self.config["user_agent"])
        try:
            response = requests.get(url, headers=headers, timeout=60)
            response.raise_for_status()
        except requests.RequestException as e:
            log.warning("Could not fetch %s: %s", url, e)
            return None
        if len(response.content) > max_bytes:
            log.warning("Not copying %s: %s bytes", url, len(response.content))
            return None
        filename = os.path.basename(urlparse(url).path) or "file"
        return FileStorage(io.BytesIO(response.content), filename=filename,
                           content_type=response.headers.get("Content-Type"))

    def _add_group(self, package_dict, remote_org):
        """A remote "organization" that is really a theme becomes a group."""
        name = remote_org["name"]
        try:
            group = toolkit.get_action("group_show")(self._context(), {"id": name})
        except toolkit.ObjectNotFound:
            group = toolkit.get_action("group_create")(self._context(), {
                "name": name,
                "title": remote_org.get("title") or name,
                "description": remote_org.get("description") or "",
            })
            log.info("Group %s created from a remote organization", name)
        groups = package_dict.setdefault("groups", [])
        if not any(g.get("id") == group["id"] or g.get("name") == name for g in groups):
            groups.append({"id": group["id"], "name": name})

    # -- extras -----------------------------------------------------------

    def _clean_extras(self, extras):
        """Remote extras minus the ones named like a field of ours."""
        fields = set(CORE_FIELDS)
        schema = toolkit.h.scheming_get_dataset_schema("dataset") or {}
        fields.update(f["field_name"] for f in schema.get("dataset_fields", []))
        kept = []
        for extra in extras:
            if extra.get("key") in fields:
                log.info("Dropping remote extra %r: it is a field here", extra["key"])
                continue
            kept.append(extra)
        return kept
