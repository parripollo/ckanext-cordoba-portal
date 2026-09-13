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

Add `cordoba_portal` to `ckan.plugins` in your CKAN config and restart CKAN.

## Config settings

None yet.

## Developer installation

    pip install -e .
    pip install -r dev-requirements.txt

## Tests

    pytest --ckan-ini=test.ini

`test.ini` extends `../ckan/test-core.ini`; point it elsewhere if your CKAN
checkout lives in another place.

## License

[AGPL](https://www.gnu.org/licenses/agpl-3.0.en.html)
