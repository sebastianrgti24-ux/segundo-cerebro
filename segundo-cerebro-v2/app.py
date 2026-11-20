"""
Segundo Cerebro v2: Gestor de Conceptos Vinculados con IA
Flask + Neo4j Aura + Vis.js Network + asistente IA (RAG sobre el grafo)

La IA NUNCA ejecuta consultas ni escribe en Neo4j: Flask controla todas las
consultas (siempre parametrizadas) y solo guarda lo que el estudiante aprueba.
"""
import atexit
import functools
import os
import sys
import uuid
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from neo4j import GraphDatabase
from neo4j.exceptions import AuthError, Neo4jError, ServiceUnavailable

import asistente
import estudio
import sugerencias
import verificacion
from grafo_utils import construir_snapshot, normalizar
from ia_cliente import ClienteIA
from limites import LimitadorVentana

load_dotenv()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024   # las peticiones son pequeñas: se rechaza lo demás
driver = None  # Se crea en iniciar()
ia = ClienteIA.desde_entorno()   # cliente de IA (la clave solo vive en el servidor)

# Límites de longitud para los textos recibidos
MAX_NOMBRE = 100
MAX_DESCRIPCION = 1000
MAX_CATEGORIA = 50
MAX_TIPO = 60
MAX_PREGUNTA = 500
MAX_ID = 100
MAX_JUSTIFICACION = 400

# Límites de solicitudes por IP (por minuto). Se pueden ajustar desde el .env.
LIMITE_IA = int(os.getenv("LIMITE_IA_POR_MINUTO") or 12)
LIMITE_GENERAL = int(os.getenv("LIMITE_GENERAL_POR_MINUTO") or 120)
limitadores = {
    "ia": LimitadorVentana(LIMITE_IA, 60),
    "general": LimitadorVentana(LIMITE_GENERAL, 60),
}

# Cliente HTTP para consultar fuentes. Se puede sustituir en las pruebas.
http_fuentes = requests


class ErrorInicio(Exception):
    """Error al arrancar la app (configuración o conexión)."""


# --------------------------------------------------------------------------
# Conexión
# --------------------------------------------------------------------------
def cerrar_driver():
    """Cierra el driver al terminar la app."""
    global driver
    if driver is not None:
        driver.close()
        driver = None


def crear_driver():
    """Lee las credenciales del .env, crea el driver y verifica la conexión."""
    global driver

    uri = os.getenv("NEO4J_URI")
    usuario = os.getenv("NEO4J_USER") or os.getenv("NEO4J_USERNAME")
    password = os.getenv("NEO4J_PASSWORD")

    faltan = [
        nombre
        for nombre, valor in (
            ("NEO4J_URI", uri),
            ("NEO4J_USER", usuario),
            ("NEO4J_PASSWORD", password),
        )
        if not valor
    ]
    if faltan:
        raise ErrorInicio(
            "Faltan variables en el archivo .env: "
            + ", ".join(faltan)
            + ".\nCopia .env.example como .env y rellénalo con los datos de Aura."
        )

    if not uri.startswith("neo4j+s://"):
        raise ErrorInicio(
            f"La URI '{uri}' no es válida para Aura.\n"
            "Debe empezar por 'neo4j+s://', por ejemplo: "
            "neo4j+s://xxxxxxxx.databases.neo4j.io"
        )

    driver = GraphDatabase.driver(uri, auth=(usuario, password))
    atexit.register(cerrar_driver)

    try:
        driver.verify_connectivity()
    except AuthError:
        cerrar_driver()
        raise ErrorInicio(
            "Autenticación rechazada por Neo4j Aura: usuario o contraseña incorrectos.\n"
            "Revisa NEO4J_USER y NEO4J_PASSWORD en tu .env."
        )
    except ServiceUnavailable:
        cerrar_driver()
        raise ErrorInicio(
            "No se pudo conectar con Neo4j Aura.\n"
            "Causas probables: la instancia está pausada (reanúdala desde la consola de Aura "
            "y espera un par de minutos), la URI es incorrecta o no hay conexión a internet."
        )
    except Exception as e:
        cerrar_driver()
        raise ErrorInicio(f"Error inesperado al conectar con Neo4j: {e}")


