"""Extra pages of the portal: the sitemap."""
from xml.sax.saxutils import escape

from flask import Blueprint, make_response

import ckan.plugins as plugins
import ckan.plugins.toolkit as toolkit

blueprint = Blueprint("cordoba_portal", __name__)


def _urls():
    """(loc, lastmod or None) for everything a search engine should index."""
    url = toolkit.url_for
    yield url("home.index", _external=True), None
    yield url("home.about", _external=True), None
    context = {"ignore_auth": True}
    start, rows = 0, 1000
    while True:
        found = toolkit.get_action("package_search")(context, {
            "rows": rows, "start": start, "fl": "name,metadata_modified",
            "sort": "metadata_modified desc"})
        for d in found["results"]:
            yield url("dataset.read", id=d["name"], _external=True), d.get("metadata_modified")
        start += rows
        if start >= found["count"]:
            break
    for group_type, endpoint in (("organization", "organization.read"), ("group", "group.read")):
        action = "organization_list" if group_type == "organization" else "group_list"
        for name in toolkit.get_action(action)(context, {}):
            yield url(endpoint, id=name, _external=True), None
    if plugins.plugin_loaded("pages"):
        for page in toolkit.get_action("ckanext_pages_list")(context, {"private": False}):
            yield url("pages.show", page=page["name"], _external=True), page.get("publish_date")


@blueprint.route("/sitemap.xml")
def sitemap():
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, lastmod in _urls():
        lines.append("<url><loc>%s</loc>%s</url>" % (
            escape(loc), "<lastmod>%s</lastmod>" % lastmod[:10] if lastmod else ""))
    lines.append("</urlset>")
    response = make_response("\n".join(lines))
    response.headers["Content-Type"] = "application/xml; charset=utf-8"
    return response
