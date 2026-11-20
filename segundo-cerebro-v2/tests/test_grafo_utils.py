from grafo_utils import (
    aristas_de_ruta, cadenas_de_aprendizaje, camino_mas_corto, grado, menciona, normalizar,
    orden_de_estudio, prerrequisitos, vecinos,
)


def test_normalizar_quita_acentos_y_signos():
    assert normalizar("  Vis.js  ") == "visjs"
    assert normalizar("¿Qué es la Inteligencia Artificial?") == "que es la inteligencia artificial"


def test_menciona_palabra_completa_y_plural():
    assert menciona("Flask es un framework de Python", "Python")
    assert menciona("Usa APIs REST", "API")            # plural simple
    assert not menciona("Pythonista experto", "Python")   # no es palabra completa
    assert menciona("Gráficas con Vis.js en el navegador", "Vis.js")


def test_grado_y_vecinos(snap):
    g = grado(snap)
    assert g["neo4j"] == 4 and g["python"] == 2
    assert set(vecinos(snap, "python", 1)) == {"flask", "neo4j"}
    assert vecinos(snap, "python", 2)["grafos"] == 2


def test_camino_mas_corto_ignora_direccion(snap):
    # Flask -> API REST -> JSON <- Vis.js : 3 saltos (el que se defiende en la documentación)
    assert camino_mas_corto(snap, "flask", "visjs") == ["flask", "api-rest", "json", "visjs"]
    assert camino_mas_corto(snap, "visjs", "flask") == ["visjs", "json", "api-rest", "flask"]
    assert camino_mas_corto(snap, "python", "python") == ["python"]
    assert camino_mas_corto(snap, "python", "no-existe") is None


def test_camino_respeta_maximo_de_saltos(snap):
    assert camino_mas_corto(snap, "flask", "visjs", max_saltos=2) is None


def test_aristas_de_ruta(snap):
    ar = aristas_de_ruta(snap, ["flask", "api-rest", "json"])
    assert [(a["origen"], a["destino"], a["tipo"]) for a in ar] == [
        ("flask", "api-rest", "expone"), ("api-rest", "json", "usa")]


def test_prerrequisitos_siguen_solo_dependencias(snap):
    # Flask se basa en Python -> Python es prerrequisito. «expone» no es dependencia.
    assert prerrequisitos(snap, "flask") == ["python"]
    # Neo4j se basa en Grafos, y «es un tipo de» Bases de Datos.
    assert set(prerrequisitos(snap, "neo4j")) == {"grafos", "bases-de-datos"}
    assert prerrequisitos(snap, "python") == []          # «se conecta a» no es dependencia


def test_prerrequisitos_orden_basico_primero(snap):
    snap["nodos"]["django"] = {"id": "django", "nombre": "Django", "descripcion": "", "categoria": "Framework", "estado": "pendiente"}
    snap["aristas"].append({"origen": "django", "destino": "flask", "tipo": "se basa en", "estado": "pendiente", "creada_por": None})
    # Django -> Flask -> Python : lo más básico (Python) va primero
    assert prerrequisitos(snap, "django") == ["python", "flask"]


def test_orden_de_estudio_por_niveles(snap):
    orden, ciclo = orden_de_estudio(snap)
    nivel = {o["id"]: o["nivel"] for o in orden}
    assert ciclo == []
    assert nivel["python"] < nivel["flask"]
    assert nivel["grafos"] < nivel["neo4j"]
    assert "cypher" not in nivel or nivel["cypher"] >= 0


def test_orden_de_estudio_detecta_ciclos(snap):
    snap["aristas"] += [
        {"origen": "a", "destino": "b", "tipo": "usa", "estado": "pendiente", "creada_por": None},
        {"origen": "b", "destino": "a", "tipo": "usa", "estado": "pendiente", "creada_por": None},
    ]
    for i in "ab":
        snap["nodos"][i] = {"id": i, "nombre": i.upper(), "descripcion": "", "categoria": "X", "estado": "pendiente"}
    _orden, ciclo = orden_de_estudio(snap)
    assert set(ciclo) == {"a", "b"}


def test_cadenas_de_aprendizaje(snap):
    cadenas = cadenas_de_aprendizaje(snap)
    assert ["python", "flask"] in cadenas
    assert all(len(c) >= 2 for c in cadenas)