# --------------------------------------------------------------------------
# Acceso a datos (siempre con consultas parametrizadas)
# --------------------------------------------------------------------------
def consultar(query, params=None):
    """Ejecuta una lectura y devuelve una lista de diccionarios."""
    def trabajo(tx):
        return [r.data() for r in tx.run(query, params or {})]

    with driver.session() as sesion:
        return sesion.execute_read(trabajo)


def escribir(query, params=None):
    """Ejecuta una escritura y devuelve (registros, contadores)."""
    def trabajo(tx):
        resultado = tx.run(query, params or {})
        registros = [r.data() for r in resultado]
        return registros, resultado.consume().counters

    with driver.session() as sesion:
        return sesion.execute_write(trabajo)


def crear_restriccion():
    escribir(
        "CREATE CONSTRAINT concepto_id_unico IF NOT EXISTS "
        "FOR (c:Concepto) REQUIRE c.id IS UNIQUE"
    )


def ahora_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def cargar_snapshot():
    """
    Foto del grafo para el asistente, las sugerencias y las recomendaciones.
    Son dos consultas separadas (como en /api/grafo) para que los nodos sin
    relaciones también aparezcan. Los conceptos y relaciones de la v1 no tienen
    propiedades de verificación: se leen como «pendiente», sin migrar nada.
    """
    filas_nodos = consultar("MATCH (n:Concepto) RETURN n")
    filas_aristas = consultar(
        "MATCH (a:Concepto)-[r]->(b:Concepto) "
        "RETURN a.id AS origen, b.id AS destino, r.tipo AS tipo, "
        "r.verificacion_estado AS estado, r.origen_creacion AS creada_por"
    )
    return construir_snapshot(filas_nodos, filas_aristas)


def poblar_datos():
    """Carga datos de ejemplo solo si no existe ningún nodo :Concepto."""
    total = consultar("MATCH (n:Concepto) RETURN count(n) AS total")[0]["total"]
    if total > 0:
        return False

    conceptos = [
        {"id": "python", "nombre": "Python", "categoria": "Lenguaje",
         "descripcion": "Lenguaje de programación versátil y legible, muy usado en web, datos y automatización."},
        {"id": "flask", "nombre": "Flask", "categoria": "Framework",
         "descripcion": "Microframework web de Python para crear aplicaciones y APIs de forma sencilla."},
        {"id": "neo4j", "nombre": "Neo4j", "categoria": "Base de datos",
         "descripcion": "Base de datos orientada a grafos que almacena nodos y relaciones como ciudadanos de primera clase."},
        {"id": "bases-de-datos", "nombre": "Bases de Datos", "categoria": "Fundamentos",
         "descripcion": "Sistemas organizados para almacenar, consultar y gestionar información de forma persistente."},
        {"id": "grafos", "nombre": "Grafos", "categoria": "Fundamentos",
         "descripcion": "Estructura matemática formada por nodos (vértices) conectados por relaciones (aristas)."},
        {"id": "sql", "nombre": "SQL", "categoria": "Lenguaje",
         "descripcion": "Lenguaje estándar para consultar y manipular bases de datos relacionales."},
        {"id": "api-rest", "nombre": "API REST", "categoria": "Arquitectura",
         "descripcion": "Estilo de arquitectura para servicios web que usa HTTP y recursos identificados por URL."},
        {"id": "json", "nombre": "JSON", "categoria": "Formato",
         "descripcion": "Formato ligero de texto para intercambiar datos estructurados entre sistemas."},
        {"id": "cypher", "nombre": "Cypher", "categoria": "Lenguaje",
         "descripcion": "Lenguaje declarativo de consultas para grafos, con sintaxis que dibuja patrones en ASCII."},
        {"id": "visjs", "nombre": "Vis.js", "categoria": "Librería",
         "descripcion": "Librería JavaScript para visualizar redes y grafos interactivos en el navegador."},
    ]

    relaciones = [
        {"origen": "flask", "destino": "python", "tipo": "se basa en"},
        {"origen": "python", "destino": "neo4j", "tipo": "se conecta a"},
        {"origen": "flask", "destino": "api-rest", "tipo": "expone"},
        {"origen": "api-rest", "destino": "json", "tipo": "usa"},
        {"origen": "neo4j", "destino": "bases-de-datos", "tipo": "es un tipo de"},
        {"origen": "neo4j", "destino": "grafos", "tipo": "se basa en"},
        {"origen": "cypher", "destino": "neo4j", "tipo": "consulta"},
        {"origen": "sql", "destino": "bases-de-datos", "tipo": "consulta"},
        {"origen": "cypher", "destino": "sql", "tipo": "se inspira en"},
        {"origen": "visjs", "destino": "grafos", "tipo": "visualiza"},
        {"origen": "visjs", "destino": "json", "tipo": "consume"},
    ]

    escribir(
        "UNWIND $conceptos AS c "
        "MERGE (n:Concepto {id: c.id}) "
        "SET n.nombre = c.nombre, n.descripcion = c.descripcion, n.categoria = c.categoria",
        {"conceptos": conceptos},
    )
    escribir(
        "UNWIND $relaciones AS r "
        "MATCH (a:Concepto {id: r.origen}), (b:Concepto {id: r.destino}) "
        "MERGE (a)-[:RELACIONADO_CON {tipo: r.tipo}]->(b)",
        {"relaciones": relaciones},
    )
    return True


