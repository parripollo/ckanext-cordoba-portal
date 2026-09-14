"""ckan cordoba-portal init-sources: the harvest sources the portal ships with."""
import json
from unittest import mock

import pytest

from ckan.plugins import toolkit

from ckan.tests import factories
from ckan.tests.helpers import call_action

from ckanext.cordoba_portal.cli import cordoba_portal


@pytest.mark.usefixtures("with_plugins", "clean_db")
class TestInitSources:

    def test_creates_the_missing_ones_once(self, cli):
        own = factories.Organization(name="cbadatos", title="Córdoba Datos")

        result = cli.invoke(cordoba_portal, ["init-sources"])

        assert result.exit_code == 0, result.output
        assert "created: organization municba" in result.output
        assert "created: source municba" in result.output
        org = call_action("organization_show", id="municba")
        assert org["source_portal"] == "municba"
        source = call_action("harvest_source_show", id="municba")
        assert source["source_type"] == "municba"
        assert source["frequency"] == "WEEKLY"
        assert source["owner_org"] == own["id"]
        assert json.loads(source["config"])["single_org"] == "municba"
        assert call_action("harvest_source_show", id="legislatura")["source_type"] == "legislatura"
        assert call_action("harvest_source_show", id="idecor")["frequency"] == "MONTHLY"

        result = cli.invoke(cordoba_portal, ["init-sources"])

        assert result.exit_code == 0, result.output
        assert "exists: organization municba" in result.output
        assert "exists: source municba" in result.output

    def test_without_the_own_organization(self, cli):
        result = cli.invoke(cordoba_portal, ["init-sources"])

        assert result.exit_code == 0, result.output
        source = call_action("harvest_source_show", id="municba")
        assert source["owner_org"] == call_action("organization_show", id="municba")["id"]

    def test_a_source_without_its_harvester_is_skipped(self, cli):
        with mock.patch("ckanext.cordoba_portal.cli.SOURCES", [
                {"organization": {"name": "otra", "title": "Otra", "source_portal": "municba"},
                 "source": {"name": "otra", "title": "Otra", "url": "https://otra.example",
                            "source_type": "no-such-harvester"}}]):
            result = cli.invoke(cordoba_portal, ["init-sources"])

        assert result.exit_code == 0, result.output
        assert "skipped: source otra" in result.output
        with pytest.raises(toolkit.ObjectNotFound):
            call_action("organization_show", id="otra")
