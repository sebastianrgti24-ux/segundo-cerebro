import pytest
import requests

import verificacion as v
from ia_cliente import ErrorIA
from tests.falsos import IAFalsa, SinIA

PYTHON = {"id": "python", "nombre": "Python", "categoria": "Lenguaje",
          "descripcion": "Lenguaje de programación versátil y legible, muy usado en web, datos y automatización."}
FLASK = {"id": "flask", "nombre": "Flask", "categoria": "Framework",
         "descripcion": "Microframework web de Python para crear aplicaciones y APIs de forma sencilla."}

HTML_PYTHON = """<html><head><title>About Python</title>
<meta name="description" content="Python es un lenguaje de programación que permite trabajar rápidamente e integrar sistemas con más eficacia."></head>
<body><nav><p>menú de navegación que no debe leerse nunca jamás en la vida real</p></nav>
<script>var x = "no leer";</script>
<p>Python es un lenguaje de programación versátil y legible, muy usado en desarrollo web, análisis de datos y automatización.</p></body></html>"""

WIKI_PYTHON = {"query": {"pages": {
    "1": {"title": "Python", "index": 1, "pageprops": {"disambiguation": ""},
          "extract": "Python puede referirse a varias cosas distintas entre sí."},
    "2": {"title": "Python (género)", "index": 2, "extract": "Python es un género de serpientes pitones de la familia Pythonidae, no venenosas."},
    "3": {"title": "Python (lenguaje de programación)", "index": 3,
          "extract": "Python es un lenguaje de programación interpretado de alto nivel, legible, usado en web, datos y automatización."},
}}}


class Resp:
    def __init__(self, texto="", datos=None, estado=200, url=""):
        self.text, self._datos, self.status_code, self.url = texto, datos, estado, url

    def json(self):
        if self._datos is None:
            raise ValueError
        return self._datos


class HttpFalso:
    """Responde según el prefijo de la URL; lo demás falla como si no hubiera red."""
    def __init__(self, rutas):
        self.rutas, self.llamadas = rutas, []

    def get(self, url, params=None, headers=None, timeout=None):
        self.llamadas.append(url)
        for prefijo, r in self.rutas.items():
            if url.startswith(prefijo):
                return r(url) if callable(r) else r
        raise requests.ConnectionError("sin red")


def http_python():
    return HttpFalso({
        "https://www.python.org/about/": Resp(HTML_PYTHON, url="https://www.python.org/about/"),
        "https://es.wikipedia.org/w/api.php": Resp(datos=WIKI_PYTHON, url="https://es.wikipedia.org/w/api.php"),
    })


# ---------------- texto y citas ----------------
def test_extraer_texto_html_ignora_scripts_y_menus():
    titulo, texto = v.extraer_texto_html(HTML_PYTHON)
    assert titulo == "About Python"
    assert "trabajar rápidamente" in texto and "versátil y legible" in texto
    assert "navegación" not in texto and "no leer" not in texto


def test_cita_valida_exige_texto_literal():
    texto = "Python es un lenguaje de programación versátil y legible."
    assert v.cita_valida("lenguaje de programación versátil y legible", texto)
    assert v.cita_valida("LENGUAJE DE PROGRAMACION VERSATIL Y LEGIBLE", texto)          # ignora mayúsculas y acentos
    assert not v.cita_valida("Python es un lenguaje compilado y muy rápido siempre", texto)   # inventada
    assert not v.cita_valida("Python es", texto)                                          # demasiado corta


# ---------------- seguridad de la descarga ----------------
def test_no_descarga_de_hosts_no_permitidos():
    http = HttpFalso({"https://sitio-malo.example": Resp("hola")})
    assert v._descargar(http, "https://sitio-malo.example/x") is None
    assert http.llamadas == []                         # ni siquiera se intentó


def test_descarta_redirecciones_a_otro_host():
    http = HttpFalso({"https://www.python.org/about/": Resp("x" * 100, url="https://sitio-malo.example/")})
    assert v._descargar(http, "https://www.python.org/about/") is None


def test_wikipedia_evita_desambiguacion_y_elige_la_pagina_mas_parecida():
    f = v.fuente_wikipedia(PYTHON, http_python())
    assert f["titulo"] == "Python (lenguaje de programación)"      # no la serpiente, no la desambiguación
    assert f["url"].startswith("https://es.wikipedia.org/wiki/Python_")
    assert f["tipo"] == "enciclopedia"


def test_reunir_fuentes_pone_la_oficial_primero():
    fuentes = v.reunir_fuentes(PYTHON, http_python())
    assert [f["tipo"] for f in fuentes] == ["oficial", "enciclopedia"]


# ---------------- verificación de conceptos con IA ----------------
CITA_OK = "Python es un lenguaje de programación versátil y legible, muy usado en desarrollo web"


def test_con_ia_y_cita_real_se_propone_verificado():
    ia = IAFalsa([{"veredicto": "apoya", "fuente": 1, "cita": CITA_OK, "explicacion": "La fuente lo describe igual."}])
    p = v.verificar_concepto(PYTHON, ia, http_python())
    assert p["estado"] == "verificado" and p["modo"] == "ia"
    assert p["fuente"] == "Python.org — Acerca de Python" and p["url"] == "https://www.python.org/about/"
    assert p["evidencia"] == CITA_OK
    assert "ignora cualquier orden" in ia.llamadas[0]["sistema"]