def iniciar():
    """Conecta, crea la restricción y siembra datos de ejemplo si procede."""
    crear_driver()
    try:
        crear_restriccion()
        if poblar_datos():
            print("✔ Base vacía de conceptos: se cargaron datos de ejemplo.")
        else:
            print("✔ Ya existen conceptos: no se cargaron datos de ejemplo.")
    except Neo4jError as e:
        cerrar_driver()
        raise ErrorInicio(f"Error de Neo4j al preparar la base de datos: {e.message}")

    if ia.disponible:
        print(f"✔ IA activada (modelo: {ia.modelo}). Las llamadas a la API pueden tener costo.")
    else:
        print("ℹ IA no configurada: el asistente y las sugerencias funcionan en «modo sin IA». "
              "Para activarla, agrega OPENAI_API_KEY al archivo .env.")


# --------------------------------------------------------------------------
# Utilidades de las rutas
# --------------------------------------------------------------------------
def limpiar(valor, maximo):
    """Devuelve el texto sin espacios sobrantes (o '' si no es texto)."""
    return valor.strip()[:maximo] if isinstance(valor, str) else ""


def error_json(mensaje, codigo):
    return jsonify({"error": mensaje}), codigo


def error_de_base_de_datos(e):
    """Traduce una excepción del driver a una respuesta JSON adecuada."""
    if isinstance(e, ServiceUnavailable):
        return error_json(
            "No hay conexión con Neo4j Aura. ¿La instancia está pausada o sin red?", 503
        )
    if isinstance(e, Neo4jError):
        app.logger.error("Error de Neo4j: %s", e)
        return error_json("Error en la base de datos.", 500)
    app.logger.exception("Error inesperado")
    return error_json("Error interno del servidor.", 500)


def limitar(nombre):
    """Decorador: limita las solicitudes por IP. Devuelve 429 con Retry-After."""
    def decorador(f):
        @functools.wraps(f)
        def envoltura(*args, **kwargs):
            ok, espera = limitadores[nombre].permitir(request.remote_addr or "?")
            if not ok:
                resp, codigo = error_json(
                    f"Demasiadas solicitudes. Espera {espera} segundos e inténtalo de nuevo.", 429)
                resp.headers["Retry-After"] = str(espera)
                return resp, codigo
            return f(*args, **kwargs)
        return envoltura
    return decorador


def cuerpo_json():
    """JSON del cuerpo (dict) o None."""
    datos = request.get_json(silent=True)
    return datos if isinstance(datos, dict) else None


