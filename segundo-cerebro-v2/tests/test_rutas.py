import pytest

import app as app_mod
from limites import LimitadorVentana
from tests.falsos import IAFalsa, SinIA
from tests.test_verificacion import HTML_PYTHON, WIKI_PYTHON, HttpFalso, Resp, http_flask_python


@pytest.fixture(autouse=True)
def sin_ia_por_defecto(con_ia):
    con_ia(SinIA())


def post(c, ruta, datos):
    return c.post(ruta, json=datos)


# ---------------- v1 intacta ----------------
def test_v1_grafo_conserva_su_forma_y_suma_estado(cliente):
    r = cliente.get("/api/grafo").get_json()
    assert len(r["nodes"]) == 10 and len(r["edges"]) == 11
    n = next(x for x in r["nodes"] if x["id"] == "flask")
    assert {"id", "label", "title", "group", "descripcion", "categoria"} <= set(n)
    assert n["estado"] == "pendiente"
    assert {"from", "to", "label"} <= set(r["edges"][0])


def test_v1_crear_concepto_y_relacion(cliente):
    r = post(cliente, "/api/concepto", {"nombre": "Docker", "categoria": "Herramienta"})
    assert r.status_code == 201
    nuevo = r.get_json()["id"]
    assert post(cliente, "/api/relacion", {"origen": nuevo, "destino": "flask", "tipo": "empaqueta"}).status_code == 201
    assert post(cliente, "/api/relacion", {"origen": nuevo, "destino": "flask", "tipo": "empaqueta"}).status_code == 200
    assert post(cliente, "/api/concepto", {"nombre": " "}).status_code == 400
    assert post(cliente, "/api/relacion", {"origen": "a", "destino": "a", "tipo": "x"}).status_code == 400


def test_la_base_caida_devuelve_503_json(cliente, bd):
    bd.caida = True
    r = cliente.get("/api/grafo")
    assert r.status_code == 503 and "error" in r.get_json()
    assert post(cliente, "/api/chat", {"pregunta": "Flask"}).status_code == 503


def test_errores_json_en_la_api(cliente):
    assert cliente.get("/api/no-existe").get_json()["error"]
    assert cliente.get("/api/chat").status_code == 405
    assert post(cliente, "/api/chat", None).status_code == 400
    grande = cliente.post("/api/chat", data="x" * 70000, content_type="application/json")
    assert grande.status_code == 413


# ---------------- chat ----------------
def test_chat_validaciones(cliente):
    assert post(cliente, "/api/chat", {"pregunta": "   "}).status_code == 400
    assert post(cliente, "/api/chat", {"pregunta": 5}).status_code == 400


def test_chat_sin_ia(cliente):
    r = post(cliente, "/api/chat", {"pregunta": "¿Qué necesito aprender antes de estudiar Flask?"}).get_json()
    assert r["modo"] == "sin_ia" and "Python" in r["respuesta"]["almacenado"]
    assert r["nodos"][0]["id"] in ("python", "flask")


def test_chat_con_ia_no_escribe_en_la_base(cliente, bd, con_ia):
    ia = con_ia(IAFalsa([{"almacenado": "Flask se basa en Python.", "complementario": "",
                          "datos_suficientes": True, "nodos_citados": ["flask", "python"]}]))
    r = post(cliente, "/api/chat", {"pregunta": "¿Cómo se relaciona Flask con Python?",
                                    "historial": [{"rol": "usuario", "texto": "hola"}]}).get_json()
    assert r["modo"] == "ia" and len(ia.llamadas) == 1
    assert bd.escrituras == []                    # la IA y el chat nunca modifican Neo4j


def test_chat_sin_contexto_no_llama_a_la_ia(cliente, con_ia):
    ia = con_ia(IAFalsa())
    r = post(cliente, "/api/chat", {"pregunta": "¿Capital de Francia?"}).get_json()
    assert r["respuesta"]["datos_suficientes"] is False and ia.llamadas == []


