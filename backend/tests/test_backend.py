import json
import sys
import tempfile
import time
import unittest
from collections import deque
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/"backend"))
from fastapi.testclient import TestClient
from main import create_app
from simulator import sample


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db=Path(self.temp.name)/"test.db"
        self.app=create_app(self.db,mqtt_enabled=False,sincronizar_reloj=False)
        self.client=TestClient(self.app);self.client.__enter__()
        self.data=sample("test-cochera","boot-a",0,time.monotonic(),{s:deque(maxlen=5) for s in ("exterior","interior")})

    def tearDown(self):
        self.client.__exit__(None,None,None);self.temp.cleanup()

    def send(self,data=None,topic="smartgate/test-cochera/telemetry",retained=False):
        raw=json.dumps(self.data if data is None else data).encode()
        return self.client.portal.call(self.app.state.ingest,topic,raw,retained)

    def test_persistence_and_csv(self):
        self.assertTrue(self.send())
        rows=self.client.get("/api/readings").json()
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]["sequence_id"],0)
        self.assertEqual(rows[0]["source"],"simulation")
        self.assertIsInstance(rows[0]["received_at"],int)
        self.assertIn("exterior_filtered_mm",self.client.get("/api/export.csv").text)

    def test_duplicate_and_new_boot(self):
        self.send();self.assertFalse(self.send())
        self.data["boot_id"]="boot-b";self.assertTrue(self.send())
        self.assertEqual(len(self.client.get("/api/readings").json()),2)
        self.assertEqual(self.app.state.counters["duplicates"],1)

    def test_invalid_sensor_must_be_null(self):
        self.data["readings"]["interior"]={"raw_mm":8191,"filtered_mm":None,"valid":False,"sample_count":0,"error":"SIN_RETORNO"}
        self.assertTrue(self.send())
        self.data["sequence_id"]=1;self.data["readings"]["interior"]["filtered_mm"]=8191
        self.assertFalse(self.send());self.assertEqual(self.app.state.counters["invalid"],1)

    def test_clock_without_ntp(self):
        self.data["timestamp"]=None;self.data["time_synced"]=False
        self.assertTrue(self.send());self.assertIsNone(self.client.get("/api/readings").json()[0]["latency_ms"])
        self.data["sequence_id"]=1;self.data["time_synced"]=True
        self.assertFalse(self.send())

    def test_topic_mismatch_and_retained(self):
        self.assertFalse(self.send(topic="smartgate/otra/telemetry"))
        self.assertFalse(self.send(retained=True));self.assertEqual(self.client.get("/api/readings").json(),[])

    def test_range_strict_bool_and_required_fields(self):
        for key,value in [("sequence_id",-1),("sequence_id","1"),("timestamp",0),("source","fake")]:
            data=json.loads(json.dumps(self.data));data[key]=value;self.assertFalse(self.send(data))
        data=json.loads(json.dumps(self.data));data["door"]["reed_closed"]="false";self.assertFalse(self.send(data))
        data=json.loads(json.dumps(self.data));del data["node_id"];self.assertFalse(self.send(data))
        data=json.loads(json.dumps(self.data));data["readings"]["interior"]["raw_mm"]=8191;self.assertFalse(self.send(data))

    def test_non_finite_and_broken_json(self):
        self.data["readings"]["interior"]["filtered_mm"]=float("nan");self.assertFalse(self.send())
        self.assertFalse(self.client.portal.call(self.app.state.ingest,"smartgate/test-cochera/telemetry",b"{"))

    def test_stale_and_lwt_not_confused_with_websocket(self):
        self.send();self.app.state.runtime["mqtt_connected"]=True
        self.assertTrue(self.app.state.snapshot()["nodes"][0]["online"])
        self.send({"node_id":"test-cochera","boot_id":"boot-a","online":False},"smartgate/test-cochera/status")
        self.assertFalse(self.app.state.snapshot()["nodes"][0]["online"])
        self.send({"node_id":"test-cochera","boot_id":"boot-a","online":True},"smartgate/test-cochera/status")
        self.app.state.snapshot()["nodes"][0]["latest"]["received_at"]=int(time.time()*1000)-10000
        self.assertFalse(self.app.state.snapshot()["nodes"][0]["online"])

    def test_out_of_order_and_websocket(self):
        self.data["sequence_id"]=10;self.send()
        self.data["sequence_id"]=2;self.send()
        self.assertEqual(self.app.state.snapshot()["nodes"][0]["latest"]["sequence_id"],10)
        with self.client.websocket_connect("/ws") as ws:
            self.assertEqual(ws.receive_json()["type"],"snapshot")
            self.data["sequence_id"]=11;self.send()
            for _ in range(5):
                event=ws.receive_json()
                if event["type"]=="telemetry":break
            self.assertEqual(event["data"]["sequence_id"],11)

    def test_telemetria_stream_for_mobile_app(self):
        self.send()
        with self.client.websocket_connect("/ws/telemetria") as ws:
            self.assertEqual(ws.receive_json()["sequence_id"],0)  # Ultima lectura al conectar.
            self.data["sequence_id"]=1;self.data["door"]["state"]="ABRIENDO";self.send()
            event=ws.receive_json()
            self.assertEqual((event["sequence_id"],event["door"]["state"]),(1,"ABRIENDO"))
            self.assertIn("received_at",event)
        otro=json.loads(json.dumps(self.data));otro["node_id"]="otra-cochera"
        with self.client.websocket_connect("/ws/telemetria?node_id=test-cochera") as ws:
            self.assertEqual(ws.receive_json()["node_id"],"test-cochera")
            self.send(otro,"smartgate/otra-cochera/telemetry")
            self.data["sequence_id"]=2;self.send()
            self.assertEqual(ws.receive_json()["sequence_id"],2)  # El otro nodo no llego.

    def test_restart_restores_history_and_offline_state(self):
        self.send()
        second=create_app(self.db,mqtt_enabled=False,sincronizar_reloj=False)
        with TestClient(second) as other:
            self.assertEqual(len(other.get("/api/readings").json()),1)
            self.assertFalse(other.get("/health").json()["nodes"][0]["online"])

    def test_gateway_payload_without_tag(self):
        del self.data["tag"]
        self.data["door"]["state"]="ABIERTA"
        self.data["readings"]["interior"]={"raw_mm":None,"filtered_mm":None,"valid":False,"sample_count":0,"error":"SIN_OBJETO"}
        self.data["network"].update(wifi_rssi_dbm=-58,qos=1,publish_us=850)
        self.assertTrue(self.send())
        self.assertIsNone(self.client.get("/api/readings").json()[0]["tag"])
        self.assertIn("tag_present",self.client.get("/api/export.csv").text)
        self.data["sequence_id"]=1;self.data["network"]["qos"]=2;self.assertFalse(self.send())

    def test_stats_loss_and_jitter(self):
        for seq in (0,1,2,5,6):
            self.data["sequence_id"]=seq;self.data["network"]["publish_us"]=100*(seq+1);self.send()
        node=self.client.get("/stats").json()["nodes"]["test-cochera"]
        self.assertEqual((node["received"],node["lost"]),(5,2))
        self.assertAlmostEqual(node["loss_pct"],100*2/7,places=1)
        self.assertEqual(node["publish_us_max"],700)
        self.assertEqual(self.client.post("/stats/reset").json(),{"reset":True})
        self.assertEqual(self.client.get("/stats").json()["nodes"],{})

    def p3(self,seq,ultra=4000.0,tof=2000.0,reed=1,apertura=0.0,ultimo_cmd=0):
        """Telemetria con los campos de la practica 3 (data, timestamp_ms y control)."""
        d=json.loads(json.dumps(self.data));d["sequence_id"]=seq
        d["timestamp_ms"]=d["timestamp"]
        d["data"]={"ultrasonico_mm":ultra,"tof_mm":tof,"reed_cerrado":reed,"apertura_pct":apertura}
        d["control"]={"periodo_ms":500,"ultimo_cmd":ultimo_cmd}
        return d

    def test_random_forest_and_orm_persistence(self):
        casos=[(dict(),"Libre"),(dict(ultra=120.0),"Vehículo en acceso"),(dict(tof=80.0),"Vehículo estacionado"),
               (dict(reed=0,apertura=60.0),"Puerta en operación"),(dict(reed=0),"Intrusión"),(dict(ultra=None),"Falla de sensor")]
        for i,(campos,clase) in enumerate(casos):
            self.assertTrue(self.send(self.p3(i,**campos)))
        eventos=self.client.get("/api/eventos?node_id=test-cochera&limit=10").json()
        self.assertEqual([e["clase_predicha_rf"] for e in reversed(eventos)],[c for _,c in casos])
        self.assertEqual(json.loads(eventos[0]["variables_json"])["ultrasonico_mm"],None)
        self.assertIsNotNone(eventos[0]["latencia_ms"]);self.assertIn("fecha_registro",eventos[0])
        ultimo=self.client.get("/api/readings?node_id=test-cochera&limit=1").json()[0]
        self.assertEqual(ultimo["rf"]["clase"],"Falla de sensor");self.assertIsNotNone(ultimo["rf"]["inferencia_us"])

    def test_old_payload_gets_data_for_the_model(self):
        self.assertTrue(self.send())
        evento=self.client.get("/api/readings?limit=1").json()[0]
        self.assertEqual(set(evento["data"]),{"ultrasonico_mm","tof_mm","reed_cerrado","apertura_pct"})
        self.assertIsNotNone(evento["rf"]["clase"])

    def test_control_command_confirmed_by_telemetry(self):
        r=self.client.post("/api/control",json={"accion":"abrir","node_id":"test-cochera"}).json()
        topic,mensaje=self.app.state.publicados[-1]
        self.assertEqual((topic,mensaje["accion"]),("smartgate/test-cochera/control","abrir"))
        self.assertEqual(self.client.post("/api/control",json={"accion":"periodo","valor":250}).status_code,422)
        self.assertEqual(self.client.post("/api/control",json={"accion":"explotar"}).status_code,422)
        with self.client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_text(json.dumps({"tipo":"comando","accion":"periodo","valor":100,"node_id":"test-cochera"}))
            for _ in range(5):
                m=ws.receive_json()
                if m["type"]=="comando":break
            self.assertEqual((m["accion"],m["valor"],m["estado"]),("periodo",100,"enviado"))
            self.send(self.p3(0,ultimo_cmd=m["id"]))
            for _ in range(8):
                m=ws.receive_json()
                if m["type"]=="comando" and m["estado"]=="confirmado" and m["accion"]=="periodo":break
            self.assertEqual(m["estado"],"confirmado");self.assertGreaterEqual(m["rtt_ms"],0)
        self.assertLess(r["id"],m["id"])

    def test_packet_loss_formula(self):
        for seq in (10,11,12,15,16,19):  # 6 recibidos de 10 posibles: P = 40 %.
            self.send(self.p3(seq))
        node=self.client.get("/stats").json()["nodes"]["test-cochera"]
        self.assertEqual((node["seq_min"],node["seq_max"],node["received"],node["p_pct"]),(10,19,6,40.0))
        self.assertIsNotNone(node["latencia_prom_ms"]);self.assertIsNotNone(node["inferencia_us_prom"])

    def test_assets_and_query_bounds(self):
        for path in ("/","/v2","/static/app.js","/static/dashboard.js","/static/manifest.webmanifest","/sw.js"):
            self.assertEqual(self.client.get(path).status_code,200)
        self.assertEqual(self.client.get("/api/readings?limit=0").status_code,422)
        self.assertEqual(self.client.get("/api/readings?node_id=invalid/slash").status_code,422)


if __name__=="__main__":unittest.main(verbosity=2)
