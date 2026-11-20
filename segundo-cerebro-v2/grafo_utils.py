"""
Utilidades puras sobre una «foto» (snapshot) del grafo.

Un snapshot es un diccionario:
    {
      "nodos":   {id: {"id", "nombre", "descripcion", "categoria", "estado", ...}},
      "aristas": [{"origen", "destino", "tipo", "estado", ...}],
    }

Nada de este módulo habla con Neo4j ni con la IA: recibe datos y devuelve
datos. Por eso es fácil de probar y se reutiliza en el asistente, las
sugerencias y las recomendaciones de estudio.
"""
import re
import unicodedata
from collections import deque

ESTADOS_VERIFICACION = ("pendiente", "verificado", "requiere_revision", "contradicho")

# Tipos de relación que se leen como «el origen depende del destino».
# Se usan para calcular prerrequisitos y orden de estudio.
# Ejemplo: Flask -[se basa en]-> Python  =>  conviene ver Python antes que Flask.
TIPOS_DEPENDENCIA = frozenset({
    "se basa en", "usa", "requiere", "necesita", "depende de",
    "consume", "es un tipo de", "esta desarrollado en", "esta construido sobre",
})

PALABRAS_VACIAS = frozenset("""
    a al algo ante antes como con contra cual cuales cuando de del desde donde el ella ellos en entre
    era es esa ese eso esta este esto estos fue ha hay la las le les lo los mas me mi mis muy no nos
    o para pero por que quien se ser si sin sobre su sus te tu tus un una uno unas unos y ya que
    cual cuales como cuales explicame dime necesito quiero saber aprender estudiar conceptos concepto
    relacionan relaciona relacion conexiones conexion conectados conectado camino ruta mas corto
    the and for with from that this una usa usan hace hacen sirve
""".split())


def normalizar(texto):
    """Minúsculas, sin acentos ni signos de puntuación, espacios simples."""
    if not isinstance(texto, str):
        return ""
    t = unicodedata.normalize("NFKD", texto)
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = re.sub(r"[^\w\s]", "", t)          # «Vis.js» -> «visjs»
    return re.sub(r"\s+", " ", t).strip()


def tokens_significativos(texto, minimo=4):
    """Palabras normalizadas, sin palabras vacías, de al menos `minimo` letras."""
    return {
        p for p in normalizar(texto).split()
        if len(p) >= minimo and p not in PALABRAS_VACIAS
    }


def menciona(texto, nombre):
    """¿Aparece `nombre` como palabra completa (admite plural simple) en `texto`?"""
    n, t = normalizar(nombre), normalizar(texto)
    if len(n) < 2 or not t:
        return False
    return re.search(rf"(?<!\w){re.escape(n)}(?:s|es)?(?!\w)", t) is not None


def etiqueta_estado(valor):
    """Devuelve un estado de verificación válido (por defecto, «pendiente»)."""
    return valor if valor in ESTADOS_VERIFICACION else "pendiente"


# --------------------------------------------------------------------------
# Construcción del snapshot a partir de las filas de Neo4j
# --------------------------------------------------------------------------
def construir_snapshot(filas_nodos, filas_aristas):
    nodos = {}
    for fila in filas_nodos:
        n = fila["n"] if "n" in fila else fila
        if not n.get("id"):
            continue
        nodos[n["id"]] = {
            "id": n["id"],
            "nombre": n.get("nombre") or n["id"],
            "descripcion": n.get("descripcion") or "",
            "categoria": n.get("categoria") or "General",
            "estado": etiqueta_estado(n.get("verificacion_estado")),
            "fuente": n.get("verificacion_fuente"),
            "url": n.get("verificacion_url"),
        }
    aristas = []
    for f in filas_aristas:
        if f.get("origen") in nodos and f.get("destino") in nodos:
            aristas.append({
                "origen": f["origen"],
                "destino": f["destino"],
                "tipo": f.get("tipo") or "",
                "estado": etiqueta_estado(f.get("estado")),
                "creada_por": f.get("creada_por"),
            })
    return {"nodos": nodos, "aristas": aristas}


# --------------------------------------------------------------------------
# Vecindad, caminos y prerrequisitos
# --------------------------------------------------------------------------
def _adyacencia(snap, dirigida=False):
    ady = {i: [] for i in snap["nodos"]}
    for a in snap["aristas"]:
        ady[a["origen"]].append((a["destino"], a))
        if not dirigida:
            ady[a["destino"]].append((a["origen"], a))
    return ady


def grado(snap):
    """Número de relaciones (entrantes + salientes) de cada nodo."""
    g = {i: 0 for i in snap["nodos"]}
    for a in snap["aristas"]:
        g[a["origen"]] += 1
        g[a["destino"]] += 1
    return g


