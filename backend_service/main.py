import os
import json
import time
import datetime
from threading import Lock
import paho.mqtt.client as mqtt
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from urllib.parse import urlparse

# Environment variables
mqtt_parsed = urlparse(os.environ.get('MQTT_URL', 'mqtt://localhost:1883'))
MQTT_HOST = mqtt_parsed.hostname or 'localhost'
MQTT_PORT = mqtt_parsed.port or 1883

INFLUX_URL = os.environ.get('INFLUX_URL', 'http://localhost:8086')
INFLUX_TOKEN = os.environ.get('INFLUX_TOKEN', 'supersecrettoken')
INFLUX_ORG = os.environ.get('INFLUX_ORG', 'cow_farm')
INFLUX_BUCKET = os.environ.get('INFLUX_BUCKET', 'cow_metrics')

print(f"Configuring InfluxDB at {INFLUX_URL}")
influx_client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
write_api = influx_client.write_api(write_options=SYNCHRONOUS)

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

alerts = []
last_alert_time = {}
state_lock = Lock()
cow_states = {}

thresholds = {
    "temp_warning_high": 39.0,
    "temp_critical_high": 39.5,
    "temp_warning_low": 37.5,
    "temp_critical_low": 37.0,
    "activity_warning_low": 40,
    "activity_critical_low": 30,
    "activity_warning_high": 120,
    "activity_critical_high": 150,
    "cooldown_sec": 30
}

def add_alert(cow_id, message, level):
    now_ms = int(time.time() * 1000)
    cooldown_ms = thresholds["cooldown_sec"] * 1000
    if cow_id in last_alert_time and (now_ms - last_alert_time[cow_id] < cooldown_ms):
        return

    timestamp = datetime.datetime.now().strftime('%d.%m.%Y, %H:%M:%S')
    
    new_alert = {
        "id": now_ms,
        "timestamp": timestamp,
        "cowId": cow_id,
        "message": message,
        "level": level
    }
    
    alerts.insert(0, new_alert)
    if len(alerts) > 50:
        alerts.pop()
    last_alert_time[cow_id] = now_ms

active_alerts = {}

def get_cow_status(cow_id, temp, activity):
    if temp > thresholds["temp_critical_high"] or temp < thresholds["temp_critical_low"]:
        return {"level": "danger", "message": f"Критическая температура: {temp}°C"}
    if activity < thresholds["activity_critical_low"] or activity > thresholds["activity_critical_high"]:
        return {"level": "danger", "message": f"Критическая активность: {activity}"}
    if temp > thresholds["temp_warning_high"] or temp < thresholds["temp_warning_low"]:
        return {"level": "warning", "message": f"Повышенная температура: {temp}°C"}
    if activity < thresholds["activity_warning_low"] or activity > thresholds["activity_warning_high"]:
        return {"level": "warning", "message": f"Аномальная активность: {activity}"}
    return None

@app.get("/api/alerts")
def get_alerts():
    return alerts

@app.get("/api/active")
def get_active_alerts():
    return active_alerts

@app.get("/api/cows")
def get_cows():
    with state_lock:
        return cow_states

@app.get("/api/thresholds")
def get_thresholds():
    return thresholds

@app.post("/api/thresholds")
async def update_thresholds(new_thresholds: dict):
    for key in new_thresholds:
        if key in thresholds:
            thresholds[key] = float(new_thresholds[key])
    return thresholds

def calculate_illness_probability(temp, activity):
    prob = 0.0

    if temp > thresholds["temp_critical_high"]:
        prob += (temp - thresholds["temp_critical_high"]) * 30
    elif temp > thresholds["temp_warning_high"]:
        prob += (temp - thresholds["temp_warning_high"]) * 15
    elif temp < thresholds["temp_critical_low"]:
        prob += (thresholds["temp_critical_low"] - temp) * 30
    elif temp < thresholds["temp_warning_low"]:
        prob += (thresholds["temp_warning_low"] - temp) * 15

    if activity < thresholds["activity_critical_low"]:
        prob += (thresholds["activity_critical_low"] - activity) * 1.5
    elif activity < thresholds["activity_warning_low"]:
        prob += (thresholds["activity_warning_low"] - activity) * 0.8
    elif activity > thresholds["activity_critical_high"]:
        prob += 10
    elif activity > thresholds["activity_warning_high"]:
        prob += 5

    return min(max(prob, 0.0), 100.0)

