"""Harvester of the budget pages of Bell Ville.

The city's WordPress site (https://bellville.gob.ar/presupuesto/) lists its
budget documents the way the Legislature's portal does: a heading
("Presupuesto", "Régimen tarifario e Impositivo", "Régimen de
contratación"), then the files. ``bellville`` is the Legislature's
harvester pointed at those pages: one dataset per heading, one resource per
file, named as the page names it ("Presupuesto - Ejecutado 2024"), the
files copied here (``FileCopyMixin``).

The tenders (``/llamados-a-licitacion/``) are posts, not documents on the
page: not harvested.

Source config (JSON): the same as ``legislatura`` (``pages`` defaults to
``["presupuesto"]``).
"""
from ckanext.cordoba_portal.legislatura import LegislaturaHarvester


class BellVilleHarvester(LegislaturaHarvester):

    portal = "bellville"
    default_pages = ["presupuesto"]

    def info(self):
        return {
            "name": "bellville",
            "title": "Bell Ville (presupuesto)",
            "description": (
                "Harvest de las páginas de presupuesto de la Municipalidad de Bell "
                "Ville, copiando los archivos."
            ),
            "form_config_interface": "Text",
        }
