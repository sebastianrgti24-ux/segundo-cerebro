import json
import unittest
from unittest.mock import MagicMock, patch

import app as segundo_cerebro

# Grafo de ejemplo (mismo que poblar_datos) para probar la recuperación sin Neo4j.
NODOS = [
    {"id": "python", "nombre": "Python", "categoria": "Lenguaje",
     "descripcion": "Lenguaje de programación versátil."},
    {"id": "flask", "nombre": "Flask", "categoria": "Framework",
     "descripcion": "Microframework web de Python."},
    {"id": "api-rest", "nombre": "API REST", "categoria": "Arquitectura",
     "descripcion": "Estilo de arquitectura para servicios web."},
    {"id": "json", "nombre": "JSON", "categoria": "Formato",
     "descripcion": "Formato ligero de texto."},
    {"id": "visjs", "nombre": "Vis.js", "categoria": "Librería",
     "descripcion": "Librería JavaScript para visualizar grafos."},
    {"id": "grafos", "nombre": "Grafos", "categoria": "Fundamentos",
     "descripcion": "Estructura matemática de nodos y aristas."},
    {"id": "neo4j", "nombre": "Neo4j", "categoria": "Base de datos",
     "descripcion": "Base de datos orientada a grafos."},
    {"id": "aislado", "nombre": "Rust", "categoria": "Lenguaje",
     "descripcion": "Lenguaje de sistemas."},
]
ARISTAS = [
    {"origen": "flask", "destino": "python", "tipo": "se basa en",
     "estado_verificacion": "Pendiente de verificación"},
    {"origen": "python", "destino": "neo4j", "tipo": "se conecta a",
     "estado_verificacion": "Pendiente de verificación"},
    {"origen": "flask", "destino": "api-rest", "tipo": "expone",
     "estado_verificacion": "Pendiente de verificación"},
    {"origen": "api-rest", "destino": "json", "tipo": "usa",
     "estado_verificacion": "Pendiente de verificación"},
    {"origen": "neo4j", "destino": "grafos", "tipo": "se basa en",
     "estado_verificacion": "Pendiente de verificación"},
    {"origen": "visjs", "destino": "grafos", "tipo": "visualiza",
     "estado_verificacion": "Pendiente de verificación"},
    {"origen": "visjs", "destino": "json", "tipo": "consume",
     "estado_verificacion": "Pendiente de verificación"},
]


def contexto_falso(**extra):
    base = {
        "nodos": [{"id": "python", "nombre": "Python"}],
        "relaciones": [],
        "ruta": [],
        "ruta_relaciones": [],
        "sin_ruta": [],
        "mencionados": ["python"],
    }
    base.update(extra)
    return base


class RecuperacionTests(unittest.TestCase):
    def test_detecta_mencionados_en_orden_de_la_pregunta(self):
        ids = segundo_cerebro.detectar_mencionados(
            "¿Cuál es el camino más corto entre Vis.js y Python?", NODOS
        )
        self.assertEqual(ids, ["visjs", "python"])

    def test_detecta_plural_y_sin_tildes(self):
        ids = segundo_cerebro.detectar_mencionados("explícame el grafo y las api rest", NODOS)
        self.assertEqual(ids, ["grafos", "api-rest"])

    def test_ruta_respeta_direccion_real_de_las_relaciones(self):
        ids, usadas = segundo_cerebro.ruta_mas_corta("python", "visjs", ARISTAS)
        self.assertEqual(ids[0], "python")
        self.assertEqual(ids[-1], "visjs")
        self.assertEqual(len(ids), 4)  # python-neo4j-grafos-visjs o python-flask-...
        for arista in usadas:
            self.assertIn(arista, ARISTAS)

    def test_sin_ruta_cuando_no_hay_conexion(self):
        self.assertEqual(segundo_cerebro.ruta_mas_corta("python", "aislado", ARISTAS),
                         (None, None))

    @patch("app.leer_grafo_completo", return_value=(NODOS, ARISTAS))
    def test_contexto_incluye_mencionados_y_ruta_encadenada(self, _):
        ctx = segundo_cerebro.recuperar_contexto(
            "¿Cómo se relacionan Python, Flask y las API REST?"
        )
        self.assertEqual(ctx["mencionados"], ["python", "flask", "api-rest"])
        self.assertEqual(ctx["ruta"], ["python", "flask", "api-rest"])
        ids = {n["id"] for n in ctx["nodos"]}
        self.assertTrue({"python", "flask", "api-rest"} <= ids)
        self.assertEqual([r["tipo"] for r in ctx["ruta_relaciones"]], ["se basa en", "expone"])

    @patch("app.leer_grafo_completo")
    def test_no_descarta_mencionados_al_recortar(self, leer):
        muchos = [{"id": f"n{i}", "nombre": f"Lenguaje {i}",
                   "descripcion": "lenguaje programacion"} for i in range(40)]
        leer.return_value = (muchos + NODOS, ARISTAS)
        ctx = segundo_cerebro.recuperar_contexto(
            "lenguaje de programación: ¿qué relación tiene Rust con Python?"
        )
        ids = [n["id"] for n in ctx["nodos"]]
        self.assertIn("aislado", ids)
        self.assertIn("python", ids)
        self.assertLessEqual(len(ids), segundo_cerebro.MAX_NODOS_CONTEXTO)
        self.assertEqual(ctx["sin_ruta"], [["Rust", "Python"]])

    @patch("app.leer_grafo_completo")
    def test_no_envia_evidencias_internas_al_modelo(self, leer):
        nodo = dict(NODOS[0], evidencia_verificacion="texto largo", fuentes_candidatas="[]")
        leer.return_value = ([nodo], [])
        ctx = segundo_cerebro.recuperar_contexto("Python")
        self.assertNotIn("evidencia_verificacion", ctx["nodos"][0])
        self.assertNotIn("fuentes_candidatas", ctx["nodos"][0])