def test_la_clave_de_la_api_no_sale_en_ninguna_respuesta(cliente, monkeypatch):
    from ia_cliente import ClienteIA
    secreta = "sk-proj-SECRETA-123456"
    monkeypatch.setattr(app_mod, "ia", ClienteIA(api_key=secreta, modelo="m"))
    textos = [cliente.get("/api/estado-ia").get_data(as_text=True), cliente.get("/").get_data(as_text=True),
              cliente.get("/api/grafo").get_data(as_text=True)]
    assert cliente.get("/api/estado-ia").get_json() == {"disponible": True, "modelo": "m"}
    assert all(secreta not in t for t in textos)


def test_no_hay_inyeccion_cypher_el_texto_del_usuario_va_como_parametro(cliente, bd):
    malo = "x' DETACH DELETE n //"
    post(cliente, "/api/concepto", {"nombre": malo, "descripcion": malo, "categoria": malo})
    post(cliente, "/api/aceptar-relacion", {"origen": "flask", "destino": "python", "tipo": malo, "justificacion": malo})
    post(cliente, "/api/aceptar-verificacion", {"tipo": "concepto", "id": "flask", "estado": "requiere_revision",
                                                "fuente": malo, "evidencia": malo})
    assert bd.escrituras
    assert all("DETACH" not in q and malo not in q for q, _ in bd.escrituras)
    assert any(malo in str(p) for _q, p in bd.escrituras)        # sí viaja, pero como dato


def test_limite_de_solicitudes_a_la_ia(cliente):
    app_mod.limitadores["ia"] = LimitadorVentana(2, 60)
    codigos = [post(cliente, "/api/chat", {"pregunta": "Flask"}).status_code for _ in range(3)]
    assert codigos == [200, 200, 429]
    r = post(cliente, "/api/chat", {"pregunta": "Flask"})
    assert r.headers["Retry-After"].isdigit() and "error" in r.get_json()


# ---------------- sugerir y aceptar relaciones ----------------
def test_sugerir_y_aceptar_relacion(cliente, bd):
    nuevo = post(cliente, "/api/concepto", {
        "nombre": "Django", "categoria": "Framework",
        "descripcion": "Framework web de alto nivel desarrollado en Python para sitios con base de datos."}).get_json()["id"]
    antes = len(bd.escrituras)
    r = post(cliente, "/api/sugerir-relaciones", {"concepto_id": nuevo})
    assert r.status_code == 200
    sug = r.get_json()["sugerencias"]
    assert len(bd.escrituras) == antes                   # sugerir no guarda nada
    s = next(x for x in sug if x["destino"] == "python")

    ok = post(cliente, "/api/aceptar-relacion", {
        "origen": s["origen"], "destino": s["destino"], "tipo": s["tipo"],
        "justificacion": s["justificacion"], "confianza": s["confianza"], "respaldo": s["respaldo"]})
    assert ok.status_code == 201 and ok.get_json()["creada"] is True
    arista = next(a for a in bd.aristas if a["origen"] == nuevo and a["destino"] == "python")
    assert arista["origen_creacion"] == "ia_aprobada" and arista["verificacion_estado"] == "pendiente"

    # ya no se vuelve a sugerir la misma relación
    again = post(cliente, "/api/sugerir-relaciones", {"concepto_id": nuevo}).get_json()["sugerencias"]
    assert all(not (x["destino"] == "python" and x["tipo"] == s["tipo"]) for x in again)


def test_aceptar_relacion_evita_duplicados_y_valida(cliente):
    base = {"origen": "flask", "destino": "python", "tipo": "SE BASA EN"}      # ya existe como «se basa en»
    r = post(cliente, "/api/aceptar-relacion", base)
    assert r.status_code == 200 and r.get_json()["creada"] is False
    assert post(cliente, "/api/aceptar-relacion", {"origen": "flask", "destino": "flask", "tipo": "x"}).status_code == 400
    assert post(cliente, "/api/aceptar-relacion", {"origen": "flask", "destino": "nada", "tipo": "x"}).status_code == 404
    assert post(cliente, "/api/aceptar-relacion", {"origen": "flask"}).status_code == 400


