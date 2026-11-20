"""
Límite de solicitudes en memoria (ventana deslizante por IP).

Suficiente para una app de un solo proceso (desarrollo o una demostración).
Si se publica con varios procesos, hay que usar un almacén compartido
(por ejemplo Flask-Limiter con Redis), porque cada proceso lleva su propia cuenta.
"""
import threading
import time
from collections import defaultdict, deque


class LimitadorVentana:
    def __init__(self, maximo, ventana_segundos, reloj=time.monotonic):
        self.maximo = maximo
        self.ventana = ventana_segundos
        self._reloj = reloj
        self._golpes = defaultdict(deque)
        self._lock = threading.Lock()

    def permitir(self, clave):
        """Devuelve (permitido, segundos_para_reintentar)."""
        ahora = self._reloj()
        with self._lock:
            cola = self._golpes[clave]
            while cola and ahora - cola[0] >= self.ventana:
                cola.popleft()
            if len(cola) >= self.maximo:
                return False, max(1, int(self.ventana - (ahora - cola[0])) + 1)
            cola.append(ahora)
            if len(self._golpes) > 5000:          # evita crecer sin control
                for k in [k for k, v in self._golpes.items() if not v]:
                    del self._golpes[k]
            return True, 0