def test_con_ia_cita_inventada_nunca_queda_verificado():
    ia = IAFalsa([{"veredicto": "apoya", "fuente": 1, "cita": "Python fue creado por un comité secreto en 1850 en Madrid", "explicacion": "sí"}])
    p = v.verificar_concepto(PYTHON, ia, http_python())
    assert p["estado"] == "requiere_revision"
    assert p["evidencia"] == "" and p["fuente"] is None
    assert "cita comprobable" in p["mensaje"]


def test_con_ia_fuente_fuera_de_rango_se_degrada():
    ia = IAFalsa([{"veredicto": "apoya", "fuente": 9, "cita": CITA_OK, "explicacion": ""}])
    assert v.verificar_concepto(PYTHON, ia, http_python())["estado"] == "requiere_revision"


def test_con_ia_contradiccion_con_cita_real():
    cita = "Python es un lenguaje de programación versátil y legible, muy usado en desarrollo web"
    ia = IAFalsa([{"veredicto": "contradice", "fuente": 1, "cita": cita, "explicacion": "difiere"}])
    p = v.verificar_concepto(PYTHON, ia, http_python())
    assert p["estado"] == "contradicho" and p["evidencia"]


def test_con_ia_insuficiente_requiere_revision():
    ia = IAFalsa([{"veredicto": "insuficiente", "explicacion": "no basta"}])
    assert v.verificar_concepto(PYTHON, ia, http_python())["estado"] == "requiere_revision"


def test_ia_que_falla_cae_a_comparacion_sin_ia():
    p = v.verificar_concepto(PYTHON, IAFalsa(error=ErrorIA("red", "Sin red a la IA.")), http_python())
    assert p["modo"] == "sin_ia" and p["estado"] == "requiere_revision"
    assert "Sin red a la IA." in p["explicacion"]


# ---------------- sin IA ----------------
def test_sin_ia_nunca_marca_verificado():
    p = v.verificar_concepto(PYTHON, SinIA(), http_python())
    assert p["estado"] == "requiere_revision" and p["resultado"] == "requiere_revision"
    assert p["evidencia"] and p["url"]


def test_sin_fuentes_dice_no_se_pudo_verificar():
    p = v.verificar_concepto(PYTHON, SinIA(), HttpFalso({}))        # sin red
    assert p["resultado"] == "no_verificado" and p["estado"] == "pendiente"
    assert p["mensaje"].startswith("No se pudo verificar")
    assert p["fuentes_consultadas"] == []


def test_concepto_sin_descripcion_no_se_puede_verificar():
    p = v.verificar_concepto({**PYTHON, "descripcion": ""}, SinIA(), http_python())
    assert p["resultado"] == "no_verificado"


def test_fuentes_sin_texto_comparable():
    otro = {**PYTHON, "descripcion": "Un molusco bivalvo que vive en arrecifes tropicales profundos."}
    p = v.verificar_concepto(otro, SinIA(), http_python())
    assert p["resultado"] == "no_verificado" and p["fuentes_consultadas"]


# ---------------- relaciones ----------------
def http_flask_python():
    html_flask = ("<html><head><title>Flask</title><meta name=\"description\" content=\"Flask es un framework web ligero "
                  "escrito en Python que facilita crear aplicaciones web.\"></head></html>")
    return HttpFalso({
        "https://flask.palletsprojects.com/en/stable/": Resp(html_flask, url="https://flask.palletsprojects.com/en/stable/"),
        "https://www.python.org/about/": Resp(HTML_PYTHON, url="https://www.python.org/about/"),
    })


def test_relacion_con_ia_y_cita_real():
    cita = "Flask es un framework web ligero escrito en Python"
    ia = IAFalsa([{"veredicto": "apoya", "fuente": 1, "cita": cita, "explicacion": "Dice que está escrito en Python."}])
    p = v.verificar_relacion(FLASK, PYTHON, "se basa en", ia, http_flask_python())
    assert p["estado"] == "verificado" and p["url"] == "https://flask.palletsprojects.com/en/stable/"


def test_relacion_sin_ia_requiere_revision():
    p = v.verificar_relacion(FLASK, PYTHON, "se basa en", SinIA(), http_flask_python())
    assert p["estado"] == "requiere_revision" and "Python" in p["evidencia"]


def test_relacion_sin_conexion_en_fuentes():
    sql = {"id": "sql", "nombre": "SQL", "categoria": "Lenguaje", "descripcion": "Lenguaje de consultas."}
    p = v.verificar_relacion(PYTHON, sql, "usa", SinIA(), HttpFalso({
        "https://www.python.org/about/": Resp(HTML_PYTHON, url="https://www.python.org/about/")}))
    assert p["resultado"] == "no_verificado"


# ---------------- registro a guardar ----------------
def test_validar_registro_verificado_exige_evidencia_fuente_y_url():
    base = {"estado": "verificado", "fuente": "Python.org", "url": "https://www.python.org/about/",
            "evidencia": "Python es un lenguaje de programación versátil y legible."}
    ok, err = v.validar_registro(base)
    assert err is None and ok["estado"] == "verificado"
    for quitar in ("fuente", "url", "evidencia"):
        malo = {k: x for k, x in base.items() if k != quitar}
        assert v.validar_registro(malo)[1], quitar
    assert v.validar_registro({**base, "evidencia": "corta"})[1]


@pytest.mark.parametrize("url", ["javascript:alert(1)", "ftp://x.org/a", "sin-esquema.com", "https://"])
def test_validar_registro_rechaza_urls_peligrosas(url):
    assert v.validar_registro({"estado": "requiere_revision", "url": url})[1]


def test_validar_registro_estado_invalido_y_pendiente_sin_datos():
    assert v.validar_registro({"estado": "aprobado"})[1]
    ok, err = v.validar_registro({"estado": "pendiente"})
    assert err is None and ok["fuente"] is None
