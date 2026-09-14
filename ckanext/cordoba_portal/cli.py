"""ckan cordoba-portal: the portal's own commands."""
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


def get_commands():
    return [cordoba_portal]
