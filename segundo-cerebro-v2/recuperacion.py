"""
Recuperación (la «R» de RAG): dada una pregunta, decide qué parte del grafo
es relevante. Se hace con código propio sobre el snapshot, sin IA, para que
sea predecible y comprobable.

Devuelve un contexto:
    {
      "semillas":  [ids de conceptos mencionados o más parecidos a la pregunta],
      "nodos":     {id: nodo},          # semillas + vecinos (con tope)
      "aristas":   [aristas entre esos nodos],
      "rutas":     [[ids]],             # camino más corto entre semillas
      "prerrequisitos": {id: [ids]},    # si la pregunta es de «qué aprender antes»
      "intenciones": {"ruta": bool, "prerrequisitos": bool}
    }
"""
import re

from grafo_utils import (
    aristas_de_ruta, camino_mas_corto, menciona, normalizar, prerrequisitos,
    tokens_significativos, vecinos,
)

MAX_NODOS_CONTEXTO = 25
RE_RUTA = re.compile(r"\b(camino|ruta|llegar|conecta|conectan|conexion|conexiones|relaciona|relacionan|relacion|vincula|entre)\b")
RE_PRERREQ = re.compile(r"\b(antes de|prerrequisit\w*|requisit\w*|previo|previos|necesito (aprender|saber|ver|conocer)|que debo (aprender|saber|ver))\b")


def _posicion(pregunta_norm, nombre):
    """Posición donde aparece el nombre en la pregunta (para ordenar), o None."""
    n = normalizar(nombre)
    m = re.search(rf"(?<!\w){re.escape(n)}(?:s|es)?(?!\w)", pregunta_norm)
    return m.start() if m else None


def _semillas_por_nombre(snap, pregunta):
    p = normalizar(pregunta)
    hallados = []
    for nodo in snap["nodos"].values():
        if len(normalizar(nodo["nombre"])) < 2:
            continue
        pos = _posicion(p, nodo["nombre"])
        if pos is not None:
            hallados.append((pos, -len(nodo["nombre"]), nodo["id"]))
    hallados.sort()
    # Si «Inteligencia Artificial» y «Inteligencia» coinciden, gana el más largo
    # (se descarta el nombre contenido en otro nombre ya encontrado).
    ids = [h[2] for h in hallados]
    finales = []
    for i in ids:
        n = normalizar(snap["nodos"][i]["nombre"])
        if not any(n != normalizar(snap["nodos"][j]["nombre"]) and n in normalizar(snap["nodos"][j]["nombre"]) for j in ids):
            finales.append(i)
    return finales


def _semillas_por_categoria(snap, pregunta):
    p = normalizar(pregunta)
    cats = {n["categoria"] for n in snap["nodos"].values()}
    ids = []
    for c in cats:
        if len(normalizar(c)) >= 4 and menciona(p, c):
            ids += [n["id"] for n in snap["nodos"].values() if n["categoria"] == c]
    return ids


def _semillas_por_texto(snap, pregunta, maximo=3):
    """Último recurso: parecido de palabras con nombre y descripción."""
    q = tokens_significativos(pregunta)
    if not q:
        return []
    puntajes = []
    for nodo in snap["nodos"].values():
        en_nombre = len(q & tokens_significativos(nodo["nombre"], 3))
        en_desc = len(q & tokens_significativos(nodo["descripcion"]))
        total = en_nombre * 3 + en_desc
        if en_nombre >= 1 or en_desc >= 2:
            puntajes.append((-total, nodo["nombre"], nodo["id"]))
    puntajes.sort()
    return [p[2] for p in puntajes[:maximo]]


def recuperar(snap, pregunta, pregunta_anterior=None):
    """Construye el contexto de la pregunta. Si no hay nada relevante, `semillas` queda vacío."""
    p = normalizar(pregunta)
    semillas = _semillas_por_nombre(snap, pregunta)
    if not semillas:
        semillas = _semillas_por_categoria(snap, pregunta)
    if not semillas:
        semillas = _semillas_por_texto(snap, pregunta)
    if not semillas and pregunta_anterior:        # preguntas de seguimiento: «¿y Flask?»
        semillas = _semillas_por_nombre(snap, pregunta_anterior)

    intenciones = {
        "ruta": bool(RE_RUTA.search(p)) and len(semillas) >= 2,
        "prerrequisitos": bool(RE_PRERREQ.search(p)) and len(semillas) >= 1,
    }

    # Vecindad: 1 salto; si el contexto queda corto, un segundo salto.
    elegidos = list(dict.fromkeys(semillas))
    for saltos in (1, 2):
        for s in semillas:
            for v, _d in sorted(vecinos(snap, s, saltos).items(), key=lambda kv: (kv[1], kv[0])):
                if v not in elegidos and len(elegidos) < MAX_NODOS_CONTEXTO:
                    elegidos.append(v)
        if len(elegidos) >= 8:
            break

    rutas = []
    if intenciones["ruta"]:
        for a, b in zip(semillas, semillas[1:]):
            ruta = camino_mas_corto(snap, a, b)
            if ruta:
                rutas.append(ruta)
                for i in ruta:
                    if i not in elegidos and len(elegidos) < MAX_NODOS_CONTEXTO + 5:
                        elegidos.append(i)

    prereq = {}
    if intenciones["prerrequisitos"]:
        for s in semillas:
            prereq[s] = prerrequisitos(snap, s)
            for i in prereq[s]:
                if i not in elegidos:
                    elegidos.append(i)

    conjunto = set(elegidos)
    return {
        "semillas": semillas,
        "nodos": {i: snap["nodos"][i] for i in elegidos},
        "aristas": [a for a in snap["aristas"] if a["origen"] in conjunto and a["destino"] in conjunto],
        "rutas": rutas,
        "prerrequisitos": prereq,
        "intenciones": intenciones,
    }


def aristas_para_mapa(snap, ids, rutas=()):
    """Aristas (formato Vis.js) entre los ids dados, más las de las rutas."""
    conjunto = set(ids)
    usadas = {}
    for a in snap["aristas"]:
        if a["origen"] in conjunto and a["destino"] in conjunto:
            usadas[(a["origen"], a["destino"], a["tipo"])] = a
    for ruta in rutas:
        for a in aristas_de_ruta(snap, ruta):
            usadas[(a["origen"], a["destino"], a["tipo"])] = a
    return [{"from": a["origen"], "to": a["destino"], "label": a["tipo"]} for a in usadas.values()]
