"""Piezas falsas para las pruebas: Neo4j en memoria, IA simulada."""
import copy

from ia_cliente import ErrorIA

CONCEPTOS = [
    ("python", "Python", "Lenguaje", "Lenguaje de programación versátil y legible, muy usado en web, datos y automatización."),
    ("flask", "Flask", "Framework", "Microframework web de Python para crear aplicaciones y APIs de forma sencilla."),
    ("neo4j", "Neo4j", "Base de datos", "Base de datos orientada a grafos que almacena nodos y relaciones como ciudadanos de primera clase."),
    ("bases-de-datos", "Bases de Datos", "Fundamentos", "Sistemas organizados para almacenar, consultar y gestionar información de forma persistente."),
    ("grafos", "Grafos", "Fundamentos", "Estructura matemática formada por nodos (vértices) conectados por relaciones (aristas)."),
    ("sql", "SQL", "Lenguaje", "Lenguaje estándar para consultar y manipular bases de datos relacionales."),
    ("api-rest", "API REST", "Arquitectura", "Estilo de arquitectura para servicios web que usa HTTP y recursos identificados por URL."),
    ("json", "JSON", "Formato", "Formato ligero de texto para intercambiar datos estructurados entre sistemas."),
    ("cypher", "Cypher", "Lenguaje", "Lenguaje declarativo de consultas para grafos, con sintaxis que dibuja patrones en ASCII."),
    ("visjs", "Vis.js", "Librería", "Librería JavaScript para visualizar redes y grafos interactivos en el navegador."),
]
RELACIONES = [
    ("flask", "python", "se basa en"), ("python", "neo4j", "se conecta a"), ("flask", "api-rest", "expone"),
    ("api-rest", "json", "usa"), ("neo4j", "bases-de-datos", "es un tipo de"), ("neo4j", "grafos", "se basa en"),
    ("cypher", "neo4j", "consulta"), ("sql", "bases-de-datos", "consulta"), ("cypher", "sql", "se inspira en"),
    ("visjs", "grafos", "visualiza"), ("visjs", "json", "consume"),
]


class Contadores:
    def __init__(self, creadas=0):
        self.relationships_created = creadas


