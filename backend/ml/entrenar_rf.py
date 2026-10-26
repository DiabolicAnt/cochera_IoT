"""Entrena el Random Forest de la cochera con lecturas reales de la maqueta mas datos aumentados.

Uso:
    python ml/entrenar_rf.py --db data/smartgate.db [--db otra.db]

Las lecturas reales salen de las tablas `readings` (practicas anteriores) y `eventos` (practica 3).
La cochera pasa casi todo el tiempo en reposo, asi que las clases raras se completan con muestras
sinteticas generadas dentro de los rangos fisicos de los sensores; todas se etiquetan con las mismas
reglas de operacion (`etiqueta_por_reglas`). Las metricas se reportan tambien solo sobre datos reales.
"""
import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import joblib
import matplotlib
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from sklearn.model_selection import train_test_split

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ml_service import (CLASES, INVALIDA, TOF_SIN_OBJETO, ULTRA_SIN_OBJETO, VARIABLES,  # noqa: E402
                        data_desde_readings, etiqueta_por_reglas, vector)

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

AQUI = Path(__file__).resolve().parent
MAX_REALES_POR_CLASE = 1500
OBJETIVO_POR_CLASE = 1500
rng = np.random.default_rng(42)


def leer_reales(rutas):
    filas = []
    for ruta in rutas:
        con = sqlite3.connect(ruta)
        tablas = {t for (t,) in con.execute("select name from sqlite_master where type='table'")}
        if "readings" in tablas:
            for (payload,) in con.execute("select payload from readings where node_id='cochera-01'"):
                evento = json.loads(payload)
                if evento["source"] == "hardware":
                    filas.append(vector(evento.get("data") or data_desde_readings(evento)))
        elif "eventos" in tablas:  # Los eventos repiten lo de readings; solo se usan si no existe esa tabla.
            for (variables,) in con.execute("select variables_json from eventos where fuente='hardware'"):
                filas.append(vector(json.loads(variables)))
    return filas


def distancia(sin_objeto, minimo, maximo, p_sin_objeto=0.4):
    return sin_objeto if rng.random() < p_sin_objeto else float(rng.uniform(minimo, maximo))


def sintetica(clase):
    """Una muestra dentro de los rangos fisicos de cada situacion; la etiqueta final la ponen las reglas."""
    libre_ext = lambda: distancia(ULTRA_SIN_OBJETO, 300, 3000)  # noqa: E731
    libre_int = lambda: distancia(TOF_SIN_OBJETO, 600, 1900, p_sin_objeto=0.8)  # noqa: E731
    if clase == "Libre":
        return [libre_ext(), libre_int(), 1, 0]
    if clase == "Vehículo en acceso":
        return [float(rng.uniform(20, 295)), libre_int(), 1, 0]
    if clase == "Vehículo estacionado":
        return [libre_ext(), float(rng.uniform(20, 599)), 1, 0]
    if clase == "Puerta en operación":
        return [distancia(ULTRA_SIN_OBJETO, 20, 3000), distancia(TOF_SIN_OBJETO, 20, 1900), int(rng.random() < 0.15),
                float(rng.uniform(5, 100))]
    if clase == "Intrusión":
        # En una intrusion real el servo sigue en cerrado: apertura exactamente 0 casi siempre.
        apertura = 0.0 if rng.random() < 0.8 else float(rng.uniform(0, 4))
        return [distancia(ULTRA_SIN_OBJETO, 20, 3000), distancia(TOF_SIN_OBJETO, 20, 1900), 0, apertura]
    falla = [INVALIDA, distancia(TOF_SIN_OBJETO, 20, 1900)] if rng.random() < 0.5 else [distancia(ULTRA_SIN_OBJETO, 20, 3000), INVALIDA]
    return falla + [int(rng.random() < 0.7), float(rng.choice([0, rng.uniform(0, 100)]))]


def main():
    args = argparse.ArgumentParser()
    args.add_argument("--db", action="append", required=True)
    opciones = args.parse_args()

    reales = leer_reales(opciones.db)
    por_clase = {c: [] for c in CLASES}
    for v in reales:
        por_clase[etiqueta_por_reglas(v)].append(v)
    conteo_reales = {c: len(v) for c, v in por_clase.items()}

    X, y, origen = [], [], []
    for clase, filas in por_clase.items():
        if len(filas) > MAX_REALES_POR_CLASE:  # La clase Libre domina: se toma una muestra.
            filas = [filas[i] for i in rng.choice(len(filas), MAX_REALES_POR_CLASE, replace=False)]
        X += filas; y += [clase] * len(filas); origen += ["real"] * len(filas)
        faltan = max(0, OBJETIVO_POR_CLASE - len(filas))
        while faltan:
            v = sintetica(clase)
            if etiqueta_por_reglas(v) == clase:
                X.append(v); y.append(clase); origen.append("sintetica"); faltan -= 1

    X, y, origen = np.array(X, dtype=float), np.array(y), np.array(origen)
    X_tr, X_te, y_tr, y_te, o_tr, o_te = train_test_split(X, y, origen, test_size=0.2, stratify=y, random_state=42)
    modelo = RandomForestClassifier(n_estimators=100, random_state=42, n_jobs=-1)
    modelo.fit(X_tr, y_tr)
    modelo.n_jobs = 1  # Inferencia de una muestra: un hilo es mas rapido.

    pred = modelo.predict(X_te)
    reales_test = o_te == "real"
    clases = [c for c in CLASES if c in set(y)]
    matriz = confusion_matrix(y_te, pred, labels=clases)
    metricas = {
        "variables": VARIABLES,
        "lecturas_reales_leidas": len(reales),
        "reales_por_clase": conteo_reales,
        "dataset_por_clase": dict(Counter(y.tolist())),
        "dataset_reales": int((origen == "real").sum()),
        "dataset_sinteticas": int((origen == "sintetica").sum()),
        "entrenamiento": len(y_tr), "prueba": len(y_te),
        "exactitud_prueba": round(accuracy_score(y_te, pred), 4),
        "exactitud_prueba_solo_reales": round(accuracy_score(y_te[reales_test], pred[reales_test]), 4),
        "prueba_reales": int(reales_test.sum()),
        "importancia_variables": dict(zip(VARIABLES, [round(float(i), 4) for i in modelo.feature_importances_])),
        "reporte": classification_report(y_te, pred, labels=clases, output_dict=True, zero_division=0),
        "matriz_confusion": {"clases": clases, "valores": matriz.tolist()},
    }
    joblib.dump(modelo, AQUI / "modelo_rf.joblib", compress=3)
    (AQUI / "metricas.json").write_text(json.dumps(metricas, ensure_ascii=False, indent=1), encoding="utf-8")

    fig, ax = plt.subplots(figsize=(7.2, 5.6))
    ax.imshow(matriz, cmap="Greens")
    ax.set_xticks(range(len(clases)), clases, rotation=35, ha="right")
    ax.set_yticks(range(len(clases)), clases)
    for i in range(len(clases)):
        for j in range(len(clases)):
            ax.text(j, i, matriz[i, j], ha="center", va="center", color="white" if matriz[i, j] > matriz.max() / 2 else "black")
    ax.set_xlabel("Clase predicha por el Random Forest"); ax.set_ylabel("Clase real")
    ax.set_title(f"Matriz de confusión (prueba, n={len(y_te)}, exactitud {metricas['exactitud_prueba']:.2%})")
    fig.tight_layout(); fig.savefig(AQUI / "matriz_confusion.png", dpi=150); plt.close(fig)

    print(json.dumps({k: v for k, v in metricas.items() if k not in ("reporte", "matriz_confusion")}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