@app.errorhandler(413)
def demasiado_grande(_e):
    return error_json("La solicitud es demasiado grande.", 413)


@app.errorhandler(404)
def no_encontrado(_e):
    if request.path.startswith("/api/"):
        return error_json("Ruta no encontrada.", 404)
    return "No encontrado", 404


@app.errorhandler(405)
def metodo_no_permitido(_e):
    return error_json("Método no permitido.", 405)


# --------------------------------------------------------------------------
# Rutas
# --------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/grafo")
@limitar("general")
def api_grafo():
    try:
        # Dos consultas separadas: así los nodos sin relaciones también aparecen.
        snap = cargar_snapshot()

        nodes = [{
            "id": n["id"],
            "label": n["nombre"],
            "title": f"{n['nombre']}\n{n['descripcion']}".strip(),
            "group": n["categoria"],
            "descripcion": n["descripcion"],
            "categoria": n["categoria"],
            "estado": n["estado"],          # v2: estado de verificación
        } for n in snap["nodos"].values()]

        edges = [
            {"from": a["origen"], "to": a["destino"], "label": a["tipo"],
             "estado": a["estado"], "creada_por": a["creada_por"]}
            for a in snap["aristas"]
        ]
        return jsonify({"nodes": nodes, "edges": edges})
    except Exception as e:
        return error_de_base_de_datos(e)


@app.route("/api/concepto", methods=["POST"])
@limitar("general")
def api_concepto():
    datos = request.get_json(silent=True)
    if not isinstance(datos, dict):
        return error_json("El cuerpo debe ser JSON.", 400)

    nombre = limpiar(datos.get("nombre"), MAX_NOMBRE)
    descripcion = limpiar(datos.get("descripcion"), MAX_DESCRIPCION)
    categoria = limpiar(datos.get("categoria"), MAX_CATEGORIA) or "General"

    if not nombre:
        return error_json("El nombre es obligatorio.", 400)

    nuevo_id = uuid.uuid4().hex
    try:
        escribir(
            "CREATE (c:Concepto {id: $id, nombre: $nombre, "
            "descripcion: $descripcion, categoria: $categoria})",
            {"id": nuevo_id, "nombre": nombre,
             "descripcion": descripcion, "categoria": categoria},
        )
        return jsonify({
            "id": nuevo_id, "nombre": nombre,
            "descripcion": descripcion, "categoria": categoria,
        }), 201
    except Exception as e:
        return error_de_base_de_datos(e)


@app.route("/api/relacion", methods=["POST"])
@limitar("general")
def api_relacion():
    datos = request.get_json(silent=True)
    if not isinstance(datos, dict):
        return error_json("El cuerpo debe ser JSON.", 400)

    origen = limpiar(datos.get("origen"), 100)
    destino = limpiar(datos.get("destino"), 100)
    tipo = limpiar(datos.get("tipo"), MAX_TIPO)

    if not origen or not destino or not tipo:
        return error_json("Origen, destino y tipo son obligatorios.", 400)
    if origen == destino:
        return error_json("Un concepto no puede relacionarse consigo mismo.", 400)

    try:
        registros, contadores = escribir(
            "MATCH (a:Concepto {id: $origen}), (b:Concepto {id: $destino}) "
            "MERGE (a)-[r:RELACIONADO_CON {tipo: $tipo}]->(b) "
            "RETURN a.id AS origen, b.id AS destino, r.tipo AS tipo",
            {"origen": origen, "destino": destino, "tipo": tipo},
        )
        if not registros:
            return error_json("Alguno de los dos conceptos no existe.", 404)

        creada = contadores.relationships_created > 0
        return jsonify({**registros[0], "creada": creada}), (201 if creada else 200)
    except Exception as e:
        return error_de_base_de_datos(e)


# ==========================================================================
# v2: IA, sugerencias, verificación y recomendaciones
# ==========================================================================
def texto_id(valor):
    """Id válido (texto no vacío de hasta MAX_ID caracteres) o None."""
    v = limpiar(valor, MAX_ID)
    return v or None


