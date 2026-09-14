import ckan.plugins as plugins
import ckan.plugins.toolkit as toolkit


# Where the data comes from. "cbadatos" is this site (own data); the rest
# are the portals we harvest. One place feeds the `source_portal` choices
# of datasets and organizations, the about page and the harvest sources
# (their config names the portal by `value`). `short` is for the facets,
# `prefix` namespaces the organizations created from that portal (see the
# harvester).
OWN_PORTAL = {
    "value": "cbadatos",
    "label": "Producción propia (cbadatos.com.ar)",
    "short": "Producción propia",
    "url": "https://cbadatos.com.ar/",
    "prefix": "",
}
SOURCE_PORTALS = [
    {
        "value": "datosgestionabierta",
        "label": "Portal de Datos Abiertos de Gestión (Gobierno de Córdoba)",
        "short": "Gestión Abierta",
        "url": "https://datosgestionabierta.cba.gov.ar/",
        "prefix": "gestion",
    },
    {
        "value": "datosestadistica",
        "label": "Portal de Datos Estadísticos (Dirección General de Estadística y Censos)",
        "short": "Estadística y Censos",
        "url": "https://datosestadistica.cba.gov.ar/",
        "prefix": "estadistica",
    },
]


def cordoba_portal_sources():
    """The portals we harvest, for the about page and the home page."""
    return SOURCE_PORTALS


def cordoba_portal_source_choices(field=None):
    """Choices of the `source_portal` field (scheming choices_helper)."""
    return [
        {"value": p["value"], "label": p["label"]}
        for p in [OWN_PORTAL] + SOURCE_PORTALS
    ]


def cordoba_portal_source(value):
    """The portal dict for a `source_portal` value, or None."""
    for portal in [OWN_PORTAL] + SOURCE_PORTALS:
        if portal["value"] == value:
            return portal
    return None


def cordoba_portal_counts():
    """Numbers shown on the home page."""
    context = {"ignore_auth": True}
    datasets = toolkit.get_action("package_search")(context, {"rows": 0})["count"]
    organizations = len(toolkit.get_action("organization_list")(context, {}))
    return {"datasets": datasets, "organizations": organizations}


class CordobaPortalPlugin(plugins.SingletonPlugin):
    plugins.implements(plugins.IConfigurer)
    plugins.implements(plugins.ITemplateHelpers)
    plugins.implements(plugins.IFacets, inherit=True)
    plugins.implements(plugins.IPackageController, inherit=True)

    # IConfigurer

    def update_config(self, config_):
        toolkit.add_template_directory(config_, "templates")
        toolkit.add_public_directory(config_, "public")
        toolkit.add_resource("assets", "cordoba_portal")
        # The portal's logo and favicon come with the extension
        # (public/images); CKAN's defaults are always in the config by now,
        # so they are set, not defaulted.
        config_["ckan.site_logo"] = "/images/cba-datos-logo.svg"
        config_["ckan.favicon"] = "/images/favicon.ico"

    # ITemplateHelpers

    def get_helpers(self):
        return {
            "cordoba_portal_counts": cordoba_portal_counts,
            "cordoba_portal_sources": cordoba_portal_sources,
            "cordoba_portal_source_choices": cordoba_portal_source_choices,
            "cordoba_portal_source": cordoba_portal_source,
        }

    # IFacets: the portal of origin, first

    def dataset_facets(self, facets_dict, package_type):
        facets = {"source_portal": toolkit._("Portal de origen")}
        facets.update(facets_dict)
        return facets

    def organization_facets(self, facets_dict, organization_type, package_type):
        return self.dataset_facets(facets_dict, package_type)

    def group_facets(self, facets_dict, group_type, package_type):
        return self.dataset_facets(facets_dict, package_type)

    # IPackageController: facet items of source_portal show the portal name

    def after_dataset_search(self, search_results, search_params):
        facet = search_results.get("search_facets", {}).get("source_portal")
        for item in (facet or {}).get("items", []):
            portal = cordoba_portal_source(item["name"])
            if portal:
                item["display_name"] = portal["short"]
        return search_results