def test_sugerir_validaciones(cliente):
    assert post(cliente, "/api/sugerir-relaciones", {}).status_code == 400
    assert post(cliente, "/api/sugerir-relaciones", {"concepto_id": "nada"}).status_code == 404


def test_sugerir_con_ia_falsa(cliente, con_ia):
    con_ia(IAFalsa([{"sugerencias": [{"origen_id": "sql", "destino_id": "json", "tipo": "intercambia datos con",
                                      "justificacion": "Ambos mueven datos.", "base": "conocimiento_general", "confianza": "baja"}]}]))
    r = post(cliente, "/api/sugerir-relaciones", {"concepto_id": "sql"}).get_json()
    assert r["modo"] == "ia" and r["sugerencias"][0]["destino"] == "json"


# ---------------- verificar y guardar ----------------
@pytest.fixture
def red(monkeypatch):
    http = HttpFalso({
        "https://www.python.org/about/": Resp(HTML_PYTHON, url="https://www.python.org/about/"),
        "https://flask.palletsprojects.com/en/stable/": http_flask_python().rutas["https://flask.palletsprojects.com/en/stable/"],
        "https://es.wikipedia.org/w/api.php": Resp(datos=WIKI_PYTHON, url="https://es.wikipedia.org/w/api.php"),
    })
    monkeypatch.setattr(app_mod, "http_fuentes", http)
    return http


def test_verificar_propone_pero_no_guarda(cliente, bd, red, con_ia):
    con_ia(IAFalsa([{"veredicto": "apoya", "fuente": 1,
                     "cita": "Python es un lenguaje de programación versátil y legible, muy usado en desarrollo web",
                     "explicacion": "ok"}]))
    r = post(cliente, "/api/verificar", {"tipo": "concepto", "id": "python"})
    p = r.get_json()
    assert r.status_code == 200 and p["estado"] == "verificado" and p["url"]
    assert bd.escrituras == []                                    # solo propone
    assert bd.nodos["python"].get("verificacion_estado") is None


def test_flujo_completo_verificar_aceptar_y_ver_fuentes(cliente, bd, red, con_ia):
    con_ia(IAFalsa([{"veredicto": "apoya", "fuente": 1,
                     "cita": "Python es un lenguaje de programación versátil y legible, muy usado en desarrollo web",
                     "explicacion": "ok"}]))
    p = post(cliente, "/api/verificar", {"tipo": "concepto", "id": "python"}).get_json()
    guardado = post(cliente, "/api/aceptar-verificacion", {
        "tipo": "concepto", "id": "python", "estado": p["estado"], "fuente": p["fuente"], "url": p["url"], "evidencia": p["evidencia"]})
    assert guardado.status_code == 200 and guardado.get_json()["estado"] == "verificado"

    grafo = cliente.get("/api/grafo").get_json()
    assert next(n for n in grafo["nodes"] if n["id"] == "python")["estado"] == "verificado"
    fuentes = cliente.get("/api/fuentes?id=python").get_json()
    assert fuentes["conceptos"][0]["url"] == p["url"] and fuentes["conceptos"][0]["fecha"] and fuentes["relaciones"] == []
    assert cliente.get("/api/fuentes?id=flask").get_json()["conceptos"] == []


def test_verificar_relacion_y_guardarla(cliente, bd, red):
    p = post(cliente, "/api/verificar", {"tipo": "relacion", "origen": "flask", "destino": "python",
                                         "relacion_tipo": "se basa en"}).get_json()
    assert p["estado"] == "requiere_revision"                 # sin IA nunca queda «verificado»
    r = post(cliente, "/api/aceptar-verificacion", {
        "tipo": "relacion", "origen": "flask", "destino": "python", "relacion_tipo": "se basa en",
        "estado": "requiere_revision", "fuente": p["fuente"], "url": p["url"], "evidencia": p["evidencia"]})
    assert r.status_code == 200
    assert cliente.get("/api/fuentes?origen=flask&destino=python&tipo=se basa en").get_json()["relaciones"][0]["estado"] == "requiere_revision"
    edge = next(e for e in cliente.get("/api/grafo").get_json()["edges"] if e["from"] == "flask" and e["to"] == "python")
    assert edge["estado"] == "requiere_revision"