class APIInteligenteTests(unittest.TestCase):
    def setUp(self):
        segundo_cerebro._limites.clear()
        self.cliente = segundo_cerebro.app.test_client()

    @patch("app.generar_json_ia")
    @patch("app.recuperar_contexto")
    def test_chat_consulta_grafo_y_devuelve_nodos(self, recuperar, generar):
        recuperar.return_value = contexto_falso()
        generar.return_value = {
            "respuesta": "Python está guardado.",
            "informacion_almacenada": "El grafo incluye Python.",
            "complementaria": "",
            "datos_insuficientes": False,
            "nodos_usados": ["python", "inventado"],
        }

        respuesta = self.cliente.post(
            "/api/chat", json={"pregunta": "¿Qué conceptos incluyen Python?"}
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json["nodos"][0]["id"], "python")
        # Un id que no está en el contexto nunca se resalta.
        self.assertEqual(respuesta.json["nodos_destacados"], ["python"])
        recuperar.assert_called_once()
        generar.assert_called_once()

    @patch("app.generar_json_ia")
    @patch("app.recuperar_contexto")
    def test_chat_sin_datos_marca_insuficiente(self, recuperar, generar):
        recuperar.return_value = contexto_falso(nodos=[], mencionados=[])
        generar.return_value = {
            "respuesta": "Kubernetes orquesta contenedores.",
            "informacion_almacenada": "",
            "complementaria": "Conocimiento general.",
            "datos_insuficientes": False,
        }
        respuesta = self.cliente.post("/api/chat", json={"pregunta": "¿Qué es Kubernetes?"})
        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(respuesta.json["datos_insuficientes"])

    def test_chat_rechaza_pregunta_vacia(self):
        respuesta = self.cliente.post("/api/chat", json={"pregunta": " "})
        self.assertEqual(respuesta.status_code, 400)

    @patch("app.recuperar_contexto", return_value=contexto_falso())
    def test_chat_sin_ia_configurada_devuelve_503(self, _):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "", "OPENAI_BASE_URL": ""}):
            respuesta = self.cliente.post("/api/chat", json={"pregunta": "Python"})
        self.assertEqual(respuesta.status_code, 503)
        self.assertIn("no está configurada", respuesta.json["error"])

    def test_limite_de_solicitudes(self):
        with patch.dict("os.environ", {"LIMITE_IA_POR_MINUTO": "2"}):
            codigos = [self.cliente.post("/api/chat", json={"pregunta": ""}).status_code
                       for _ in range(3)]
        self.assertEqual(codigos, [400, 400, 429])

    @patch("app.generar_json_ia")
    @patch("app.consultar")
    def test_sugerencias_filtra_relaciones_duplicadas(self, consultar, generar):
        consultar.side_effect = [
            [
                {"nodo": {"id": "nuevo", "nombre": "Django"}},
                {"nodo": {"id": "python", "nombre": "Python"}},
            ],
            [{"origen": "nuevo", "destino": "python", "tipo": "se basa en"}],
        ]
        generar.return_value = {
            "sugerencias": [
                {"origen_id": "nuevo", "destino_id": "python", "tipo": "Se Basa En",
                 "justificacion": "Relación ya existente.", "confianza": 0.9},
                {"origen_id": "nuevo", "destino_id": "python", "tipo": "es un framework de",
                 "justificacion": "Propuesta nueva.", "confianza": 0.8,
                 "fuente_url": "javascript:alert(1)"},
                {"origen_id": "nuevo", "destino_id": "python", "tipo": "es un framework de",
                 "justificacion": "Repetida en el mismo lote.", "confianza": 0.8},
                {"origen_id": "nuevo", "destino_id": "inventado", "tipo": "usa",
                 "justificacion": "Nodo inexistente.", "confianza": 0.8},
            ]
        }

        respuesta = self.cliente.post(
            "/api/sugerir-relaciones", json={"concepto_id": "nuevo"}
        )

        self.assertEqual(respuesta.status_code, 200)
        sugerencias = respuesta.json["sugerencias"]
        self.assertEqual(len(sugerencias), 1)
        self.assertEqual(sugerencias[0]["tipo"], "es un framework de")
        self.assertEqual(sugerencias[0]["fuente_url"], "")
        self.assertEqual(sugerencias[0]["relaciones_previas_del_par"], ["se basa en"])

    @patch("app.guardar_relacion", return_value=(
        {"origen": "a", "destino": "b", "tipo": "usa"}, True))
    def test_aceptar_relacion_guarda_procedencia(self, guardar):
        respuesta = self.cliente.post("/api/aceptar-relacion", json={
            "origen": "a", "destino": "b", "tipo": "usa",
            "justificacion": "Porque sí", "confianza": 3,
        })
        self.assertEqual(respuesta.status_code, 201)
        extra = guardar.call_args.args[3]
        self.assertEqual(extra["origen_registro"], "Sugerencia IA aprobada")
        self.assertEqual(extra["confianza_ia"], 1.0)

    def test_aceptar_relacion_valida_entrada(self):
        respuesta = self.cliente.post("/api/aceptar-relacion",
                                      json={"origen": "a", "destino": "a", "tipo": "usa"})
        self.assertEqual(respuesta.status_code, 400)

    @patch("app.analizar_evidencias", return_value=None)
    @patch("app.guardar_estado_verificacion")
    @patch("app.buscar_fuentes")
    @patch("app.obtener_objetivo_verificacion")
    def test_verificacion_no_certifica_automaticamente(
        self, obtener_objetivo, buscar_fuentes, guardar, _
    ):
        obtener_objetivo.return_value = (
            "concepto",
            "python",
            {"nombre": "Python", "descripcion": "Lenguaje"},
            None,
        )
        buscar_fuentes.return_value = [{
            "fuente": "OpenAlex · índice académico",
            "tipo_fuente": "academica",
            "titulo": "Artículo académico",
            "url": "https://doi.org/10/example",
            "anio": 2024,
            "autores": [],
            "evidencia": "Resumen del artículo.",
        }]
        guardar.return_value = True

        respuesta = self.cliente.post(
            "/api/verificar", json={"objetivo": "concepto", "id": "python"}
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json["estado_verificacion"], "Requiere revisión")
        self.assertIn("manual", respuesta.json["mensaje"])
        self.assertEqual(guardar.call_args.args[2], "Requiere revisión")
        candidatas = guardar.call_args.args[6]
        self.assertEqual(candidatas[0]["url"], "https://doi.org/10/example")

    @patch("app.guardar_estado_verificacion")
    @patch("app.buscar_fuentes", return_value=[])
    @patch("app.obtener_objetivo_verificacion")
    def test_sin_evidencia_no_se_pudo_verificar_y_no_sobrescribe(
        self, obtener_objetivo, _, guardar
    ):
        obtener_objetivo.return_value = (
            "concepto", "python",
            {"nombre": "Python", "descripcion": "Lenguaje",
             "estado_verificacion": "Verificado con evidencia"}, None,
        )
        respuesta = self.cliente.post(
            "/api/verificar", json={"objetivo": "concepto", "id": "python"}
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertIn("No se pudo verificar", respuesta.json["mensaje"])
        self.assertEqual(respuesta.json["estado_verificacion"], "Verificado con evidencia")
        guardar.assert_not_called()

    @patch("app.guardar_estado_verificacion", return_value=True)
    @patch("app.obtener_objetivo_verificacion")
    def test_decision_humana_acepta_cualquier_candidata_guardada(self, obtener, guardar):
        candidatas = [
            {"fuente": "A", "url": "https://a.example", "evidencia": "texto a"},
            {"fuente": "B", "url": "https://b.example", "evidencia": "texto b"},
        ]
        obtener.return_value = ("concepto", "python", {
            "nombre": "Python", "fuentes_candidatas": json.dumps(candidatas),
        }, None)
        ok = self.cliente.post("/api/verificar", json={
            "objetivo": "concepto", "id": "python", "decision": "verificado",
            "url": "https://b.example", "evidencia": "texto b",
        })
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json["estado_verificacion"], "Verificado con evidencia")
        self.assertEqual(guardar.call_args.args[3], "B")

        falsa = self.cliente.post("/api/verificar", json={
            "objetivo": "concepto", "id": "python", "decision": "verificado",
            "url": "https://b.example", "evidencia": "texto inventado",
        })
        self.assertEqual(falsa.status_code, 400)

    @patch("app.generar_json_ia")
    def test_analisis_descarta_citas_que_no_estan_en_la_fuente(self, generar):
        fuentes = [
            {"evidencia": "Flask is a micro web framework written in Python."},
            {"evidencia": "Django is a high-level Python web framework."},
        ]
        generar.return_value = {"analisis": [
            {"indice": 0, "veredicto": "apoya",
             "cita": "Flask is a micro web framework written in Python", "explicacion": "ok"},
            {"indice": 1, "veredicto": "contradice",
             "cita": "Flask está escrito en Java", "explicacion": "inventada"},
            {"indice": 7, "veredicto": "apoya", "cita": "x"},
        ]}
        with patch.dict("os.environ", {"OPENAI_API_KEY": "x"}):
            analisis = segundo_cerebro.analizar_evidencias("Flask se basa en Python", fuentes)
        self.assertEqual(analisis[0]["veredicto"], "apoya")
        self.assertEqual(analisis[1]["veredicto"], "insuficiente")
        self.assertEqual(analisis[1]["cita"], "")
        self.assertNotIn(7, analisis)

    @patch("app.generar_json_ia")
    @patch("app.leer_grafo_completo", return_value=(NODOS, ARISTAS))
    def test_recomendaciones_separan_grafo_e_ia(self, _, generar):
        generar.return_value = {
            "conceptos_no_registrados": ["Django", "python", "Docker"],
            "rutas_estudio": ["Python → Flask"],
            "preguntas_practica": ["¿Qué expone Flask?"],
        }
        with patch.dict("os.environ", {"OPENAI_API_KEY": "x"}):
            respuesta = self.cliente.get("/api/recomendaciones")
        datos = respuesta.json
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(datos["sugerencias_ia"]["conceptos_no_registrados"], ["Django", "Docker"])
        self.assertEqual(datos["datos_grafo"]["pocas_conexiones"][0]["id"], "aislado")
        base = [t["id"] for t in datos["datos_grafo"]["temas_base"]]
        self.assertIn("grafos", base)


