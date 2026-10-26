"""Clasificacion Random Forest del estado de la cochera.

El vector de entrada son las 4 variables de `data` del payload:
    ultrasonico_mm  distancia en la entrada (4000 = sin objeto, -1 = lectura invalida)
    tof_mm          distancia en el estacionamiento (2000 = sin objeto, -1 = lectura invalida)
    reed_cerrado    1 si el iman esta junto al reed
    apertura_pct    0 = servo en cerrado, 100 = servo en abierto
"""
import time
from pathlib import Path

import joblib
import numpy as np

VARIABLES = ["ultrasonico_mm", "tof_mm", "reed_cerrado", "apertura_pct"]
ULTRA_SIN_OBJETO, TOF_SIN_OBJETO, INVALIDA = 4000, 2000, -1
CLASES = ["Libre", "Vehículo en acceso", "Vehículo estacionado", "Puerta en operación", "Intrusión", "Falla de sensor"]
MODELO = Path(__file__).resolve().parent / "ml" / "modelo_rf.joblib"


def data_desde_readings(evento):
    """Arma `data` para payloads anteriores a la practica 3 (sin ese campo), con la calibracion 90/180."""
    ex, it = evento["readings"]["exterior"], evento["readings"]["interior"]

    def distancia(lectura, sin_objeto):
        if lectura["valid"]:
            return lectura["filtered_mm"]
        return sin_objeto if lectura["error"] == "SIN_OBJETO" else None

    puerta, grados = evento["door"]["state"], evento["door"]["servo_command_deg"]
    if puerta in ("CERRADA", "ALERTA"):
        apertura = 0
    elif puerta == "ABIERTA":
        apertura = 100
    else:
        apertura = round(min(max((grados - 90) / 90 * 100, 0), 100)) if 90 <= grados <= 180 else 50
    return {"ultrasonico_mm": distancia(ex, ULTRA_SIN_OBJETO), "tof_mm": distancia(it, TOF_SIN_OBJETO),
            "reed_cerrado": int(evento["door"]["reed_closed"]), "apertura_pct": apertura}


def vector(data):
    """Orden fijo de las variables; una lectura invalida (null) entra como -1."""
    return [INVALIDA if data.get(v) is None else float(data[v]) for v in VARIABLES]


def etiqueta_por_reglas(v):
    """Etiqueta de referencia para entrenar: reglas de operacion de la cochera, en orden de prioridad."""
    ultra, tof, reed, apertura = v
    if ultra == INVALIDA or tof == INVALIDA:
        return "Falla de sensor"
    if not reed and apertura < 5:
        return "Intrusión"          # La hoja se separo del reed sin que el servo la moviera.
    if not reed or apertura >= 5:
        return "Puerta en operación"
    if ultra < 300:
        return "Vehículo en acceso"
    # Con el interior vacio el ToF no ve nada (2000). En la sesion real el carro estacionado se leyo
    # entre 120 y 330 mm porque el haz solo alcanza una parte: cualquier lectura < 600 mm es el carro.
    if tof < 600:
        return "Vehículo estacionado"
    return "Libre"


class Clasificador:
    def __init__(self, ruta=MODELO):
        self.modelo = joblib.load(ruta) if Path(ruta).exists() else None

    @property
    def listo(self):
        return self.modelo is not None

    def clasificar(self, data):
        """Devuelve (clase, probabilidad, microsegundos de inferencia) o (None, None, None) sin modelo."""
        if not self.listo:
            return None, None, None
        x = np.array(vector(data), dtype=float).reshape(1, -1)
        inicio = time.perf_counter()
        probabilidades = self.modelo.predict_proba(x)[0]
        us = round((time.perf_counter() - inicio) * 1e6)
        i = int(np.argmax(probabilidades))
        return str(self.modelo.classes_[i]), round(float(probabilidades[i]), 3), us