def on_connect(client, userdata, flags, rc):
    print(f"Connected to MQTT Broker with result code {rc}")
    client.subscribe("cow/+/+")

def on_message(client, userdata, msg):
    try:
        topic = msg.topic
        parts = topic.split('/')
        if len(parts) < 3:
            return
        
        cow_id = parts[1]
        sensor_type = parts[2]
        payload_str = msg.payload.decode('utf-8')
        
        with state_lock:
            if cow_id not in cow_states:
                cow_states[cow_id] = {"temperature": 38.5, "activity": 50.0, "lat": 55.75, "lon": 37.61}
            
            if sensor_type == 'data':
                data = json.loads(payload_str)
                if 'temperature' in data: cow_states[cow_id]['temperature'] = float(data['temperature'])
                if 'activity' in data: cow_states[cow_id]['activity'] = float(data['activity'])
                if 'lat' in data: cow_states[cow_id]['lat'] = float(data['lat'])
                if 'lon' in data: cow_states[cow_id]['lon'] = float(data['lon'])
            elif sensor_type in ['temperature', 'activity', 'lat', 'lon']:
                cow_states[cow_id][sensor_type] = float(payload_str)
            else:
                return

            state = cow_states[cow_id]
            temp = state['temperature']
            activity = state['activity']

        illness_prob = calculate_illness_probability(temp, activity)
        print(f"Cow {cow_id} | Temp: {temp} | Act: {activity} | Prob: {illness_prob:.1f}%")

        status = get_cow_status(cow_id, temp, activity)
        if status:
            active_alerts[cow_id] = {
                "cowId": cow_id,
                "level": status["level"],
                "message": status["message"],
                "temperature": temp,
                "activity": activity,
                "illness_probability": round(illness_prob, 1),
                "updated": datetime.datetime.now().strftime('%d.%m.%Y, %H:%M:%S')
            }
            add_alert(cow_id, status["message"], status["level"])
        else:
            active_alerts.pop(cow_id, None)

        if illness_prob >= 60:
            add_alert(cow_id, f"Высокая вероятность заболевания ({int(illness_prob)}%). Темп: {temp}°C, Акт: {activity}", 'danger')

        # Write to InfluxDB
        point = Point("cow_telemetry") \
            .tag("cow_id", cow_id) \
            .field("temperature", float(temp)) \
            .field("activity", float(activity)) \
            .field("illness_probability", float(illness_prob)) \
            .field("lat", float(state['lat'])) \
            .field("lon", float(state['lon']))
        
        write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=point)
            
    except Exception as e:
        print(f"Error processing MQTT message: {e}")

mqtt_client = mqtt.Client()
mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message

print(f"Connecting to MQTT broker at {MQTT_HOST}:{MQTT_PORT}")
mqtt_client.connect(MQTT_HOST, MQTT_PORT, 60)
mqtt_client.loop_start()

WEB_APP_DIR = os.environ.get('WEB_APP_DIR', '/app/web_app')

if os.path.isdir(WEB_APP_DIR):
    from starlette.staticfiles import StaticFiles as StarletteStatic
    app.mount("/static", StarletteStatic(directory=WEB_APP_DIR), name="static_files")
    print(f"Serving web app from {WEB_APP_DIR}")

    from starlette.responses import FileResponse

    @app.get("/")
    async def serve_index():
        return FileResponse(os.path.join(WEB_APP_DIR, "index.html"))

    @app.get("/{filename}.html")
    async def serve_page(filename: str):
        filepath = os.path.join(WEB_APP_DIR, f"{filename}.html")
        if os.path.isfile(filepath):
            return FileResponse(filepath)
        return {"error": "not found"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=3001)
