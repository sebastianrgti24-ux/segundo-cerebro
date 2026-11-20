import json

import httpx
import pytest

from estudio import preguntas_de_practica, recomendaciones
from ia_cliente import ClienteIA, ErrorIA, extraer_json
from limites import LimitadorVentana
from tests.falsos import IAFalsa, SinIA


# ---------------- estudio ----------------
def test_recomendaciones_basadas_en_el_grafo(snap):
    snap["nodos"]["suelto"] = {"id": "suelto", "nombre": "Suelto", "descripcion": "", "categoria": "X", "estado": "pendiente"}
    r = recomendaciones(snap, SinIA(), semilla=1)
    assert any(p["id"] == "suelto" and p["grado"] == 0 for p in r["pocas_conexiones"])
    niveles = {o["id"]: o["nivel"] for o in r["orden_estudio"]}
    assert niveles["python"] < niveles["flask"]
    assert any(c["nombres"] == ["Python", "Flask"] for c in r["rutas_aprendizaje"])
    assert r["verificacion"]["por_estado"]["pendiente"] == 11
    assert all(x["origen"] == "grafo" for x in r["pocas_conexiones"] + r["orden_estudio"] + r["preguntas_practica"])
    assert r["conceptos_sugeridos"] == [] and r["ia"]["modo"] == "sin_ia"


def test_preguntas_salen_de_datos_reales(snap):
    for p in preguntas_de_practica(snap, cantidad=50, semilla=3):
        assert p["pregunta"] and p["respuesta"] and p["nodos"]
        assert all(i in snap["nodos"] for i in p["nodos"])
    assert len(preguntas_de_practica(snap, cantidad=5, semilla=3)) == 5
    assert preguntas_de_practica(snap, 5, semilla=3) == preguntas_de_practica(snap, 5, semilla=3)   # determinista con semilla


def test_conceptos_sugeridos_por_ia_excluyen_los_existentes(snap):
    ia = IAFalsa([{"conceptos": [
        {"nombre": "python", "categoria": "Lenguaje", "motivo": "ya existe"},
        {"nombre": "Docker", "categoria": "Herramienta", "motivo": "Se usa para desplegar Flask."},
        {"nombre": "Docker", "categoria": "Herramienta", "motivo": "repetido"},
        {"nombre": 5}, "x"]}])
    r = recomendaciones(snap, ia, pedir_ia=True)
    assert [c["nombre"] for c in r["conceptos_sugeridos"]] == ["Docker"]
    assert r["conceptos_sugeridos"][0]["origen"] == "ia" and r["ia"]["modo"] == "ia"


def test_sin_pedir_ia_no_se_llama_a_la_ia(snap):
    ia = IAFalsa()
    recomendaciones(snap, ia, pedir_ia=False)
    assert ia.llamadas == []


def test_pedir_ia_sin_configurar_o_con_error(snap):
    assert "OPENAI_API_KEY" in recomendaciones(snap, SinIA(), pedir_ia=True)["ia"]["aviso"]
    r = recomendaciones(snap, IAFalsa(error=ErrorIA("limite", "Sin saldo.")), pedir_ia=True)
    assert r["ia"]["aviso"] == "Sin saldo." and r["conceptos_sugeridos"] == []


# ---------------- límites ----------------
def test_limitador_de_ventana():
    t = [0.0]
    lim = LimitadorVentana(3, 60, reloj=lambda: t[0])
    assert [lim.permitir("a")[0] for _ in range(3)] == [True, True, True]
    ok, espera = lim.permitir("a")
    assert not ok and 1 <= espera <= 61
    assert lim.permitir("b")[0]                  # otra IP no se ve afectada
    t[0] = 61.0
    assert lim.permitir("a")[0]                  # la ventana se renueva


# ---------------- cliente de IA con el SDK real y HTTP simulado ----------------
def cliente_con(handler, **kw):
    http = httpx.Client(transport=httpx.MockTransport(handler))
    from openai import OpenAI
    sdk = OpenAI(api_key="clave-de-prueba", http_client=http, max_retries=0)
    return ClienteIA(api_key="clave-de-prueba", client=sdk, **kw)


def respuesta_ok(contenido):
    return {"id": "x", "object": "chat.completion", "created": 1, "model": "m",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": contenido}}]}


def test_cliente_envia_json_mode_y_parsea():
    vistos = {}

    def handler(req):
        vistos["url"], vistos["auth"], vistos["cuerpo"] = str(req.url), req.headers["authorization"], json.loads(req.content)
        return httpx.Response(200, json=respuesta_ok('{"a": 1}'))

    r = cliente_con(handler, modelo="modelo-x").json("sistema", "usuario")
    assert r == {"a": 1}
    assert vistos["url"].endswith("/chat/completions") and vistos["auth"] == "Bearer clave-de-prueba"
    c = vistos["cuerpo"]
    assert c["model"] == "modelo-x" and c["response_format"] == {"type": "json_object"}
    assert c["messages"][0] == {"role": "system", "content": "sistema"} and "max_completion_tokens" in c


@pytest.mark.parametrize("estado,codigo", [(401, "autenticacion"), (403, "autenticacion"), (429, "limite"),
                                            (404, "servicio"), (500, "servicio")])
def test_cliente_traduce_errores_http(estado, codigo):
    def handler(req):
        return httpx.Response(estado, json={"error": {"message": "x", "type": "y"}})
    with pytest.raises(ErrorIA) as e:
        cliente_con(handler).json("s", "u")
    assert e.value.codigo == codigo


def test_cliente_reintenta_sin_response_format_si_no_se_admite():
    llamadas = []

    def handler(req):
        cuerpo = json.loads(req.content)
        llamadas.append("response_format" in cuerpo)
        if "response_format" in cuerpo:
            return httpx.Response(400, json={"error": {"message": "unsupported", "type": "invalid_request_error"}})
        return httpx.Response(200, json=respuesta_ok('{"ok": true}'))

    assert cliente_con(handler).json("s", "u") == {"ok": True}
    assert llamadas == [True, False]


def test_cliente_error_de_red_y_tiempo():
    def sin_red(req):
        raise httpx.ConnectError("sin red")
    with pytest.raises(ErrorIA) as e:
        cliente_con(sin_red).json("s", "u")
    assert e.value.codigo == "red"

    def lento(req):
        raise httpx.ReadTimeout("lento")
    with pytest.raises(ErrorIA) as e:
        cliente_con(lento).json("s", "u")
    assert e.value.codigo == "tiempo"


def test_cliente_respuesta_no_json():
    with pytest.raises(ErrorIA) as e:
        cliente_con(lambda r: httpx.Response(200, json=respuesta_ok("esto no es json"))).json("s", "u")
    assert e.value.codigo == "respuesta"


def test_extraer_json_tolera_bloques_de_codigo():
    assert extraer_json('```json\n{"a": 2}\n```') == {"a": 2}
    with pytest.raises(ErrorIA):
        extraer_json("[1, 2]")


def test_sin_clave_no_esta_disponible_y_no_llama(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    c = ClienteIA.desde_entorno()
    assert not c.disponible
    with pytest.raises(ErrorIA) as e:
        c.json("s", "u")
    assert e.value.codigo == "no_configurada"


def test_base_url_usa_max_tokens_clasico():
    vistos = {}

    def handler(req):
        vistos["c"] = json.loads(req.content)
        return httpx.Response(200, json=respuesta_ok("{}"))
    c = cliente_con(handler)
    c.base_url = "http://localhost:11434/v1"
    c.json("s", "u")
    assert "max_tokens" in vistos["c"] and "max_completion_tokens" not in vistos["c"]