def aviso_limite_ia():
    """Si se superó el límite de llamadas a la IA, devuelve la respuesta 429; si no, None."""
    ok, espera = limitadores["ia"].permitir(request.remote_addr or "?")
    if ok:
        return None
    resp, codigo = error_json(f"Demasiadas consultas a la IA. Espera {espera} segundos.", 429)
    resp.headers["Retry-After"] = str(espera)
    return resp, codigo


@app.route("/api/estado-ia")
def api_estado_ia():
    """Le dice a la interfaz si hay IA configurada (nunca expone la clave)."""
    return jsonify({"disponible": ia.disponible, "modelo": ia.modelo if ia.disponible else None})


@app.route("/api/chat", methods=["POST"])
@limitar("ia")
def api_chat():
    datos = cuerpo_json()
    if datos is None:
        return error_json("El cuerpo debe ser JSON.", 400)
    pregunta = limpiar(datos.get("pregunta"), MAX_PREGUNTA)
    if not pregunta:
        return error_json("Escribe una pregunta.", 400)
    try:
        snap = cargar_snapshot()
        # El historial viene del navegador y no se guarda en el servidor.
        return jsonify(asistente.responder(snap, pregunta, datos.get("historial"), ia))
    except Exception as e:
        return error_de_base_de_datos(e)


@app.route("/api/sugerir-relaciones", methods=["POST"])
@limitar("ia")
def api_sugerir_relaciones():
    datos = cuerpo_json()
    if datos is None:
        return error_json("El cuerpo debe ser JSON.", 400)
    concepto_id = texto_id(datos.get("concepto_id"))
    if not concepto_id:
        return error_json("Indica el concepto (concepto_id).", 400)
    try:
        snap = cargar_snapshot()
        if concepto_id not in snap["nodos"]:
            return error_json("El concepto no existe.", 404)
        return jsonify(sugerencias.sugerir(snap, concepto_id, ia))
    except Exception as e:
        return error_de_base_de_datos(e)


@app.route("/api/aceptar-relacion", methods=["POST"])
@limitar("general")
def api_aceptar_relacion():
    """Guarda UNA relación sugerida que el estudiante aprobó. Es la única vía de escritura de las sugerencias."""
    datos = cuerpo_json()
    if datos is None:
        return error_json("El cuerpo debe ser JSON.", 400)
    origen, destino = texto_id(datos.get("origen")), texto_id(datos.get("destino"))
    tipo = " ".join(limpiar(datos.get("tipo"), MAX_TIPO).split())
    if not origen or not destino or not tipo:
        return error_json("Origen, destino y tipo son obligatorios.", 400)
    if origen == destino:
        return error_json("Un concepto no puede relacionarse consigo mismo.", 400)

    justificacion = limpiar(datos.get("justificacion"), MAX_JUSTIFICACION) or None
    confianza = datos.get("confianza") if datos.get("confianza") in sugerencias.CONFIANZAS else None
    respaldo = limpiar(datos.get("respaldo"), 150) or None

    try:
        snap = cargar_snapshot()
        if origen not in snap["nodos"] or destino not in snap["nodos"]:
            return error_json("Alguno de los dos conceptos no existe.", 404)
        # Evita duplicados aunque cambien mayúsculas o acentos en el tipo.
        if any(a["origen"] == origen and a["destino"] == destino and normalizar(a["tipo"]) == normalizar(tipo)
               for a in snap["aristas"]):
            return jsonify({"origen": origen, "destino": destino, "tipo": tipo, "creada": False,
                            "mensaje": "Esa relación ya existía."}), 200

        registros, contadores = escribir(
            "MATCH (a:Concepto {id: $origen}), (b:Concepto {id: $destino}) "
            "MERGE (a)-[r:RELACIONADO_CON {tipo: $tipo}]->(b) "
            "ON CREATE SET r.origen_creacion = 'ia_aprobada', r.justificacion = $justificacion, "
            "r.confianza_ia = $confianza, r.respaldo = $respaldo, r.fecha_creacion = $fecha, "
            "r.verificacion_estado = 'pendiente' "
            "RETURN a.id AS origen, b.id AS destino, r.tipo AS tipo",
            {"origen": origen, "destino": destino, "tipo": tipo, "justificacion": justificacion,
             "confianza": confianza, "respaldo": respaldo, "fecha": ahora_iso()},
        )
        if not registros:
            return error_json("Alguno de los dos conceptos no existe.", 404)
        creada = contadores.relationships_created > 0
        return jsonify({**registros[0], "creada": creada}), (201 if creada else 200)
    except Exception as e:
        return error_de_base_de_datos(e)


