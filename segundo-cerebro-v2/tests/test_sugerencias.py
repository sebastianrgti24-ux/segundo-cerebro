from sugerencias import sugerir
from tests.falsos import IAFalsa, SinIA
from ia_cliente import ErrorIA


def _django(snap):
    snap["nodos"]["django"] = {
        "id": "django", "nombre": "Django", "categoria": "Framework", "estado": "pendiente",
        "descripcion": "Framework web de alto nivel desarrollado en Python para sitios con base de datos."}
    return "django"


def test_sin_ia_detecta_python_y_flask_para_django(snap):
    r = sugerir(snap, _django(snap), SinIA())
    assert r["modo"] == "sin_ia" and r["aviso"]
    pares = {(s["origen"], s["destino"], s["tipo"]) for s in r["sugerencias"]}
    assert ("django", "python", "está desarrollado en") in pares       # la descripción dice «desarrollado en Python»
    assert ("django", "flask", "es comparable con") in pares           # misma categoría y términos comunes
    for s in r["sugerencias"]:
        assert s["justificacion"] and s["confianza"] in ("alta", "media", "baja")


def test_sin_ia_no_repite_relaciones_existentes(snap):
    _django(snap)
    snap["aristas"].append({"origen": "django", "destino": "python", "tipo": "está desarrollado en",
                            "estado": "pendiente", "creada_por": None})
    r = sugerir(snap, "django", SinIA())
    assert all(not (s["destino"] == "python" and s["tipo"] == "está desarrollado en") for s in r["sugerencias"])


def test_con_ia_valida_y_filtra(snap):
    _django(snap)
    ia = IAFalsa([{"sugerencias": [
        {"origen_id": "django", "destino_id": "python", "tipo": "se basa en", "justificacion": "Django está escrito en Python.", "base": "grafo", "confianza": "alta"},
        {"origen_id": "django", "destino_id": "id-inventado", "tipo": "usa", "justificacion": "x", "base": "grafo", "confianza": "alta"},   # id inexistente
        {"origen_id": "flask", "destino_id": "python", "tipo": "usa", "justificacion": "x", "base": "grafo", "confianza": "alta"},          # no involucra al objetivo
        {"origen_id": "django", "destino_id": "django", "tipo": "usa", "justificacion": "x", "base": "grafo", "confianza": "alta"},         # consigo mismo
        {"origen_id": "django", "destino_id": "flask", "tipo": "es comparable con", "justificacion": "Ambos son frameworks web.", "base": "conocimiento_general", "confianza": "muy-alta"},
        {"origen_id": "django", "destino_id": "python", "tipo": "SE BASA EN", "justificacion": "duplicada", "base": "grafo", "confianza": "media"},  # duplicado en la misma respuesta
        "no es un dict", {"origen_id": 5},
    ]}])
    r = sugerir(snap, "django", ia)
    assert r["modo"] == "ia"
    assert [(s["destino"], s["tipo"]) for s in r["sugerencias"]] == [("python", "se basa en"), ("flask", "es comparable con")]
    assert r["sugerencias"][1]["confianza"] == "baja"                         # valor fuera de lista -> «baja»
    assert "sin fuente verificada" in r["sugerencias"][1]["respaldo"]         # conocimiento general, sin URL
    assert r["sugerencias"][1]["fuente_url"] is None


def test_ia_no_puede_inventar_fuentes_url(snap):
    _django(snap)
    snap["nodos"]["python"].update(estado="verificado", fuente="Python.org", url="https://www.python.org/about/")
    ia = IAFalsa([{"sugerencias": [{"origen_id": "django", "destino_id": "python", "tipo": "usa", "justificacion": "ok",
                                    "base": "conocimiento_general", "confianza": "alta",
                                    "fuente_url": "https://sitio-falso.example"}]}])
    s = sugerir(snap, "django", ia)["sugerencias"][0]
    assert s["fuente_url"] == "https://www.python.org/about/"       # solo sale de una fuente ya verificada y guardada


def test_no_sugiere_si_ya_existe_la_inversa_con_el_mismo_tipo(snap):
    ia = IAFalsa([{"sugerencias": [{"origen_id": "python", "destino_id": "flask", "tipo": "se basa en", "justificacion": "x",
                                    "base": "grafo", "confianza": "alta"}]}])
    assert sugerir(snap, "flask", ia)["sugerencias"] == []


def test_si_la_ia_falla_cae_a_reglas(snap):
    _django(snap)
    r = sugerir(snap, "django", IAFalsa(error=ErrorIA("limite", "Límite de uso.")))
    assert r["modo"] == "sin_ia" and "Límite de uso." in r["aviso"] and r["sugerencias"]


def test_concepto_inexistente(snap):
    import pytest
    with pytest.raises(KeyError):
        sugerir(snap, "nada", SinIA())


def test_maximo_de_sugerencias(snap):
    ia = IAFalsa([{"sugerencias": [
        {"origen_id": "flask", "destino_id": d, "tipo": f"rel{i}", "justificacion": "x", "base": "grafo", "confianza": "baja"}
        for i, d in enumerate(["python", "neo4j", "sql", "json", "cypher", "grafos", "visjs", "bases-de-datos"])]}])
    assert len(sugerir(snap, "flask", ia)["sugerencias"]) == 6
