"""Genera JSON de demostracion y lo publica a Mosquitto; no acciona hardware."""
import argparse
import json
import random
import time
import uuid
from collections import deque
import paho.mqtt.client as mqtt


def sample(node_id, boot_id, sequence, start, buffers, invalid=False):
    phase = sequence % 36
    state = "CERRADA" if phase < 5 or phase >= 22 else "ABRIENDO" if phase < 8 else "APERTURA_ORDENADA" if phase < 18 else "CERRANDO"
    inside = phase >= 12
    exterior = 650 if phase < 2 or phase >= 14 else 180
    interior = 80 if inside else 350
    readings = {}
    for side, mean in [("exterior", exterior), ("interior", interior)]:
        raw = mean + random.randint(-12, 12)
        if invalid and side == "interior":
            buffers[side].clear()
            readings[side] = {"raw_mm": 8191, "filtered_mm": None, "valid": False, "sample_count": 0, "error": "SIN_RETORNO"}
        else:
            buffers[side].append(raw)
            readings[side] = {"raw_mm": raw, "filtered_mm": round(sum(buffers[side])/len(buffers[side]),1), "valid": True, "sample_count": len(buffers[side]), "error": None}
    return {"schema_version":1,"node_id":node_id,"boot_id":boot_id,"sequence_id":sequence,
            "timestamp":int(time.time()*1000),"time_synced":True,"uptime_ms":int((time.monotonic()-start)*1000),
            "source":"simulation","filter_window":5,"readings":readings,
            "door":{"state":state,"mode":"AUTO","reed_closed":state=="CERRADA","servo_command_deg":15 if state=="CERRADA" else 180 if state=="APERTURA_ORDENADA" else 90},
            "vehicle":{"inside":inside,"arrival":inside,"rearmed":phase<5,"exterior_clear":phase>=16 or phase<2},
            "tag":{"present":2<=phase<32,"rssi_dbm":-55},
            "network":{"wifi_connected":True,"mqtt_connected":True,"heap_free":90000}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host",default="127.0.0.1");parser.add_argument("--port",type=int,default=1883)
    parser.add_argument("--node",default="demo-cochera");parser.add_argument("--count",type=int,default=0)
    parser.add_argument("--interval",type=float,default=1);parser.add_argument("--pause-at",type=int,default=-1)
    parser.add_argument("--pause-seconds",type=float,default=10);parser.add_argument("--invalid-at",type=int,default=-1)
    args=parser.parse_args();boot=uuid.uuid4().hex[:16];start=time.monotonic()
    client=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,client_id="smartgate-demo-"+boot)
    topic=f"smartgate/{args.node}/telemetry";status=f"smartgate/{args.node}/status"
    def availability(online):return json.dumps({"node_id":args.node,"boot_id":boot,"online":online})
    client.will_set(status,availability(False),qos=1,retain=True)
    client.connect(args.host,args.port,15);client.loop_start()
    client.publish(status,availability(True),qos=1,retain=True).wait_for_publish(5)
    buffers={s:deque(maxlen=5) for s in ("exterior","interior")}
    sequence=0
    try:
        while not args.count or sequence<args.count:
            if sequence==args.pause_at:
                print(f"PAUSA SIMULADA de {args.pause_seconds}s",flush=True);time.sleep(args.pause_seconds)
            payload=sample(args.node,boot,sequence,start,buffers,sequence==args.invalid_at)
            encoded=json.dumps(payload,separators=(",",":"))
            client.publish(topic,encoded,qos=1,retain=False).wait_for_publish(5)
            print(encoded,flush=True);sequence+=1;time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        client.publish(status,availability(False),qos=1,retain=True).wait_for_publish(5)
        client.disconnect();client.loop_stop()


if __name__=="__main__":main()
