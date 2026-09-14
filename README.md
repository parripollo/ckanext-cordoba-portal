[![Tests](https://github.com/parripollo/ckanext-cordoba-portal/workflows/Tests/badge.svg?branch=main)](https://github.com/parripollo/ckanext-cordoba-portal/actions)

# ckanext-cordoba-portal

CKAN extension for the Cordoba open data portal.

Generated with `ckan generate extension`. Still empty: it will grow with
whatever the portal needs (schema, theme, custom pages, etc).

## Requirements

Runs on the PostgreSQL-only CKAN: https://github.com/parripollo/ckanito
(no Solr, no Redis). Python 3.10+.

## Installation

    git clone https://github.com/parripollo/ckanext-cordoba-portal.git
    cd ckanext-cordoba-portal
    pip install -e .
    pip install -r requirements.txt

Add the plugins to `ckan.plugins`, with `cordoba_portal` **before** the
scheming ones (it overrides some of their templates, and CKAN gives the
plugin listed first the highest template priority):

    ckan.plugins = ... cordoba_portal scheming_datasets scheming_organizations ...
    scheming.dataset_schemas = ckanext.cordoba_portal:schemas/dataset.yaml
    scheming.organization_schemas = ckanext.cordoba_portal:schemas/organization.yaml

Then restart CKAN.

## Config settings

Only the scheming ones above. The portals the site gathers data from are
listed in `ckanext/cordoba_portal/plugin.py` (`SOURCE_PORTALS`): that list
feeds the `source_portal` field, the about page and the harvest sources.

## Harvesters

All need ckanext-harvest (`harvest ckan_harvester` in `ckan.plugins`) and
copy the files of the portal of origin here, one at a time with a pause,
so the site is a backup and not a list of links.

- `ckan_with_files`: a CKAN portal, keeping the provenance of every dataset
  (`source_portal`, `source_url`) and one organization per remote
  organization, prefixed by portal; remote license ids are mapped onto
  the local register (`CC-BY-4.0` is `cc-by`). Used for the provincial
  portals and for Rio Tercero's CKAN. See `harvester.py`.
- `municba`: the open data portal of the city of Cordoba
  (https://gobiernoabierto.cordoba.gob.ar), which is not CKAN: its open API
  gives datasets, versions and resources; every resource of every version
  becomes a resource here and the categories become groups. See
  `municba.py`.
- `villamaria`: the open data portal of Villa Maria
  (https://datos.villamaria.gob.ar), a site of its own without an API: the
  sitemap lists the datasets and each HTML page gives the metadata and the
  resources (loaded six at a time); the category becomes a group. See
  `villamaria.py`.
- `legislatura`: the open data portal of the provincial Legislature
  (https://legislaturacba.gob.ar/portal-de-datos-abiertos/), a few
  WordPress pages read through the REST API: every heading of a section
  page is a dataset, its files (CSV, XLSX, zipped XML, JSON, a metadata
  text) the resources, the section a group. See `legislatura.py`.
- `idecor`: the geoportal of the province (https://www.mapascordoba.gob.ar,
  IDECOR), whose two static JSON catalogs (downloads and geoservices) are
  joined by layer name: one dataset per layer, the GeoJSON / GeoTIFF /
  symbology / metadata copied, the WMS / WFS / WCS and the on-demand
  Shapefile and KML as links, the first level of the tree a group. See
  `idecor.py`.
- `riocuarto`: the transparency portal of the city of Rio Cuarto
  (https://economiariocuarto.gob.ar/transparencia), a Next.js site whose
  section data is JSON: one dataset per kind of document, every document
  a resource, the Google Drive files copied through their download URL,
  Drive folders as links. See `riocuarto.py`.
- `villaallende`: the transparency section of Villa Allende's WordPress
  site, whose bulletins, tenders and purchase calls are custom post types
  with their fields in the REST API: one dataset per post type, one
  resource per document. See `villaallende.py`.
- `bellville`: the Legislature's harvester pointed at Bell Ville's budget
  page (same shape: a heading, its files). See `bellville.py`.

Rio Tercero is a CKAN portal: a `ckan_with_files` source, nothing of its
own.

All take their options as JSON in the config of the harvest source; the
module docstrings list them.

`ckan cordoba-portal init-sources` creates the harvest sources the portal
ships with (and their organizations) when they are missing; the deploy runs
it. `init-pages` does the same for the pages.

## Developer installation

    pip install -e .
    pip install -r dev-requirements.txt

## Tests

    pytest --ckan-ini=test.ini

`test.ini` extends `../ckan/test-core.ini`; point it elsewhere if your CKAN
checkout lives in another place.

## License

[AGPL](https://www.gnu.org/licenses/agpl-3.0.en.html)