def test_aceptar_verificacion_no_acepta_verificado_sin_evidencia(cliente, bd):
    r = post(cliente, "/api/aceptar-verificacion", {"tipo": "concepto", "id": "python", "estado": "verificado"})
    assert r.status_code == 400 and bd.escrituras == []
    r = post(cliente, "/api/aceptar-verificacion", {"tipo": "concepto", "id": "python", "estado": "verificado",
                                                    "fuente": "X", "url": "javascript:alert(1)", "evidencia": "texto de evidencia suficientemente largo"})
    assert r.status_code == 400


def test_verificar_y_aceptar_validaciones(cliente):
    assert post(cliente, "/api/verificar", {"tipo": "otro"}).status_code == 400
    assert post(cliente, "/api/verificar", {"tipo": "concepto", "id": "nada"}).status_code == 404
    assert post(cliente, "/api/verificar", {"tipo": "relacion", "origen": "flask", "destino": "python", "relacion_tipo": "no existe"}).status_code == 404
    assert post(cliente, "/api/aceptar-verificacion", {"tipo": "concepto", "id": "nada", "estado": "pendiente"}).status_code == 404
    assert post(cliente, "/api/aceptar-verificacion", {"tipo": "relacion", "origen": "flask", "destino": "python",
                                                       "relacion_tipo": "no existe", "estado": "pendiente"}).status_code == 404
    assert post(cliente, "/api/aceptar-verificacion", {"tipo": "otro", "estado": "pendiente"}).status_code == 400


def test_sin_red_dice_no_se_pudo_verificar(cliente, monkeypatch):
    monkeypatch.setattr(app_mod, "http_fuentes", HttpFalso({}))
    p = post(cliente, "/api/verificar", {"tipo": "concepto", "id": "python"}).get_json()
    assert p["mensaje"].startswith("No se pudo verificar") and p["estado"] == "pendiente"


# ---------------- recomendaciones ----------------
def test_recomendaciones(cliente, con_ia):
    r = cliente.get("/api/recomendaciones").get_json()
    assert r["orden_estudio"] and r["preguntas_practica"] and r["conceptos_sugeridos"] == []
    ia = con_ia(IAFalsa([{"conceptos": [{"nombre": "Docker", "categoria": "Herramienta", "motivo": "Despliega Flask."}]}]))
    r = cliente.get("/api/recomendaciones?ia=1").get_json()
    assert r["conceptos_sugeridos"][0]["origen"] == "ia" and len(ia.llamadas) == 1


def test_recomendaciones_con_ia_respeta_el_limite(cliente, con_ia):
    con_ia(IAFalsa([{"conceptos": []}] * 5))
    app_mod.limitadores["ia"] = LimitadorVentana(1, 60)
    assert cliente.get("/api/recomendaciones?ia=1").status_code == 200
    assert cliente.get("/api/recomendaciones?ia=1").status_code == 429
    assert cliente.get("/api/recomendaciones").status_code == 200     # sin IA no consume ese límite


# ---------------- entorno ----------------
def test_modo_debug_solo_en_desarrollo(monkeypatch):
    monkeypatch.delenv("APP_ENTORNO", raising=False)
    assert app_mod.entorno_es_desarrollo() is True
    monkeypatch.setenv("APP_ENTORNO", "produccion")
    assert app_mod.entorno_es_desarrollo() is False
    monkeypatch.setenv("APP_ENTORNO", "PRODUCCION")
    assert app_mod.entorno_es_desarrollo() is False
