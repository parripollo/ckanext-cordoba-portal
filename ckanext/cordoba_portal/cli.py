"""ckan cordoba-portal: the portal's own commands."""
import json
import os

import click

import ckan.plugins.toolkit as toolkit

HERE = os.path.dirname(__file__)

# The pages of ckanext-pages this portal ships with: (name, title, order).
# The markdown lives in pages/<name>.md; init-pages creates the ones that
# do not exist yet and leaves the existing ones alone (they are edited on
# the site).
PAGES = [
    ("sobre-este-ckan", "Sobre este CKAN y este experimento", "1"),
]

# The harvest sources this portal ships with, each with the organization
# its datasets go to. init-sources creates what is missing and leaves the
# existing ones alone (they are edited on the site). The source is owned by
# the site's own organization, `cbadatos`, when it exists.
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/128.0 Safari/537.36 cbadatos.com.ar")
SOURCES = [
    {
        "organization": {
            "name": "municba",
            "title": "Municipalidad de Córdoba",
            "description": ("Datos publicados por la Municipalidad de Córdoba en su "
                            "portal de Gobierno Abierto."),
            "source_portal": "municba",
            "source_url": "https://gobiernoabierto.cordoba.gob.ar/data/datos-abiertos",
        },
        "source": {
            "name": "municba",
            "title": "Muni CBA (Gobierno Abierto)",
            "url": "https://gobiernoabierto.cordoba.gob.ar",
            "source_type": "municba",
            "frequency": "WEEKLY",
            "notes": "Portal de datos abiertos de la Municipalidad de Córdoba.",
            "config": json.dumps({
                "single_org": "municba", "pause": 1, "copy_pause": 3,
                "copy_max_mb": 1024, "recheck_days": 30, "user_agent": USER_AGENT,
            }),
        },
    },
    {
        "organization": {
            "name": "villamaria",
            "title": "Municipalidad de Villa María",
            "description": ("Datos publicados por la Municipalidad de Villa María en su "
                            "portal de datos abiertos."),
            "source_portal": "villamaria",
            "source_url": "https://datos.villamaria.gob.ar/",
        },
        "source": {
            "name": "villamaria",
            "title": "Villa María (datos abiertos)",
            "url": "https://datos.villamaria.gob.ar",
            "source_type": "villamaria",
            "frequency": "WEEKLY",
            "notes": "Portal de datos abiertos de la Municipalidad de Villa María.",
            "config": json.dumps({
                "single_org": "villamaria", "pause": 1, "copy_pause": 3,
                "user_agent": USER_AGENT,
            }),
        },
    },
    {
        "organization": {
            "name": "legislatura",
            "title": "Legislatura de la Provincia de Córdoba",
            "description": ("Datos publicados por la Legislatura de la Provincia de Córdoba "
                            "en su Portal de Datos Abiertos."),
            "source_portal": "legislatura",
            "source_url": "https://legislaturacba.gob.ar/portal-de-datos-abiertos/",
        },
        "source": {
            "name": "legislatura",
            "title": "Legislatura de Córdoba (datos abiertos)",
            "url": "https://legislaturacba.gob.ar",
            "source_type": "legislatura",
            "frequency": "WEEKLY",
            "notes": "Portal de Datos Abiertos de la Legislatura de la Provincia de Córdoba.",
            "config": json.dumps({
                "single_org": "legislatura", "pause": 1, "copy_pause": 3,
                "user_agent": USER_AGENT,
            }),
        },
    },
    {
        "organization": {
            "name": "idecor",
            "title": "IDECOR - Infraestructura de Datos Espaciales de Córdoba",
            "description": ("Capas publicadas por IDECOR en Mapas Córdoba, el geoportal "
                            "de la Provincia de Córdoba."),
            "source_portal": "idecor",
            "source_url": "https://www.mapascordoba.gob.ar/",
        },
        "source": {
            "name": "idecor",
            "title": "IDECOR (Mapas Córdoba)",
            "url": "https://www.mapascordoba.gob.ar",
            "source_type": "idecor",
            "frequency": "MONTHLY",
            "notes": "Geoportal de la Infraestructura de Datos Espaciales de Córdoba.",
            "config": json.dumps({
                "single_org": "idecor", "pause": 1, "copy_pause": 3,
                "copy_max_mb": 200, "recheck_days": 30, "user_agent": USER_AGENT,
            }),
        },
    },
    {
        "organization": {
            "name": "riocuarto",
            "title": "Municipalidad de Río Cuarto",
            "description": ("Documentos publicados por la Secretaría de Economía de la "
                            "Municipalidad de Río Cuarto en su portal de transparencia."),
            "source_portal": "riocuarto",
            "source_url": "https://economiariocuarto.gob.ar/transparencia",
        },
        "source": {
            "name": "riocuarto",
            "title": "Río Cuarto (transparencia)",
            "url": "https://economiariocuarto.gob.ar",
            "source_type": "riocuarto",
            "frequency": "WEEKLY",
            "notes": "Portal de transparencia de la Secretaría de Economía de Río Cuarto.",
            "config": json.dumps({
                "single_org": "riocuarto", "pause": 1, "copy_pause": 3,
                "recheck_days": 30, "user_agent": USER_AGENT,
            }),
        },
    },
]
OWN_ORGANIZATION = "cbadatos"


