"""Experimento de la practica 3: latencia L y perdida P segun la frecuencia de envio de la ESP32.

Para cada periodo (1000, 500 y 100 ms) manda el comando por el mismo canal del dashboard, espera a que la
ESP32 lo confirme en su telemetria, reinicia /stats y mide durante N segundos.

Uso: python experimentos/experimento_frecuencia.py --segundos 60 --salida ../docs/practica3/evidencias
"""
import argparse
import json
import time
import urllib.request
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

API = "http://127.0.0.1:8000"
NODO = "cochera-01"


def pedir(ruta, cuerpo=None):
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    req = urllib.request.Request(API + ruta, data=datos, method="POST" if cuerpo is not None else "GET",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def periodo_actual():
    ultimo = pedir(f"/api/readings?node_id={NODO}&limit=1")[0]
    return (ultimo.get("control") or {}).get("periodo_ms")


def correr(periodo, segundos):
    comando = pedir("/api/control", {"accion": "periodo", "valor": periodo, "node_id": NODO})
    limite = time.time() + 20
    while periodo_actual() != periodo:
        if time.time() > limite:
            raise RuntimeError(f"La ESP32 no confirmo el periodo {periodo} ms (comando #{comando['id']})")
        time.sleep(0.5)
    time.sleep(3)  # Que se vacien los mensajes del periodo anterior.
    pedir("/stats/reset", {})
    time.sleep(segundos)
    s = pedir("/stats")
    nodo = s["nodes"][NODO]
    return {"periodo_ms": periodo, "segundos": segundos, "desfase_reloj_ms": s["desfase_reloj_ms"],
            **{k: nodo[k] for k in ("received", "seq_min", "seq_max", "p_pct", "lost", "mean_interval_ms", "jitter_ms",
                                    "latencia_prom_ms", "latencia_p95_ms", "latencia_max_ms", "inferencia_us_prom",
                                    "procesamiento_ms_prom", "procesamiento_ms_max", "publish_us_avg", "publish_us_max")}}


def graficar(resultados, salida):
    etiquetas = [f"{r['periodo_ms']} ms" for r in resultados]
    fig, (a, b) = plt.subplots(1, 2, figsize=(10, 4))
    x = range(len(resultados))
    a.bar([i - 0.2 for i in x], [r["latencia_prom_ms"] for r in resultados], 0.4, label="Promedio", color="#2d6142")
    a.bar([i + 0.2 for i in x], [r["latencia_p95_ms"] for r in resultados], 0.4, label="Percentil 95", color="#a9c9a4")
    a.set_xticks(list(x), etiquetas); a.set_ylabel("Latencia L (ms)"); a.set_title("Latencia de extremo a extremo")
    a.legend()
    b.bar(list(x), [r["p_pct"] for r in resultados], 0.5, color="#c07a12")
    b.set_xticks(list(x), etiquetas); b.set_ylabel("Pérdida P (%)"); b.set_title("Pérdida de paquetes")
    for i, r in enumerate(resultados):
        b.text(i, r["p_pct"], f"{r['p_pct']:.2f} %\n({r['received']} recibidos)", ha="center", va="bottom", fontsize=9)
    b.set_ylim(0, max(1, max(r["p_pct"] for r in resultados) * 1.6))
    fig.suptitle("Frecuencia de envío de la ESP32")
    fig.tight_layout(); fig.savefig(salida / "experimento_frecuencia.png", dpi=150); plt.close(fig)


def main():
    args = argparse.ArgumentParser()
    args.add_argument("--segundos", type=int, default=60)
    args.add_argument("--salida", default=".")
    opciones = args.parse_args()
    salida = Path(opciones.salida); salida.mkdir(parents=True, exist_ok=True)
    resultados = []
    for periodo in (1000, 500, 100):
        print(f"Midiendo con periodo {periodo} ms durante {opciones.segundos} s...", flush=True)
        resultados.append(correr(periodo, opciones.segundos))
        print(json.dumps(resultados[-1], ensure_ascii=False), flush=True)
    pedir("/api/control", {"accion": "periodo", "valor": 500, "node_id": NODO})  # Dejarla como estaba.
    (salida / "experimento_frecuencia.json").write_text(json.dumps(resultados, ensure_ascii=False, indent=1), encoding="utf-8")
    graficar(resultados, salida)
    print("Listo:", salida / "experimento_frecuencia.json")


if __name__ == "__main__":
    main()
