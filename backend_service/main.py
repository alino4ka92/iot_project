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
from fastapi.staticfiles import StaticFiles
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

def add_alert(cow_id, message, level):
    now_ms = int(time.time() * 1000)
    if cow_id in last_alert_time and (now_ms - last_alert_time[cow_id] < 30000):
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

@app.get("/api/alerts")
def get_alerts():
    return alerts

@app.get("/api/cows")
def get_cows():
    with state_lock:
        return cow_states

def calculate_illness_probability(temp, activity):
    prob = 0.0

    if temp > 39.5:
        prob += (temp - 39.5) * 30
    elif temp < 37.5:
        prob += (37.5 - temp) * 30

    if activity < 30:
        prob += (30 - activity) * 1.5
    elif activity > 150:
        prob += 10

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

        if illness_prob >= 60:
            add_alert(cow_id, f"Высокая вероятность заболевания ({int(illness_prob)}%). Темп: {temp}°C, Акт: {activity}", 'danger')
        elif temp > 39.5 or temp < 37.5:
            add_alert(cow_id, f"Аномальная температура: {temp}°C", 'warning')
        elif activity < 30 or activity > 150:
            add_alert(cow_id, f"Аномальная активность: {activity}", 'warning')

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
    app.mount("/", StaticFiles(directory=WEB_APP_DIR, html=True), name="static")
    print(f"Serving web app from {WEB_APP_DIR}")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=3001)
