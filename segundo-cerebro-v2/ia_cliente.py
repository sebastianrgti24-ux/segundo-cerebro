"""
Cliente de IA (SDK oficial de OpenAI) con respuestas siempre en JSON.

Notas de diseño
- La clave sale de OPENAI_API_KEY (archivo .env). Nunca llega al navegador.
- OPENAI_BASE_URL permite usar otro servicio compatible con la API de OpenAI
  (por ejemplo, un modelo local con Ollama) sin cambiar el código.
- Si no hay clave, `disponible` es False y la app funciona en «modo sin IA».
- El modelo NO ejecuta consultas ni escribe en la base de datos: solo recibe
  texto y devuelve JSON que Flask valida.
"""
import json
import os
import re


class ErrorIA(Exception):
    """Error al usar la IA. `codigo` sirve a las rutas para elegir el HTTP."""

    def __init__(self, codigo, mensaje):
        super().__init__(mensaje)
        self.codigo = codigo          # no_configurada | autenticacion | limite | tiempo | red | respuesta | servicio
        self.mensaje = mensaje


class ClienteIA:
    def __init__(self, api_key=None, modelo="gpt-4o-mini", base_url=None,
                 timeout=30.0, max_tokens=800, client=None):
        self.api_key = api_key
        self.modelo = modelo
        self.base_url = base_url
        self.timeout = timeout
        self.max_tokens = max_tokens
        self._client = client          # se puede inyectar uno (pruebas)

    # ---- configuración -------------------------------------------------
    @classmethod
    def desde_entorno(cls):
        return cls(
            api_key=(os.getenv("OPENAI_API_KEY") or "").strip() or None,
            modelo=(os.getenv("OPENAI_MODEL") or "gpt-4o-mini").strip(),
            base_url=(os.getenv("OPENAI_BASE_URL") or "").strip() or None,
            timeout=float(os.getenv("OPENAI_TIMEOUT") or 30),
            max_tokens=int(os.getenv("OPENAI_MAX_TOKENS") or 800),
        )

    @property
    def disponible(self):
        # Un servicio local compatible (base_url) puede no exigir clave real.
        return bool(self.api_key or self.base_url or self._client)

    def _cliente(self):
        if self._client is None:
            from openai import OpenAI      # import tardío: solo si se usa IA
            self._client = OpenAI(
                api_key=self.api_key or "sin-clave",
                base_url=self.base_url,
                timeout=self.timeout,
                max_retries=1,
            )
        return self._client

    # ---- llamada -------------------------------------------------------
    def json(self, sistema, usuario, max_tokens=None):
        """Pide una respuesta JSON y devuelve un dict. Lanza ErrorIA si falla."""
        if not self.disponible:
            raise ErrorIA("no_configurada", "La IA no está configurada (falta OPENAI_API_KEY).")

        import openai

        mensajes = [
            {"role": "system", "content": sistema},
            {"role": "user", "content": usuario},
        ]
        limite = max_tokens or self.max_tokens
        # api.openai.com usa max_completion_tokens; los servicios compatibles
        # suelen seguir esperando max_tokens.
        parametro_limite = "max_tokens" if self.base_url else "max_completion_tokens"
        base = {"model": self.modelo, "messages": mensajes, parametro_limite: limite}

        try:
            try:
                resp = self._cliente().chat.completions.create(
                    **base, response_format={"type": "json_object"}
                )
            except openai.BadRequestError:
                # Algunos servicios no admiten response_format: se reintenta sin él.
                resp = self._cliente().chat.completions.create(**base)
        except openai.AuthenticationError:
            raise ErrorIA("autenticacion", "La clave de la API de IA no es válida. Revisa OPENAI_API_KEY.")
        except openai.PermissionDeniedError:
            raise ErrorIA("autenticacion", "La clave de la API no tiene permiso para usar ese modelo.")
        except openai.RateLimitError:
            raise ErrorIA("limite", "La API de IA rechazó la solicitud por límite de uso o saldo agotado.")
        except openai.APITimeoutError:
            raise ErrorIA("tiempo", "La IA tardó demasiado en responder.")
        except openai.APIConnectionError:
            raise ErrorIA("red", "No se pudo conectar con el servicio de IA.")
        except openai.NotFoundError:
            raise ErrorIA("servicio", f"El modelo «{self.modelo}» no existe o no está disponible. Revisa OPENAI_MODEL.")
        except openai.APIError as e:
            raise ErrorIA("servicio", f"El servicio de IA devolvió un error ({getattr(e, 'status_code', '?')}).")

        try:
            texto = resp.choices[0].message.content or ""
        except (AttributeError, IndexError):
            raise ErrorIA("respuesta", "La IA devolvió una respuesta vacía.")
        return extraer_json(texto)


def extraer_json(texto):
    """Convierte la respuesta del modelo en un dict (tolera ```json ... ```)."""
    texto = (texto or "").strip()
    candidatos = [texto]
    m = re.search(r"\{.*\}", texto, re.DOTALL)
    if m:
        candidatos.append(m.group(0))
    for c in candidatos:
        try:
            valor = json.loads(c)
            if isinstance(valor, dict):
                return valor
        except ValueError:
            continue
    raise ErrorIA("respuesta", "La IA no devolvió un JSON válido.")
