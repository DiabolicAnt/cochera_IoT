import asyncio
import csv
import io
import json
import logging
import os
import statistics
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite
import paho.mqtt.client as mqtt
from fastapi import FastAPI, HTTPException, Query, WebSocket
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

import reloj
from db import BaseDatos
from ml_service import Clasificador, data_desde_readings
from models import Availability, Comando, Telemetry

BASE = Path(__file__).resolve().parent
LOG = logging.getLogger("smartgate")
LOG.setLevel(logging.INFO)
if not LOG.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOG.addHandler(handler)

NODO_PATRON = r"^[A-Za-z0-9_-]{1,64}$"
COMANDO_TIMEOUT_MS = 10000


def now_ms():
    return int(time.time() * 1000)


def percentil(valores, p):
    orden = sorted(valores)
    return orden[min(len(orden) - 1, int(round(p / 100 * (len(orden) - 1))))]


class PeticionControl(BaseModel):
    accion: str
    valor: int | None = None
    node_id: str = "cochera-01"


def create_app(db_path=None, mqtt_enabled=True, sincronizar_reloj=True):
    database = Path(db_path or os.getenv("DATABASE_PATH", BASE / "data" / "smartgate.db"))
    broker = os.getenv("MQTT_HOST", "127.0.0.1")
    port = int(os.getenv("MQTT_PORT", "1883"))
    stale_ms = int(os.getenv("STALE_MS", "7000"))
    nodes, availability, sockets = {}, {}, {}
    streams = {}  # Clientes de /ws/telemetria (app movil): reciben cada JSON tal cual.
    stream_nodes = {}  # Nodo que sigue cada cliente de /ws/telemetria; None = todos.
    counters = {"accepted": 0, "duplicates": 0, "invalid": 0, "dropped": 0}
    runtime = {"mqtt_connected": False, "desfase_ms": None, "desfase_medido": None}
    queue = asyncio.Queue(maxsize=1000)
    lock = asyncio.Lock()
    stats = {}
    pendientes = {}  # Comandos publicados que la ESP32 aun no confirma: id -> datos.
    bitacora = {}    # Ultimos comandos (id -> ultimo estado) para que el dashboard los muestre al abrir.
    clasificador = Clasificador()
    orm = BaseDatos(database)
    siguiente_id = [int(time.time()) - 1_790_000_000]  # Crece entre reinicios del backend.
    publicados = []  # Sin broker (pruebas) los comandos se guardan aqui.

    # Calidad del enlace por nodo y arranque. P sigue la formula de la guia:
    # P = (1 - recibidos / (secuencia_max - secuencia_min + 1)) x 100.
    def update_stats(data, received, event):
        s = stats.get(data.node_id)
        if s is None or s["boot_id"] != data.boot_id:
            s = stats[data.node_id] = {"boot_id": data.boot_id, "last_seq": data.sequence_id - 1, "last_arrival": None,
                                       "received": 0, "lost": 0, "intervals": deque(maxlen=200),
                                       "publish_us": deque(maxlen=200), "qos": None, "seq_min": data.sequence_id,
                                       "seq_max": data.sequence_id, "latencias": deque(maxlen=2000),
                                       "inferencia_us": deque(maxlen=500), "procesamiento_ms": deque(maxlen=500),
                                       "periodo_ms": None}
        if data.sequence_id > s["last_seq"]:
            s["lost"] += data.sequence_id - s["last_seq"] - 1
            s["last_seq"] = data.sequence_id
        else:
            s["lost"] = max(0, s["lost"] - 1)  # Llego tarde: ya se habia contado como perdido.
        s["seq_min"], s["seq_max"] = min(s["seq_min"], data.sequence_id), max(s["seq_max"], data.sequence_id)
        s["received"] += 1
        if s["last_arrival"] is not None:
            s["intervals"].append(received - s["last_arrival"])
        s["last_arrival"] = received
        if data.network.publish_us is not None:
            s["publish_us"].append(data.network.publish_us)
        if event["latencia_ms"] is not None:
            s["latencias"].append(event["latencia_ms"])
        if event["rf"]["inferencia_us"] is not None:
            s["inferencia_us"].append(event["rf"]["inferencia_us"])
        s["qos"] = data.network.qos
        if data.control:
            s["periodo_ms"] = data.control.periodo_ms

    def stats_view():
        result = {}
        for node_id, s in stats.items():
            intervals, publish, lat, inf = list(s["intervals"]), list(s["publish_us"]), list(s["latencias"]), list(s["inferencia_us"])
            proc = list(s["procesamiento_ms"])
            expected = s["received"] + s["lost"]
            rango = s["seq_max"] - s["seq_min"] + 1
            result[node_id] = {
                "boot_id": s["boot_id"], "qos": s["qos"], "periodo_ms": s["periodo_ms"],
                "received": s["received"], "lost": s["lost"],
                "loss_pct": round(100 * s["lost"] / expected, 2) if expected else 0.0,
                "seq_min": s["seq_min"], "seq_max": s["seq_max"],
                "p_pct": round((1 - s["received"] / rango) * 100, 2) if rango > 0 else 0.0,
                "mean_interval_ms": round(statistics.fmean(intervals), 1) if intervals else None,
                "jitter_ms": round(statistics.pstdev(intervals), 1) if len(intervals) > 1 else None,
                "publish_us_avg": round(statistics.fmean(publish)) if publish else None,
                "publish_us_max": max(publish) if publish else None,
                "latencia_prom_ms": round(statistics.fmean(lat), 1) if lat else None,
                "latencia_p95_ms": round(percentil(lat, 95), 1) if lat else None,
                "latencia_max_ms": round(max(lat), 1) if lat else None,
                "inferencia_us_prom": round(statistics.fmean(inf)) if inf else None,
                "procesamiento_ms_prom": round(statistics.fmean(proc), 2) if proc else None,
                "procesamiento_ms_max": round(max(proc), 2) if proc else None,
            }
        return result

    def snapshot():
        result = []
        for node_id, event in nodes.items():
            age = max(0, now_ms() - event["received_at"])
            status = availability.get(node_id)
            explicitly_offline = status is not None and status["boot_id"] == event["boot_id"] and not status["online"]
            result.append({"node_id": node_id, "online": runtime["mqtt_connected"] and age < stale_ms and not explicitly_offline,
                           "age_ms": age, "latest": event})
        return {"type": "snapshot", "server_time": now_ms(), "broker_connected": runtime["mqtt_connected"],
                "stale_ms": stale_ms, "nodes": result, "counters": counters.copy(), "stats": stats_view(),
                "modelo_rf": clasificador.listo, "desfase_reloj_ms": runtime["desfase_ms"]}

    async def broadcast(message, destinos=None):
        if message.get("type") == "comando" and message.get("id") is not None and destinos is None:
            bitacora[message["id"]] = message | {"t": now_ms()}
            while len(bitacora) > 20:
                bitacora.pop(next(iter(bitacora)))
        # Una cola por cliente evita que un telefono lento detenga la ingesta.
        for outgoing in list((sockets if destinos is None else destinos).values()):
            if outgoing.full():
                outgoing.get_nowait()
            outgoing.put_nowait(message)

    async def confirmar_comandos(node_id, ultimo_cmd):
        for cmd_id in [i for i, p in pendientes.items() if p["node_id"] == node_id and i <= ultimo_cmd]:
            p = pendientes.pop(cmd_id)
            rtt = now_ms() - p["t"]
            LOG.info("Comando #%s %s confirmado por la ESP32 en %s ms", cmd_id, p["accion"], rtt)
            await broadcast({"type": "comando", "id": cmd_id, "accion": p["accion"], "valor": p["valor"],
                             "estado": "confirmado", "rtt_ms": rtt})

    async def ingest(topic, raw, retained=False):
        try:
            parts = topic.split("/")
            if len(parts) != 3 or parts[0] != "smartgate" or parts[2] not in ("telemetry", "status"):
                raise ValueError("Topic no permitido")
            if len(raw) > 16384:
                raise ValueError("Payload demasiado grande")
            if parts[2] == "status":
                status = Availability.model_validate_json(raw)
                if parts[1] != status.node_id:
                    raise ValueError("node_id no coincide con topic")
                availability[status.node_id] = status.model_dump()
                return
            if retained:
                raise ValueError("Telemetria retenida no representa una lectura nueva")
            data = Telemetry.model_validate_json(raw)
            if parts[1] != data.node_id:
                raise ValueError("node_id no coincide con topic")
        except (ValidationError, ValueError):
            counters["invalid"] += 1
            LOG.warning("JSON rechazado en %s", topic)
            return False

        received = now_ms()
        inicio = time.perf_counter()  # Procesamiento en el backend: inferencia + SQLite + ORM + reparto.
        event = data.model_dump()
        event["received_at"] = received
        envio = data.timestamp_ms or data.timestamp
        event["latency_ms"] = received - data.timestamp if data.timestamp is not None else None
        # L = t_recepcion - t_envio, con t_recepcion llevada a hora NTP (desfase medido de la laptop).
        event["latencia_ms"] = round(received + (runtime["desfase_ms"] or 0) - envio, 1) if envio else None
        event["data"] = event["data"] or data_desde_readings(event)
        clase, probabilidad, inferencia = clasificador.clasificar(event["data"])
        event["rf"] = {"clase": clase, "probabilidad": probabilidad, "inferencia_us": inferencia}
        async with lock:
            cursor = await app.state.db.execute(
                "INSERT OR IGNORE INTO readings(node_id,boot_id,sequence_id,received_at,payload) VALUES(?,?,?,?,?)",
                (data.node_id, data.boot_id, data.sequence_id, received, json.dumps(event, allow_nan=False)))
            inserted = cursor.rowcount == 1
            await app.state.db.commit()
        if not inserted:
            counters["duplicates"] += 1
            return False
        await orm.guardar(node_id=data.node_id, boot_id=data.boot_id, sequence_id=data.sequence_id,
                          variables_json=json.dumps(event["data"], allow_nan=False), clase_predicha_rf=clase,
                          probabilidad_rf=probabilidad, latencia_ms=event["latencia_ms"], puerta=data.door.state,
                          fuente=data.source)
        counters["accepted"] += 1
        update_stats(data, received, event)
        old = nodes.get(data.node_id)
        if old is None or old["boot_id"] != data.boot_id or data.sequence_id > old["sequence_id"]:
            nodes[data.node_id] = event
        await broadcast({"type": "telemetry", "data": event})
        await broadcast(event, {ws: q for ws, q in streams.items() if stream_nodes.get(ws) in (None, data.node_id)})
        if data.control:
            await confirmar_comandos(data.node_id, data.control.ultimo_cmd)
        stats[data.node_id]["procesamiento_ms"].append((time.perf_counter() - inicio) * 1000)
        LOG.info("[MQTT Inbox] %s", raw.decode("utf-8"))
        LOG.info("Guardado %s #%s | RF=%s (%s, %s us) | L=%s ms", data.node_id, data.sequence_id, clase,
                 probabilidad, inferencia, event["latencia_ms"])
        return True

    async def enviar_comando(node_id, comando):
        if mqtt_enabled and not runtime["mqtt_connected"]:
            raise HTTPException(503, "Broker MQTT desconectado")
        siguiente_id[0] += 1
        cmd_id = siguiente_id[0]
        mensaje = {"id": cmd_id, "accion": comando.accion, "valor": comando.valor}
        topic = f"smartgate/{node_id}/control"
        if mqtt_enabled:
            app.state.mqtt.publish(topic, json.dumps(mensaje), qos=1)
        else:
            publicados.append((topic, mensaje))
        pendientes[cmd_id] = {"node_id": node_id, "accion": comando.accion, "valor": comando.valor, "t": now_ms()}
        LOG.info("Comando #%s publicado en %s: %s %s", cmd_id, topic, comando.accion, comando.valor or "")
        await broadcast({"type": "comando", "id": cmd_id, "accion": comando.accion, "valor": comando.valor, "estado": "enviado"})
        return mensaje

    async def consume():
        while True:
            topic, raw, retained = await queue.get()
            try:
                await ingest(topic, raw, retained)
            except Exception:
                counters["dropped"] += 1
                LOG.exception("No se pudo persistir telemetria")
            finally:
                queue.task_done()

    async def heartbeat():
        while True:
            for cmd_id in [i for i, p in pendientes.items() if now_ms() - p["t"] > COMANDO_TIMEOUT_MS]:
                p = pendientes.pop(cmd_id)
                await broadcast({"type": "comando", "id": cmd_id, "accion": p["accion"], "valor": p["valor"], "estado": "sin_confirmar"})
            await broadcast(snapshot())
            await asyncio.sleep(1)

    async def medir_reloj():
        while True:
            try:
                runtime["desfase_ms"] = await asyncio.to_thread(reloj.desfase_ms)
                runtime["desfase_medido"] = now_ms()
                LOG.info("Desfase NTP de la laptop: %s ms", runtime["desfase_ms"])
            except OSError as e:
                LOG.warning("No se pudo medir el desfase NTP: %s", e)
            await asyncio.sleep(600)

    @asynccontextmanager
    async def lifespan(app):
        database.parent.mkdir(parents=True, exist_ok=True)
        db = await aiosqlite.connect(database)
        app.state.db = db
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("CREATE TABLE IF NOT EXISTS readings(id INTEGER PRIMARY KEY,node_id TEXT NOT NULL,boot_id TEXT NOT NULL,sequence_id INTEGER NOT NULL,received_at INTEGER NOT NULL,payload TEXT NOT NULL,UNIQUE(node_id,boot_id,sequence_id))")
        await db.execute("CREATE INDEX IF NOT EXISTS readings_node ON readings(node_id,id)")
        await db.commit()
        await orm.crear()
        async with db.execute("SELECT payload FROM readings WHERE id IN (SELECT MAX(id) FROM readings GROUP BY node_id)") as cursor:
            for row in await cursor.fetchall():
                event = json.loads(row[0]); nodes[event["node_id"]] = event
        LOG.info("Modelo Random Forest %s", "cargado" if clasificador.listo else "NO encontrado: se guarda sin clase")
        loop = asyncio.get_running_loop()
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="smartgate-backend-" + uuid.uuid4().hex[:10])
        app.state.mqtt = client
        if os.getenv("MQTT_USER"):
            client.username_pw_set(os.environ["MQTT_USER"], os.getenv("MQTT_PASSWORD"))
        client.reconnect_delay_set(1, 10)

        def connected(client, userdata, flags, reason, properties):
            runtime["mqtt_connected"] = reason == 0
            if reason == 0:
                client.subscribe([("smartgate/+/telemetry", 1), ("smartgate/+/status", 1)])

        def disconnected(client, userdata, flags, reason, properties):
            runtime["mqtt_connected"] = False

        def put(topic, raw, retained):
            try:
                queue.put_nowait((topic, raw, retained))
            except asyncio.QueueFull:
                counters["dropped"] += 1

        def message(client, userdata, msg):
            loop.call_soon_threadsafe(put, msg.topic, bytes(msg.payload), bool(msg.retain))

        client.on_connect, client.on_disconnect, client.on_message = connected, disconnected, message
        tareas = [asyncio.create_task(heartbeat())]
        if sincronizar_reloj:
            tareas.append(asyncio.create_task(medir_reloj()))
        worker = asyncio.create_task(consume())
        if mqtt_enabled:
            client.connect_async(broker, port, 15); client.loop_start()
        try:
            yield
        finally:
            if mqtt_enabled:
                client.disconnect(); await asyncio.to_thread(client.loop_stop)
            for tarea in tareas:
                tarea.cancel()
            await asyncio.gather(*tareas, return_exceptions=True)
            try:
                await asyncio.wait_for(queue.join(), 5)
            except asyncio.TimeoutError:
                LOG.warning("Cola pendiente al apagar")
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            await orm.cerrar()
            await db.close()

    app = FastAPI(title="SmartGate IoT · Full-Stack (Random Forest + control bidireccional)", lifespan=lifespan)
    app.state.ingest, app.state.snapshot = ingest, snapshot
    app.state.runtime, app.state.counters = runtime, counters
    app.state.publicados, app.state.enviar_comando = publicados, enviar_comando
    app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")

    @app.get("/")
    async def home():
        return FileResponse(BASE / "static" / "dashboard.html", headers={"Cache-Control": "no-cache"})

    @app.get("/v2")
    async def dashboard_practica2():
        return FileResponse(BASE / "static" / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/sw.js")
    async def service_worker():
        return FileResponse(BASE / "static" / "sw.js", media_type="application/javascript", headers={"Cache-Control": "no-cache"})

    @app.get("/health")
    async def health():
        return snapshot()

    @app.get("/stats")
    async def get_stats():
        return {"server_time": now_ms(), "counters": counters.copy(), "nodes": stats_view(),
                "desfase_reloj_ms": runtime["desfase_ms"]}

    @app.post("/stats/reset")
    async def reset_stats():
        # Para comparar corridas (QoS o frecuencia de envio) sin mezclarlas.
        stats.clear()
        return {"reset": True}

    @app.post("/api/control")
    async def control(peticion: PeticionControl):
        try:
            comando = Comando(accion=peticion.accion, valor=peticion.valor)
        except ValidationError as e:
            raise HTTPException(422, str(e.errors()[0]["msg"]))
        return await enviar_comando(peticion.node_id, comando)

    @app.get("/api/comandos")
    async def comandos():
        return sorted(bitacora.values(), key=lambda c: c["id"])

    @app.get("/api/eventos")
    async def eventos(node_id: str = Query("cochera-01", pattern=NODO_PATRON), limit: int = Query(20, ge=1, le=5000)):
        return await orm.ultimos(node_id, limit)

    @app.get("/api/readings")
    async def readings(node_id: str | None = Query(None, pattern=NODO_PATRON), limit: int = Query(120, ge=1, le=1000)):
        sql = "SELECT payload FROM readings"
        args = []
        if node_id is not None:
            sql += " WHERE node_id=?"; args.append(node_id)
        sql += " ORDER BY id DESC LIMIT ?"; args.append(limit)
        async with lock:
            async with app.state.db.execute(sql, args) as cursor:
                rows = await cursor.fetchall()
        return [json.loads(row[0]) for row in reversed(rows)]

    @app.get("/api/export.csv")
    async def export(node_id: str | None = Query(None, pattern=NODO_PATRON)):
        rows = await readings(node_id, 1000)
        stream = io.StringIO()
        fields = ["node_id", "boot_id", "sequence_id", "timestamp", "received_at", "source", "exterior_raw_mm", "exterior_filtered_mm", "interior_raw_mm", "interior_filtered_mm", "door_state", "reed_closed", "tag_present"]
        writer = csv.DictWriter(stream, fields); writer.writeheader()
        for row in rows:
            values = {k: row[k] for k in fields[:6]}
            for side in ("exterior", "interior"):
                for kind in ("raw_mm", "filtered_mm"):
                    values[f"{side}_{kind}"] = row["readings"][side][kind]
            values.update(door_state=row["door"]["state"], reed_closed=row["door"]["reed_closed"],
                          tag_present=row["tag"]["present"] if row.get("tag") else "")
            writer.writerow(values)
        return Response(stream.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=smartgate_ultimas_1000.csv"})

    async def serve(ws, destinos, primeros, al_recibir=None):
        await ws.accept()
        outgoing = asyncio.Queue(maxsize=10); destinos[ws] = outgoing

        async def send():
            for message in primeros():
                await ws.send_json(message)
            while True:
                await asyncio.wait_for(ws.send_json(await outgoing.get()), 5)

        async def receive():
            while True:
                texto = await ws.receive_text()
                if al_recibir:
                    await al_recibir(ws, texto)

        sender, receiver = asyncio.create_task(send()), asyncio.create_task(receive())
        try:
            await asyncio.wait([sender, receiver], return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            pass
        finally:
            destinos.pop(ws, None)
            sender.cancel(); receiver.cancel()
            try:
                await asyncio.gather(sender, receiver, return_exceptions=True)
            except asyncio.CancelledError:
                pass

    async def comando_por_websocket(ws, texto):
        """El dashboard manda {"tipo":"comando","accion":"abrir"}; cualquier otro texto es keepalive."""
        try:
            mensaje = json.loads(texto)
        except json.JSONDecodeError:
            return
        if not isinstance(mensaje, dict) or mensaje.get("tipo") != "comando":
            return
        try:
            comando = Comando(accion=mensaje.get("accion"), valor=mensaje.get("valor"))
            await enviar_comando(mensaje.get("node_id", "cochera-01"), comando)
        except (ValidationError, HTTPException) as e:
            detalle = e.detail if isinstance(e, HTTPException) else e.errors()[0]["msg"]
            await broadcast({"type": "comando", "accion": mensaje.get("accion"), "estado": "rechazado", "detalle": detalle},
                            {ws: sockets[ws]} if ws in sockets else {})

    @app.websocket("/ws")
    async def live(ws: WebSocket):
        await serve(ws, sockets, lambda: [snapshot()], comando_por_websocket)

    # Endpoint de la guia de la practica 2: cada telemetria JSON tal cual llega por MQTT.
    # Al conectar envia la ultima lectura conocida de cada nodo para que la app no empiece vacia.
    @app.websocket("/ws/telemetria")
    async def telemetria(ws: WebSocket, node_id: str | None = Query(None, pattern=NODO_PATRON)):
        stream_nodes[ws] = node_id
        try:
            await serve(ws, streams, lambda: [e for n, e in nodes.items() if node_id in (None, n)])
        finally:
            stream_nodes.pop(ws, None)

    return app


app = create_app()
