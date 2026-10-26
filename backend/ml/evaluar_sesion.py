"""Compara el modelo v1 (prediccion guardada en la tabla eventos) contra las etiquetas de referencia
en la sesion real de situaciones. El v1 nunca vio estos datos, asi que es una prueba fuera de muestra.

Uso: python ml/evaluar_sesion.py --db data/smartgate.db --desde "2026-10-26 05:55" --hasta "2026-10-26 06:08"
"""
import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ml_service import Clasificador, etiqueta_por_reglas, vector  # noqa: E402

args = argparse.ArgumentParser()
args.add_argument("--db", required=True); args.add_argument("--desde", required=True); args.add_argument("--hasta", required=True)
o = args.parse_args()
filas = sqlite3.connect(o.db).execute(
    "select variables_json, clase_predicha_rf, probabilidad_rf from eventos where fuente='hardware' and fecha_registro between ? and ?",
    (o.desde, o.hasta)).fetchall()
v2 = Clasificador()
total, ok_v1, ok_v2, errores_v1 = len(filas), 0, 0, Counter()
confianza = {}  # clase de referencia -> listas de probabilidad v1 y v2
for variables, pred_v1, prob_v1 in filas:
    data = json.loads(variables)
    real = etiqueta_por_reglas(vector(data))
    pred_v2, prob_v2, _ = v2.clasificar(data)
    ok_v1 += pred_v1 == real
    ok_v2 += pred_v2 == real
    confianza.setdefault(real, ([], []))
    confianza[real][0].append(prob_v1); confianza[real][1].append(prob_v2)
    if pred_v1 != real:
        errores_v1[f"{real} -> {pred_v1}"] += 1
resultado = {"lecturas_sesion": total, "exactitud_v1": round(ok_v1 / total, 4), "exactitud_v2": round(ok_v2 / total, 4),
             "confianza_media": {c: {"v1": round(sum(a) / len(a), 3), "v2": round(sum(b) / len(b), 3)}
                                 for c, (a, b) in confianza.items()},
             "errores_v1": dict(errores_v1.most_common()), "referencia_por_clase": dict(Counter(
                 etiqueta_por_reglas(vector(json.loads(f[0]))) for f in filas))}
(Path(__file__).parent / "evaluacion_sesion.json").write_text(json.dumps(resultado, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(resultado, ensure_ascii=False, indent=1))