class ProveedorIATests(unittest.TestCase):
    def setUp(self):
        segundo_cerebro._cliente_ia = None

    def tearDown(self):
        segundo_cerebro._cliente_ia = None

    def test_reintenta_con_max_completion_tokens(self):
        from openai import BadRequestError

        # Se construye sin respuesta HTTP real (independiente de la versión del SDK).
        error = BadRequestError.__new__(BadRequestError)
        Exception.__init__(error, "Unsupported parameter: 'max_tokens'")
        mensaje = MagicMock()
        mensaje.choices = [MagicMock(finish_reason="stop",
                                     message=MagicMock(content='{"ok": true}'))]
        cliente = MagicMock()
        cliente.chat.completions.create.side_effect = [error, mensaje]
        segundo_cerebro._cliente_ia = cliente
        with patch.dict("os.environ", {"OPENAI_API_KEY": "x"}):
            resultado = segundo_cerebro.generar_json_ia([{"role": "user", "content": "hola"}])
        self.assertEqual(resultado, {"ok": True})
        segunda = cliente.chat.completions.create.call_args_list[1].kwargs
        self.assertIn("max_completion_tokens", segunda)
        self.assertNotIn("temperature", segunda)

    def test_url_segura(self):
        self.assertEqual(segundo_cerebro.url_segura("javascript:alert(1)"), "")
        self.assertEqual(segundo_cerebro.url_segura("https://flask.palletsprojects.com"),
                         "https://flask.palletsprojects.com")


if __name__ == "__main__":
    unittest.main()
