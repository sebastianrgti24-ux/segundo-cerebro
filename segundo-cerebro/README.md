# Segundo Cerebro: Gestor de Conceptos Vinculados

Flask, Neo4j Aura y Vis.js Network para explorar conceptos académicos, con un asistente de IA que responde **a partir de tu grafo** (RAG). El grafo es la única fuente de los datos almacenados: el modelo no tiene acceso a Neo4j, no ejecuta Cypher y no puede escribir nada sin tu aprobación.

## Requisitos y configuración

1. Crear un entorno: `python -m venv venv`
   - Windows: `venv\Scripts\activate`
   - macOS/Linux: `source venv/bin/activate`
2. Instalar dependencias: `pip install -r requirements.txt`
3. Copiar `.env.example` a `.env` y completar `NEO4J_URI`, `NEO4J_USER` y `NEO4J_PASSWORD`.
4. Para el asistente, las sugerencias y el análisis de evidencias, configurar la IA (ver abajo).
5. Iniciar con `python app.py` y abrir <http://127.0.0.1:5000>.
6. Pruebas: `python -m unittest discover -s tests -v`

### Configurar la IA

**Opción A, la API de OpenAI (de pago por uso):**

1. Crea una clave en <https://platform.openai.com/api-keys> y añade saldo en *Billing*.
2. En `.env`:
   ```
   OPENAI_API_KEY=sk-...
   OPENAI_MODEL=gpt-4o-mini
   ```
   Cualquier modelo de chat compatible con salida JSON sirve. Si el modelo es de razonamiento y rechaza `max_tokens`/`temperature`, la app reintenta automáticamente con `max_completion_tokens`.

**Opción B, gratuita y local:** usa un servidor compatible con la API de OpenAI, como LM Studio u Ollama.

```
OPENAI_API_KEY=
OPENAI_BASE_URL=http://localhost:1234/v1      # LM Studio  (Ollama: http://localhost:11434/v1)
OPENAI_MODEL=<nombre del modelo cargado>
```

Sin IA configurada, la app sigue funcionando. La verificación con fuentes también funciona, pero sin el análisis automático de las citas.

La clave nunca se envía al navegador. No configures `FLASK_DEBUG=true` en producción.

## Funcionalidades IA y fuentes

| Ruta | Qué hace |
|---|---|
| `POST /api/chat` | **RAG**: detecta los conceptos que nombras (en el orden de la pregunta), calcula la ruta más corta guardada entre ellos y añade sus vecinos y las coincidencias de texto. Envía ese contexto al modelo. La respuesta separa lo que está en el grafo de la información complementaria, marca cuando faltan datos y devuelve los nodos y la ruta para resaltarlos en el mapa. Los ids que la IA invente se descartan. |
| `POST /api/sugerir-relaciones` | Propone hasta 5 relaciones con justificación, confianza y fuente sugerida. Descarta nodos inexistentes y duplicados (sin distinguir mayúsculas). **No escribe en el grafo.** |
| `POST /api/aceptar-relacion` | Guarda solo una propuesta que aprobaste, con su procedencia (`origen_registro`, `justificacion_ia`, `confianza_ia`). Queda pendiente de verificación. |
| `POST /api/verificar` | Consulta, por orden de prioridad, el **sitio oficial** (declarado en Wikidata), **OpenAlex** (académico) y **Wikipedia** (secundaria). Si la IA está configurada, compara la afirmación con cada fragmento; un veredicto sin cita literal comprobable se descarta. El resultado máximo automático es «Requiere revisión»: solo tú puedes marcar «Verificado con evidencia» o «Contradicho por una fuente». Si no hay evidencia, responde «No se pudo verificar» y no cambia el estado. |
| `GET /api/fuentes` | Fuentes, URL, evidencia, fecha y estado guardados. |
| `GET /api/recomendaciones` | Separa los datos medidos en Neo4j (temas base, conceptos con pocas conexiones, pendientes) de las sugerencias de IA (conceptos nuevos, rutas, preguntas de práctica). |
| `GET /api/ia/estado` | Indica si la IA está configurada (sin exponer la clave). |

`/api/verificar` acepta `{"objetivo":"concepto","id":"..."}` o `{"objetivo":"relacion","origen":"...","destino":"...","tipo":"..."}`. Para registrar tu revisión, envía además `decision` (`verificado`, `contradicho` o `revision`) junto con la `url` y la `evidencia` de una de las fuentes recuperadas en la última consulta.

## Esquema de Neo4j

No se elimina nada. Se añaden propiedades opcionales:

- En `:Concepto` y `:RELACIONADO_CON`: `estado_verificacion`, `fuente_verificacion`, `url_verificacion`, `evidencia_verificacion`, `fecha_verificacion` y `fuentes_candidatas` (JSON).
- En `:RELACIONADO_CON`: `origen_registro`, `fecha_creacion` y, si vino de la IA, `justificacion_ia`, `confianza_ia` y `fuente_sugerida`.

Al arrancar, la app ejecuta esta migración idempotente, que solo rellena estados vacíos. También puedes ejecutarla tú en Aura → *Query*:

```cypher
MATCH (n:Concepto) WHERE n.estado_verificacion IS NULL
SET n.estado_verificacion = 'Pendiente de verificación';
MATCH (:Concepto)-[r:RELACIONADO_CON]->(:Concepto) WHERE r.estado_verificacion IS NULL
SET r.estado_verificacion = 'Pendiente de verificación';
```

## Seguridad

- Todas las consultas Cypher son fijas y parametrizadas; la IA solo recibe datos y devuelve JSON, que Flask valida.
- Límite de solicitudes por IP en las rutas de IA y verificación (`LIMITE_IA_POR_MINUTO`). Es temporal y por proceso: en un despliegue con varias instancias, añade límites y autenticación en el proxy.
- Las conversaciones no se guardan en el servidor; el historial vive solo en la página.
- Las URL se filtran a `http(s)` antes de mostrarse.
