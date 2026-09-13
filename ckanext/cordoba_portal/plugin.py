import ckan.plugins as plugins
import ckan.plugins.toolkit as toolkit


# The portals whose data this site gathers. The same list will feed the
# `source_portal` field of the datasets (scheming choices) and the harvest
# sources, so a portal is added here and nowhere else.
SOURCE_PORTALS = [
    {
        "value": "datosgestionabierta",
        "label": "Portal de Datos Abiertos de Gestión (Gobierno de Córdoba)",
        "url": "https://datosgestionabierta.cba.gov.ar/",
    },
    {
        "value": "datosestadistica",
        "label": "Portal de Datos Estadísticos (Dirección General de Estadística y Censos)",
        "url": "https://datosestadistica.cba.gov.ar/",
    },
]


def cordoba_portal_sources():
    """The source portals, for the about page and the home page."""
    return SOURCE_PORTALS


def cordoba_portal_counts():
    """Numbers shown on the home page."""
    context = {"ignore_auth": True}
    datasets = toolkit.get_action("package_search")(context, {"rows": 0})["count"]
    organizations = len(toolkit.get_action("organization_list")(context, {}))
    return {"datasets": datasets, "organizations": organizations}


class CordobaPortalPlugin(plugins.SingletonPlugin):
    plugins.implements(plugins.IConfigurer)
    plugins.implements(plugins.ITemplateHelpers)

    # IConfigurer

    def update_config(self, config_):
        toolkit.add_template_directory(config_, "templates")
        toolkit.add_public_directory(config_, "public")
        toolkit.add_resource("assets", "cordoba_portal")

    # ITemplateHelpers

    def get_helpers(self):
        return {
            "cordoba_portal_counts": cordoba_portal_counts,
            "cordoba_portal_sources": cordoba_portal_sources,
        }