@app.route("/api/verificar", methods=["POST"])
@limitar("ia")
def api_verificar():
    """
    Busca evidencia y PROPONE un resultado. No guarda nada: guardar es una
    acción aparte (/api/aceptar-verificacion) que el estudiante confirma.
    """
    datos = cuerpo_json()
    if datos is None:
        return error_json("El cuerpo debe ser JSON.", 400)
    tipo_elemento = datos.get("tipo")
    if tipo_elemento not in ("concepto", "relacion"):
        return error_json("«tipo» debe ser «concepto» o «relacion».", 400)
    try:
        snap = cargar_snapshot()
        if tipo_elemento == "concepto":
            cid = texto_id(datos.get("id"))
            if not cid or cid not in snap["nodos"]:
                return error_json("El concepto no existe.", 404)
            return jsonify(verificacion.verificar_concepto(snap["nodos"][cid], ia, http_fuentes))

        origen, destino = texto_id(datos.get("origen")), texto_id(datos.get("destino"))
        rel = limpiar(datos.get("relacion_tipo"), MAX_TIPO)
        existe = any(a["origen"] == origen and a["destino"] == destino and a["tipo"] == rel for a in snap["aristas"])
        if not (origen and destino and rel) or not existe:
            return error_json("La relación no existe.", 404)
        return jsonify(verificacion.verificar_relacion(
            snap["nodos"][origen], snap["nodos"][destino], rel, ia, http_fuentes))
    except Exception as e:
        return error_de_base_de_datos(e)


@app.route("/api/aceptar-verificacion", methods=["POST"])
@limitar("general")
def api_aceptar_verificacion():
    """Guarda el resultado de verificación que el estudiante revisó y aceptó."""
    datos = cuerpo_json()
    if datos is None:
        return error_json("El cuerpo debe ser JSON.", 400)
    registro, error = verificacion.validar_registro(datos)
    if error:
        return error_json(error, 400)
    registro["fecha"] = ahora_iso()

    tipo_elemento = datos.get("tipo")
    try:
        if tipo_elemento == "concepto":
            cid = texto_id(datos.get("id"))
            if not cid:
                return error_json("Indica el concepto (id).", 400)
            registros, _ = escribir(
                "MATCH (n:Concepto {id: $id}) "
                "SET n.verificacion_estado = $estado, n.verificacion_fuente = $fuente, "
                "n.verificacion_url = $url, n.verificacion_evidencia = $evidencia, "
                "n.verificacion_fecha = $fecha "
                "RETURN n.id AS id",
                {"id": cid, **registro},
            )
            if not registros:
                return error_json("El concepto no existe.", 404)
            return jsonify({"ok": True, "estado": registro["estado"], "fecha": registro["fecha"]})

        if tipo_elemento == "relacion":
            origen, destino = texto_id(datos.get("origen")), texto_id(datos.get("destino"))
            rel = limpiar(datos.get("relacion_tipo"), MAX_TIPO)
            if not (origen and destino and rel):
                return error_json("Indica origen, destino y relacion_tipo.", 400)
            registros, _ = escribir(
                "MATCH (a:Concepto {id: $origen})-[r:RELACIONADO_CON {tipo: $tipo}]->(b:Concepto {id: $destino}) "
                "SET r.verificacion_estado = $estado, r.verificacion_fuente = $fuente, "
                "r.verificacion_url = $url, r.verificacion_evidencia = $evidencia, "
                "r.verificacion_fecha = $fecha "
                "RETURN count(r) AS total",
                {"origen": origen, "destino": destino, "tipo": rel, **registro},
            )
            if not registros or registros[0]["total"] == 0:
                return error_json("La relación no existe.", 404)
            return jsonify({"ok": True, "estado": registro["estado"], "fecha": registro["fecha"]})

        return error_json("«tipo» debe ser «concepto» o «relacion».", 400)
    except Exception as e:
        return error_de_base_de_datos(e)