def vecinos(snap, nodo_id, saltos=1):
    """Ids a distancia 1..saltos (en cualquier dirección), con su distancia."""
    ady = _adyacencia(snap)
    dist = {nodo_id: 0}
    cola = deque([nodo_id])
    while cola:
        actual = cola.popleft()
        if dist[actual] >= saltos:
            continue
        for otro, _ in ady.get(actual, []):
            if otro not in dist:
                dist[otro] = dist[actual] + 1
                cola.append(otro)
    dist.pop(nodo_id)
    return dist


def camino_mas_corto(snap, origen, destino, max_saltos=6):
    """
    Camino más corto sin importar la dirección de las flechas (equivale a
    shortestPath((a)-[*..6]-(b)) de Cypher). Devuelve la lista de ids o None.
    """
    if origen not in snap["nodos"] or destino not in snap["nodos"]:
        return None
    if origen == destino:
        return [origen]
    ady = _adyacencia(snap)
    previo = {origen: None}
    cola = deque([(origen, 0)])
    while cola:
        actual, d = cola.popleft()
        if d >= max_saltos:
            continue
        for otro, _ in ady[actual]:
            if otro in previo:
                continue
            previo[otro] = actual
            if otro == destino:
                ruta = [otro]
                while previo[ruta[-1]] is not None:
                    ruta.append(previo[ruta[-1]])
                return ruta[::-1]
            cola.append((otro, d + 1))
    return None


def aristas_de_ruta(snap, ruta):
    """Las aristas reales que unen cada par consecutivo de la ruta."""
    salida = []
    for a, b in zip(ruta, ruta[1:]):
        for ar in snap["aristas"]:
            if {ar["origen"], ar["destino"]} == {a, b}:
                salida.append(ar)
                break
    return salida


def _es_dependencia(arista):
    return normalizar(arista["tipo"]) in TIPOS_DEPENDENCIA


def prerrequisitos(snap, nodo_id, max_saltos=3):
    """
    Conceptos de los que depende `nodo_id` siguiendo relaciones de dependencia
    (flecha origen -> destino). Devuelve una lista de ids ordenada para
    estudiar: primero los más básicos (los más lejanos), al final los directos.
    """
    if nodo_id not in snap["nodos"]:
        return []
    ady = _adyacencia(snap, dirigida=True)
    dist = {nodo_id: 0}
    cola = deque([nodo_id])
    while cola:
        actual = cola.popleft()
        if dist[actual] >= max_saltos:
            continue
        for otro, ar in ady[actual]:
            if _es_dependencia(ar) and otro not in dist:
                dist[otro] = dist[actual] + 1
                cola.append(otro)
    dist.pop(nodo_id)
    return sorted(dist, key=lambda i: (-dist[i], snap["nodos"][i]["nombre"]))


def orden_de_estudio(snap):
    """
    Orden topológico (algoritmo de Kahn) sobre las relaciones de dependencia.
    Devuelve (lista de {id, nivel}, ids_en_ciclo). Solo incluye conceptos que
    participan en alguna relación de dependencia.
    """
    deps = {i: set() for i in snap["nodos"]}      # i depende de ...
    for a in snap["aristas"]:
        if _es_dependencia(a) and a["origen"] != a["destino"]:
            deps[a["origen"]].add(a["destino"])
    participan = {i for i, d in deps.items() if d} | {x for d in deps.values() for x in d}
    pendientes = {i: set(deps[i]) & participan for i in participan}
    nivel, orden = {}, []
    actual = 0
    while pendientes:
        libres = sorted(
            (i for i, d in pendientes.items() if not d),
            key=lambda i: snap["nodos"][i]["nombre"],
        )
        if not libres:
            break
        for i in libres:
            nivel[i] = actual
            orden.append({"id": i, "nivel": actual})
            del pendientes[i]
        for d in pendientes.values():
            d.difference_update(libres)
        actual += 1
    return orden, sorted(pendientes)


def cadenas_de_aprendizaje(snap, maximo=5):
    """
    Rutas «base -> ... -> concepto»: para cada concepto con prerrequisitos, la
    cadena de dependencias más larga que termina en él.
    """
    deps = {i: [] for i in snap["nodos"]}
    for a in snap["aristas"]:
        if _es_dependencia(a):
            deps[a["origen"]].append(a["destino"])

    memo = {}

    def mas_larga(i, visitando):
        if i in memo:
            return memo[i]
        mejor = [i]
        for d in sorted(deps[i]):
            if d in visitando:
                continue
            cand = mas_larga(d, visitando | {i}) + [i]
            if len(cand) > len(mejor):
                mejor = cand
        memo[i] = mejor
        return mejor

    cadenas = [mas_larga(i, frozenset()) for i in snap["nodos"] if deps[i]]
    cadenas = [c for c in cadenas if len(c) >= 2]
    # Sin duplicados ni cadenas contenidas dentro de otras más largas.
    cadenas.sort(key=lambda c: (-len(c), c))
    unicas = []
    for c in cadenas:
        txt = "|".join(c)
        if not any(txt in "|".join(u) for u in unicas):
            unicas.append(c)
    return unicas[:maximo]