class FalsaNeo4j:
    """Interpreta solo las consultas que usa la app (no valida sintaxis Cypher)."""

    def __init__(self):
        self.nodos = {i: {"id": i, "nombre": n, "categoria": c, "descripcion": d} for i, n, c, d in CONCEPTOS}
        self.aristas = [{"origen": o, "destino": d, "tipo": t} for o, d, t in RELACIONES]
        self.escrituras = []          # registro de las escrituras (para comprobar que la IA no escribe)
        self.caida = False

    def _arista(self, o, d, t):
        return next((a for a in self.aristas if (a["origen"], a["destino"], a["tipo"]) == (o, d, t)), None)

    def consultar(self, q, p=None):
        from neo4j.exceptions import ServiceUnavailable
        if self.caida:
            raise ServiceUnavailable("sin conexión")
        if q.strip() == "MATCH (n:Concepto) RETURN n":
            return [{"n": copy.deepcopy(n)} for n in self.nodos.values()]
        if "r.verificacion_estado AS estado, r.origen_creacion AS creada_por" in q:
            return [{"origen": a["origen"], "destino": a["destino"], "tipo": a["tipo"],
                     "estado": a.get("verificacion_estado"), "creada_por": a.get("origen_creacion")}
                    for a in self.aristas]
        if q.startswith("MATCH (n:Concepto) WHERE n.verificacion_fecha"):
            return [{"id": n["id"], "nombre": n["nombre"], "estado": n.get("verificacion_estado"),
                     "fuente": n.get("verificacion_fuente"), "url": n.get("verificacion_url"),
                     "evidencia": n.get("verificacion_evidencia"), "fecha": n["verificacion_fecha"]}
                    for n in self.nodos.values() if n.get("verificacion_fecha")]
        if q.startswith("MATCH (a:Concepto)-[r:RELACIONADO_CON]->(b:Concepto) WHERE r.verificacion_fecha"):
            return [{"origen": a["origen"], "origen_nombre": self.nodos[a["origen"]]["nombre"],
                     "destino": a["destino"], "destino_nombre": self.nodos[a["destino"]]["nombre"],
                     "tipo": a["tipo"], "estado": a.get("verificacion_estado"),
                     "fuente": a.get("verificacion_fuente"), "url": a.get("verificacion_url"),
                     "evidencia": a.get("verificacion_evidencia"), "fecha": a["verificacion_fecha"]}
                    for a in self.aristas if a.get("verificacion_fecha")]
        raise AssertionError(f"Consulta de lectura no prevista: {q}")

    def escribir(self, q, p=None):
        from neo4j.exceptions import ServiceUnavailable
        if self.caida:
            raise ServiceUnavailable("sin conexión")
        self.escrituras.append((q, p))
        if q.startswith("CREATE (c:Concepto"):
            self.nodos[p["id"]] = {k: p[k] for k in ("id", "nombre", "descripcion", "categoria")}
            return [], Contadores()
        if "ON CREATE SET r.origen_creacion" in q:
            if p["origen"] not in self.nodos or p["destino"] not in self.nodos:
                return [], Contadores()
            if self._arista(p["origen"], p["destino"], p["tipo"]):
                nueva = 0
            else:
                self.aristas.append({"origen": p["origen"], "destino": p["destino"], "tipo": p["tipo"],
                                     "origen_creacion": "ia_aprobada", "justificacion": p["justificacion"],
                                     "confianza_ia": p["confianza"], "verificacion_estado": "pendiente"})
                nueva = 1
            return [{"origen": p["origen"], "destino": p["destino"], "tipo": p["tipo"]}], Contadores(nueva)
        if q.startswith("MERGE") or "MERGE (a)-[r:RELACIONADO_CON {tipo: $tipo}]->(b) RETURN" in q:
            if p["origen"] not in self.nodos or p["destino"] not in self.nodos:
                return [], Contadores()
            nueva = 0 if self._arista(p["origen"], p["destino"], p["tipo"]) else 1
            if nueva:
                self.aristas.append({"origen": p["origen"], "destino": p["destino"], "tipo": p["tipo"]})
            return [{"origen": p["origen"], "destino": p["destino"], "tipo": p["tipo"]}], Contadores(nueva)
        if q.startswith("MATCH (n:Concepto {id: $id}) SET n.verificacion_estado"):
            n = self.nodos.get(p["id"])
            if not n:
                return [], Contadores()
            for k in ("estado", "fuente", "url", "evidencia", "fecha"):
                n[f"verificacion_{k}"] = p[k]
            return [{"id": n["id"]}], Contadores()
        if "SET r.verificacion_estado" in q:
            a = self._arista(p["origen"], p["destino"], p["tipo"])
            if a:
                for k in ("estado", "fuente", "url", "evidencia", "fecha"):
                    a[f"verificacion_{k}"] = p[k]
            return [{"total": 1 if a else 0}], Contadores()
        raise AssertionError(f"Consulta de escritura no prevista: {q}")


class IAFalsa:
    """IA simulada: devuelve respuestas programadas y guarda lo que recibió."""
    disponible = True
    modelo = "falso"

    def __init__(self, respuestas=None, error=None):
        self.respuestas = list(respuestas or [])
        self.error = error
        self.llamadas = []

    def json(self, sistema, usuario, max_tokens=None):
        self.llamadas.append({"sistema": sistema, "usuario": usuario})
        if self.error:
            raise self.error
        return self.respuestas.pop(0)


class SinIA:
    disponible = False
    modelo = None

    def json(self, *a, **k):
        raise ErrorIA("no_configurada", "sin IA")


