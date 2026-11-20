"""Fixtures compartidas por las pruebas."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_mod                       # noqa: E402
from grafo_utils import construir_snapshot   # noqa: E402
from tests.falsos import FalsaNeo4j          # noqa: E402

@pytest.fixture
def snap():
    f = FalsaNeo4j()
    return construir_snapshot(f.consultar("MATCH (n:Concepto) RETURN n"),
                              f.consultar("x r.verificacion_estado AS estado, r.origen_creacion AS creada_por"))


@pytest.fixture
def bd(monkeypatch):
    f = FalsaNeo4j()
    monkeypatch.setattr(app_mod, "consultar", f.consultar)
    monkeypatch.setattr(app_mod, "escribir", f.escribir)
    return f


@pytest.fixture
def cliente(bd):
    # Límites altos para que no interfieran con las pruebas de otras rutas.
    from limites import LimitadorVentana
    app_mod.limitadores["ia"] = LimitadorVentana(1000, 60)
    app_mod.limitadores["general"] = LimitadorVentana(1000, 60)
    app_mod.app.config["TESTING"] = True
    return app_mod.app.test_client()


@pytest.fixture
def con_ia(monkeypatch):
    def poner(ia):
        monkeypatch.setattr(app_mod, "ia", ia)
        return ia
    return poner