@click.group("cordoba-portal", short_help="cbadatos.com.ar commands")
def cordoba_portal():
    pass


@cordoba_portal.command("init-pages", short_help="Create the pages of the portal that are missing")
def init_pages():
    site_user = toolkit.get_action("get_site_user")({"ignore_auth": True}, {})
    context = {"user": site_user["name"], "ignore_auth": True}
    for name, title, order in PAGES:
        existing = toolkit.get_action("ckanext_pages_show")(dict(context), {"page": name})
        if existing:
            click.echo("exists: %s" % name)
            continue
        with open(os.path.join(HERE, "pages", name + ".md"), encoding="utf-8") as f:
            content = f.read()
        toolkit.get_action("ckanext_pages_update")(dict(context), {
            "page": "", "name": name, "title": title, "content": content,
            "order": order, "private": False, "page_type": "page",
        })
        click.echo("created: %s" % name)


@cordoba_portal.command("init-sources",
                        short_help="Create the harvest sources (and their organizations) that are missing")
def init_sources():
    site_user = toolkit.get_action("get_site_user")({"ignore_auth": True}, {})
    context = {"user": site_user["name"], "ignore_auth": True}

    def exists(action, name):
        try:
            return toolkit.get_action(action)(dict(context), {"id": name})
        except toolkit.ObjectNotFound:
            return None

    harvesters = {h["name"] for h in toolkit.get_action("harvesters_info_show")(dict(context), {})}
    for entry in SOURCES:
        org = entry["organization"]
        if entry["source"]["source_type"] not in harvesters:
            # its harvester plugin is not loaded on this instance
            click.echo("skipped: source %s (no %s harvester here)"
                       % (entry["source"]["name"], entry["source"]["source_type"]))
            continue
        if exists("organization_show", org["name"]):
            click.echo("exists: organization %s" % org["name"])
        else:
            toolkit.get_action("organization_create")(dict(context), dict(org))
            click.echo("created: organization %s" % org["name"])

        source = entry["source"]
        if exists("harvest_source_show", source["name"]):
            click.echo("exists: source %s" % source["name"])
            continue
        owner = exists("organization_show", OWN_ORGANIZATION) or \
            exists("organization_show", org["name"])
        toolkit.get_action("harvest_source_create")(
            dict(context), dict(source, owner_org=owner["id"]))
        click.echo("created: source %s" % source["name"])


def get_commands():
    return [cordoba_portal]
