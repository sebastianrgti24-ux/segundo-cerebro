# Segundo Cerebro v2: Gestor de Conceptos Vinculados + IA

Flask + Neo4j Aura + Vis.js. La v2 mantiene todo lo de la v1 y agrega:

- **Asistente IA** que responde consultando tu grafo (RAG): separa «De tu grafo» de «Información complementaria», cita conceptos y los resalta en el mapa.
- **Relaciones inteligentes**: la IA (o reglas) sugiere vínculos; Flask los valida y **solo se guarda lo que aceptas**.
- **Verificación**: busca evidencia en fuentes oficiales y Wikipedia (lista cerrada, nunca URLs de la IA). Estados: pendiente, verificado, requiere revisión, contradicho. También puedes registrar una fuente a mano.
- **Estudio**: orden de estudio, rutas de aprendizaje, conceptos poco conectados, preguntas de práctica.
- **Modo sin IA**: todo funciona con reglas si no hay clave o si la IA falla.

La IA nunca accede a Neo4j: solo ve el contexto que el servidor le entrega. No hay migración: los datos de la v1 se leen como «pendiente de verificación».

## Puesta en marcha
```
python -m venv venv
venv\Scripts\activate          # Windows  (Linux/Mac: source venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env         # edita .env con tus credenciales de Aura
python app.py                  # http://127.0.0.1:5000
```

## Configurar la IA (opcional)
> ⚠️ **La API de OpenAI es de pago.** Cada pregunta, sugerencia o verificación con IA consume tokens. Pon un límite de gasto en tu cuenta. Sin clave, la app usa el modo sin IA (gratis).

1. Crea una clave en https://platform.openai.com/api-keys y ponla en `.env` como `OPENAI_API_KEY`.
2. Verifica el nombre vigente del modelo y ponlo en `OPENAI_MODEL` (por defecto `gpt-4o-mini`).
3. Reinicia la app: arriba del panel verás «IA activa · modelo».

**Alternativa gratuita/local:** cualquier servicio compatible con OpenAI (p. ej. Ollama): define `OPENAI_BASE_URL=http://localhost:11434/v1`, `OPENAI_API_KEY=ollama` y `OPENAI_MODEL=llama3.1`.

## Pruebas
```
pip install -r requirements-dev.txt
python -m pytest -q
```
Usan una base Neo4j y una IA simuladas: validan la lógica, no la sintaxis real de Cypher ni la red.

## Seguridad y límites
- La clave vive solo en el servidor (`.env`, que no debe subirse a git). Nunca reutilices contraseñas que hayas pegado en chats.
- Limitador por IP en memoria (por proceso); en producción usa Flask-Limiter + Redis y `APP_ENTORNO=produccion`.
- Las fuentes automáticas se limitan a documentación oficial de un registro cerrado y Wikipedia; la verificación es una ayuda, no una prueba.