@app.route("/api/fuentes")
@limitar("general")
def api_fuentes():
    """Fuentes guardadas (concepto y relaciones). Filtros opcionales: ?id= o ?origen=&destino=&tipo=."""
    try:
        conceptos = consultar(
            "MATCH (n:Concepto) WHERE n.verificacion_fecha IS NOT NULL "
            "RETURN n.id AS id, n.nombre AS nombre, n.verificacion_estado AS estado, "
            "n.verificacion_fuente AS fuente, n.verificacion_url AS url, "
            "n.verificacion_evidencia AS evidencia, n.verificacion_fecha AS fecha"
        )
        relaciones = consultar(
            "MATCH (a:Concepto)-[r:RELACIONADO_CON]->(b:Concepto) WHERE r.verificacion_fecha IS NOT NULL "
            "RETURN a.id AS origen, a.nombre AS origen_nombre, b.id AS destino, b.nombre AS destino_nombre, "
            "r.tipo AS tipo, r.verificacion_estado AS estado, r.verificacion_fuente AS fuente, "
            "r.verificacion_url AS url, r.verificacion_evidencia AS evidencia, r.verificacion_fecha AS fecha"
        )
        filtro_id = request.args.get("id")
        if filtro_id:
            conceptos = [c for c in conceptos if c["id"] == filtro_id]
            relaciones = []
        elif request.args.get("origen") and request.args.get("destino"):
            conceptos = []
            relaciones = [r for r in relaciones
                          if r["origen"] == request.args["origen"] and r["destino"] == request.args["destino"]
                          and (not request.args.get("tipo") or r["tipo"] == request.args["tipo"])]
        return jsonify({
            "conceptos": [{**c, "tipo_elemento": "concepto"} for c in conceptos],
            "relaciones": [{**r, "tipo_elemento": "relacion"} for r in relaciones],
        })
    except Exception as e:
        return error_de_base_de_datos(e)


@app.route("/api/recomendaciones")
@limitar("general")
def api_recomendaciones():
    """Recomendaciones de estudio. Con ?ia=1 se piden además conceptos nuevos a la IA."""
    pedir_ia = request.args.get("ia") == "1"
    if pedir_ia:
        bloqueo = aviso_limite_ia()
        if bloqueo:
            return bloqueo
    try:
        snap = cargar_snapshot()
        return jsonify(estudio.recomendaciones(snap, ia, pedir_ia=pedir_ia))
    except Exception as e:
        return error_de_base_de_datos(e)


# --------------------------------------------------------------------------
def entorno_es_desarrollo():
    """APP_ENTORNO=produccion desactiva el modo debug. Por defecto: desarrollo."""
    return (os.getenv("APP_ENTORNO") or "desarrollo").strip().lower() != "produccion"


if __name__ == "__main__":
    DEBUG = entorno_es_desarrollo()
    if not DEBUG:
        print("ℹ Modo producción: debug desactivado. Para publicar la app usa un servidor WSGI "
              "(por ejemplo waitress o gunicorn) en lugar de app.run().")
    # Con debug=True el recargador lanza dos procesos; solo el hijo
    # (WERKZEUG_RUN_MAIN="true") conecta, para no repetir la inicialización.
    if not DEBUG or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        try:
            iniciar()
        except ErrorInicio as e:
            print(f"\n✖ No se pudo iniciar Segundo Cerebro:\n{e}\n", file=sys.stderr)
            sys.exit(1)
    app.run(debug=DEBUG)
