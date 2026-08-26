import threading
import time
import io
import base64
import os
import csv
import hmac
import hashlib
from datetime import datetime, timezone
import serial.tools.list_ports
import math
from playsound import playsound
from flask import Flask, render_template_string, jsonify
import geopandas as gpd
import folium	
import numpy as np
from scipy.interpolate import interp1d
import serial
from shapely.geometry import Point
from folium.features import DivIcon
import json
import sys
import webview
import pandas as pd
import pyodbc
from collections import deque
import webbrowser
from waitress import serve
from pymodbus.client import ModbusTcpClient
import struct


app = Flask(__name__, static_folder='static')
from flask import jsonify

azure_connection_status = {"connected": False, "message": "Not connected"}

error_log = deque(maxlen=1)
has_active_error = False
error_clear_timer = None
ERROR_CLEAR_DELAY = 30

def clear_error_log():
    global has_active_error, error_log
    error_log.clear()
    has_active_error = False
    print("[Info] Error log cleared automatically after no errors.")

def log_error(message):
    global has_active_error, error_clear_timer
    print(f"[Error] {message}")
    error_log.appendleft(f"{datetime.utcnow().isoformat()} - {message}")
    has_active_error = True
    
    # Reset/Start timer to clear errors after delay
    if error_clear_timer and error_clear_timer.is_alive():
        error_clear_timer.cancel()
    error_clear_timer = threading.Timer(ERROR_CLEAR_DELAY, clear_error_log)
    error_clear_timer.start()

GPS_TIMEOUT_SECONDS = 3

last_sent_lat = None
last_sent_lon = None
MOVEMENT_THRESHOLD_METERS = 5  # Only update if moved more than 5 meters

MODBUS_IP = "172.16.100.94"     
MODBUS_PORT = 502               
MODBUS_REGISTER = 0          # ← register index (e.g., 0 for 30001 or 40001)

latest_so2_co2 = 10.0 

def modbus_loop():
    global latest_so2_co2
    while True:
        try:
            client = ModbusTcpClient(MODBUS_IP, port=MODBUS_PORT)
            if client.connect():
                result = client.read_input_registers(address=MODBUS_REGISTER, count=2)
                if result and not result.isError():
                    # Combine two 16-bit registers into bytes (big-endian or little-endian depending on your device)
                    register_bytes = struct.pack('>HH', result.registers[0], result.registers[1])  # '>HH' = big-endian
                    so2_co2 = struct.unpack('>f', register_bytes)[0]  # '>f' = big-endian float
                    latest_so2_co2 = round(so2_co2, 2)
                    print(f"[MODBUS] Updated SO₂/CO₂ ratio: {latest_so2_co2}")
                else:
                    log_error("Modbus read returned error or empty.")
                client.close()
            else:
                log_error("Modbus connection failed.")
        except Exception as e:
            log_error(f"Modbus read error: {e}")
        time.sleep(10)

SECRET_KEY = b'0000'
SIGNATURE_FIELDS = [
    'timestamp', 'lat', 'lon', 'heading',
    'in_eca', 'sulphur', 'so2_co2', 'compliance', 'vessel_name'
]

latest_data = {
    "lat": None,
    "lon": None,
    "heading": None,
    "time": None
}

def play_entry_alarm():
    threading.Thread(target=playsound, args=("enter_alarm.mp3",), daemon=True).start()

def play_exit_alarm():
    threading.Thread(target=playsound, args=("exit_alarm.mp3",), daemon=True).start()

CSV_FILE = "compliance_records.csv"

def initialize_csv():
    if not os.path.exists(CSV_FILE):
        with open(CSV_FILE, "w", newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=SIGNATURE_FIELDS + ["signature"])
            writer.writeheader()

initialize_csv()

def generate_signature(record, secret_key):
    data_str = "|".join(str(record[k]) for k in SIGNATURE_FIELDS)
    return hmac.new(secret_key, data_str.encode('utf-8'), hashlib.sha256).hexdigest()

def append_record_to_csv(record):
    with open(CSV_FILE, "a", newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=SIGNATURE_FIELDS + ["signature"])
        writer.writerow({key: record[key] for key in SIGNATURE_FIELDS + ["signature"]})

def haversine(lat1, lon1, lat2, lon2):
    R = 6371e3
    phi1, phi2 = np.radians(lat1), np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlambda = np.radians(lon2 - lon1)
    a = np.sin(dphi/2)**2 + np.cos(phi1) * np.cos(phi2) * np.sin(dlambda/2)**2
    c = 2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a))
    return R * c

def calculate_heading(lat1, lon1, lat2, lon2):
    # Convert to radians
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_lon = math.radians(lon2 - lon1)

    x = math.sin(delta_lon) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - \
        math.sin(phi1) * math.cos(phi2) * math.cos(delta_lon)

    bearing_rad = math.atan2(x, y)
    bearing_deg = (math.degrees(bearing_rad) + 360) % 360
    return bearing_deg


previous_point = {"lat": None, "lon": None, "time": None}

last_csv_log_time = None

vessel_path_history = []

so2_co2_ratio_points = np.array([4.3, 21.7, 30])          
sulphur_content_points = np.array([0.10, 0.50, 0.70])    

# Use np.interp for quick interpolation (sulphur from ratio)
new_ratio = 10.0
sulphur_value = np.interp(new_ratio, so2_co2_ratio_points, sulphur_content_points)
print(f"Interpolated Sulphur Content for SO2/CO2 ratio {new_ratio}: {sulphur_value:.4f}")

# interp1d function to get ratio from sulphur (inverse mapping)
interp_func = interp1d(
    sulphur_content_points,
    so2_co2_ratio_points,
    kind='linear',
    fill_value='extrapolate'
)

def get_so2_co2_ratio(sulphur_percent):
    return float(interp_func(sulphur_percent))
def check_compliance(sulphur_percent, in_eca):
    if in_eca:
        return "Compliant" if sulphur_percent <= 0.10 else "Non Compliant"
    else:
        return "Compliant" if sulphur_percent <= 0.50 else "Non Compliant"

def parse_nmea(nmea_sentence):
    global latest_data
    try:
        parts = nmea_sentence.strip().split(',')
        if parts[0] == "$GPRMC" and len(parts) > 6:
            lat = nmea_coord_to_decimal(parts[3], parts[4])
            lon = nmea_coord_to_decimal(parts[5], parts[6])
            if lat is not None and lon is not None:
                latest_data["lat"] = lat
                latest_data["lon"] = lon
                latest_data["time"] = datetime.now(timezone.utc)
        elif parts[0] == "$GPGGA" and len(parts) > 5:
            lat = nmea_coord_to_decimal(parts[2], parts[3])
            lon = nmea_coord_to_decimal(parts[4], parts[5])
            if lat is not None and lon is not None:
                latest_data["lat"] = lat
                latest_data["lon"] = lon
                latest_data["time"] = datetime.now(timezone.utc)
    except Exception as e:
        print(f"NMEA parse error: {e}")

def nmea_coord_to_decimal(coord, direction):
    if not coord or not direction or '.' not in coord:
        return None
    deg_len = 2 if direction in ['N', 'S'] else 3
    degrees = float(coord[:deg_len])
    minutes = float(coord[deg_len:])
    decimal = degrees + minutes / 60.0
    if direction in ['S', 'W']:
        decimal *= -1
    return decimal

def find_gps_serial_port(baudrate=4800, timeout=1):
    ports = serial.tools.list_ports.comports()
    for port in ports:
        print(f"Checking port: {port.device}")
        try:
            ser = serial.Serial(port.device, baudrate=baudrate, timeout=timeout)
            time.sleep(2)  # Wait for data to arrive
            lines_read = 0
            while lines_read < 10:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                print(f"{port.device} -> {line}")
                if "$GPRMC" in line or "$GPGGA" in line:
                    print(f"GPS detected on {port.device}")
                    return ser
                lines_read += 1
            ser.close()
        except Exception as e:
            print(f"Error probing {port.device}: {e}")
    print("No GPS device found.")
    return None

ser = find_gps_serial_port()

def resource_path(relative_path):
    """ Get absolute path to resource, always relative to the EXE folder or current dir """
    if getattr(sys, 'frozen', False):  # Running as a PyInstaller bundle
        base_path = os.path.dirname(sys.executable)
    else:
        base_path = os.path.abspath(".")  # Dev mode

    return os.path.join(base_path, relative_path)

# Now load the geojson using this path
try:
    geojson_path = resource_path("static/eca_boundaries.geojson")
    eca_gdf = gpd.read_file(geojson_path).to_crs(epsg=4326)
    eca_polygon = eca_gdf.geometry.union_all()
except Exception as e:
    print(f"Error loading ECA geojson: {e}")
    eca_gdf = None
    eca_polygon = None

simulated_state = {"data": None}
def generate_map(lat, lon, compliance, in_eca, heading):
    m = folium.Map(location=[lat, lon], zoom_start=8,width='100%', height='700px')

    if len(vessel_path_history) > 1:
        folium.PolyLine(vessel_path_history, color="blue", weight=3, opacity=0.7).add_to(m)
    offset_distance = 0.01  # ~1km offset depending on latitude
    heading_rad = math.radians(heading)
    arrow_lat = lat + offset_distance * math.cos(heading_rad)
    arrow_lon = lon + offset_distance * math.sin(heading_rad)
    marker_color = "green" if compliance == "Compliant" else "red"
    folium.CircleMarker(
        location=[lat, lon],
        radius=6,
        color=marker_color,
        fill=True,
        fill_color="black",
        fill_opacity=1,
        tooltip="Current Position"
    ).add_to(m)

    icon_html = f"""
    <div style="transform: rotate({heading}deg); width: 32px; height: 32px;">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="black" xmlns="http://www.w3.org/2000/svg">
        <path d="M12 2L15 8H9L12 2ZM12 22V10" stroke="black" stroke-width="2" stroke-linecap="round"/>
      </svg>
    </div>
    """
    folium.Marker(
        [arrow_lat, arrow_lon],
        tooltip=f"{'INSIDE' if in_eca else 'OUTSIDE'} ECA - {compliance}",
        icon=DivIcon(html=icon_html)
    ).add_to(m)

    if eca_gdf is not None:
        folium.GeoJson(eca_gdf, name="ECA").add_to(m)

    buf = io.BytesIO()
    m.save(buf, close_file=False)
    html_bytes = buf.getvalue()
    encoded = base64.b64encode(html_bytes).decode('utf-8')
    return encoded

last_in_eca_flag = None

def read_serial_data():
    global last_in_eca_flag, last_csv_log_time, ser
    while True:
        try:
            if ser is None or not ser.is_open:
                log_error("Serial port not available or closed. Attempting to reconnect...")
                ser = find_gps_serial_port()
                if ser is None:
                    log_error("No GPS device found. Retrying in 10 seconds...")
                    time.sleep(10)
                    continue

            try:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                if line:
                    parse_nmea(line)

                    if all(latest_data[k] is not None for k in ["lat", "lon", "time"]):
                        lat = latest_data["lat"]
                        lon = latest_data["lon"]
                        if previous_point["lat"] is not None:
                            heading = calculate_heading(previous_point["lat"], previous_point["lon"], lat, lon)
                        else:
                            heading = 0.0  # Default if no previous point
                        now = latest_data["time"]

                        # Reset values after use
                        latest_data["lat"] = None
                        latest_data["lon"] = None
                        latest_data["time"] = None

                        if previous_point["lat"] is not None:
                            distance = haversine(previous_point["lat"], previous_point["lon"], lat, lon)
                            time_diff = (now - previous_point["time"]).total_seconds()
                            speed = distance / time_diff if time_diff > 0 else 0
                        else:
                            speed = 0.0

                        previous_point.update({"lat": lat, "lon": lon, "time": now})

                        try:
                            so2_co2 = latest_so2_co2 or 10.0  # fallback
                            sulphur = np.interp(so2_co2, so2_co2_ratio_points, sulphur_content_points)
                            sulphur = round(float(sulphur), 4)
                        except Exception as e:
                            log_error(f"Modbus read error: {e}")
                            so2_co2 = 10.0
                            sulphur = round(np.interp(so2_co2, so2_co2_ratio_points, sulphur_content_points), 4)

                        vessel_name = "MSC NORA"
                        in_eca_flag = Point(lon, lat).within(eca_polygon) if eca_polygon else False
                        compliance = check_compliance(sulphur, in_eca_flag)

                        if last_in_eca_flag is not None:
                            if not last_in_eca_flag and in_eca_flag:
                                play_entry_alarm()
                            elif last_in_eca_flag and not in_eca_flag:
                                play_exit_alarm()

                        last_in_eca_flag = in_eca_flag

                        record = {
                            "timestamp": now.isoformat(),
                            "lat": lat,
                            "lon": lon,
                            "heading": round(heading, 2),
                            "in_eca": str(in_eca_flag).upper(),
                            "sulphur": round(sulphur, 4),
                            "so2_co2": round(so2_co2, 2),
                            "compliance": compliance,
                            "vessel_name": vessel_name,
                            "speed": round(speed * 1.94384, 2)
                        }

                        record["signature"] = generate_signature(record, SECRET_KEY)

                        now_utc = datetime.now(timezone.utc)
                        if last_csv_log_time is None or (now_utc - last_csv_log_time).total_seconds() >= 600:
                            append_record_to_csv(record)
                            last_csv_log_time = now_utc

                        simulated_state["data"] = record

            except Exception as e:
                log_error(f"Serial read error: {e}")
                time.sleep(1)

        except Exception as outer_e:
            log_error(f"Serial thread crashed: {outer_e}")
            time.sleep(5)

def start_background_threads():
    threading.Thread(target=read_serial_data, daemon=True).start()

@app.route('/')
def index():
    return render_template_string(INDEX_HTML)

@app.route('/azure_status')
def azure_status():
    return jsonify(azure_connection_status)

@app.route("/errors")
def errors():
    return jsonify(list(error_log))

@app.route('/data')
def data():
    global last_sent_lat, last_sent_lon
    data = simulated_state.get("data")
    now = datetime.now(timezone.utc)
    if not data:
        return jsonify({"status": "waiting"})

    # Load vessel info from JSON file fresh each time
    try:
        with open("vessel_info.json", "r") as f:
            vessel_info = json.load(f)
    except Exception as e:
        log_error(f"Failed to read vessel_info.json in /data: {e}")
        vessel_info = {
            "Vessel Name": "Unknown",
            "Vessel IMO": "Unknown",
            "Call Sign": "Unknown"
        }

    lat = data["lat"]
    lon = data["lon"]
    compliance = data["compliance"]
    in_eca = data["in_eca"] == "TRUE"
    heading = data["heading"]
# Check for GPS timeout
    last_gps_time = None
    if data and "timestamp" in data and data["timestamp"]:
        try:
            last_gps_time = datetime.fromisoformat(data["timestamp"])
        except Exception:
            last_gps_time = None
    if last_gps_time is None or (now - last_gps_time).total_seconds() > GPS_TIMEOUT_SECONDS:
        return jsonify({"status": "gps_lost"})

    if not data:
        return jsonify({"status": "waiting"})

    # Check if position changed significantly
    moved = False
    if last_sent_lat is None or last_sent_lon is None:
        moved = True
    else:
        distance = haversine(last_sent_lat, last_sent_lon, lat, lon)
        if distance >= MOVEMENT_THRESHOLD_METERS:
            moved = True

    if not moved:
        return jsonify({"status": "nochange"})

    # Update last sent coordinates
    last_sent_lat = lat
    last_sent_lon = lon

    vessel_path_history.append([lat, lon])
    if len(vessel_path_history) > 500:
        vessel_path_history.pop(0)

    # map_html_b64 = generate_map(lat, lon, compliance, in_eca, heading)

    return jsonify({
        "status": "ok",
        "data": data,
        "vessel_info": vessel_info,
        "path_history": vessel_path_history
    })


INDEX_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Vessel ECA Compliance Dashboard</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link rel="stylesheet" href="https://unpkg.com/leaflet/dist/leaflet.css" />
    <script src="https://unpkg.com/leaflet/dist/leaflet.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Roboto&display=swap" rel="stylesheet">
    <style>
        html, body {
            height: 100%;
            margin: 0;
            padding: 0;
            background-color: #121212;
            color: #f0f0f0;
            font-family: 'Roboto', sans-serif;
            font-size: 17px;
            line-height: 1.7;
        }
        #container {
            display: flex;
            gap: 20px;
            height: 90vh;
            align-items: stretch;
        }
        #map {
            width: 70%;
            height: 100%;
            border: 2px solid #333;
            border-radius: 10px;
            overflow: hidden;
        }
        #details {
            width: 30%;
            border-radius: 10px;
            padding: 20px;
            background-color: #1e1e1e;
            border: 2px solid #666;
            height: 100%;
            box-shadow: 0 0 10px #00000099;
        }
        h1 {
            color: #ffffff;
            text-align: center;
            font-size: 28px;
            margin-top: 20px;
        }
        h3 {
            color: #ffffff;
            text-align: center;
            font-size: 22px;
            margin-bottom: 10px;
        }
        strong {
            color: #c0c0c0;
            display: inline-block;
            width: 150px;
        }
        #azure-status {
            margin-top: 20px;
            font-weight: bold;
            font-size: 17px;
            text-align: center;
        }
    </style>
</head>
<body>
    <h1>Vessel ECA Compliance Dashboard</h1>
    <div id="container">
        <div id="map"></div>
        <div id="details">
            <h3>Vessel Info</h3>
            <div id="vessel-info"></div>
            <h3>Live Data</h3>
            <div id="live-data">Waiting for NMEA coordinates...</div>
            <div id="azure-status">Checking Azure status...</div>
            <h3>System Logs</h3>
            <div id="error-logs" style="font-size: 14px; color: orange; max-height: 180px; overflow-y: auto;"></div>
        </div>
    </div>
    <script>
        let initialLat = 25.0;
        let initialLon = 55.0;

        let map = L.map('map').setView([initialLat, initialLon], 8);
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            attribution: '© OpenStreetMap contributors'
        }).addTo(map);

        fetch('/static/eca_boundaries.geojson')
            .then(res => res.json())
            .then(data => {
                L.geoJSON(data, {
                    style: {
                        color: 'orange',
                        weight: 2,
                        opacity: 0.6
                    }
                }).addTo(map);
            });

        let vesselMarker = L.circleMarker([initialLat, initialLon], {
            radius: 8,
            color: 'green',
            fillColor: '#000',
            fillOpacity: 1
        }).addTo(map);

        let headingArrow = null;
        let vesselPath = L.polyline([], {color: 'blue'}).addTo(map);

        function updateMap(lat, lon, path, compliance, heading) {
            vesselMarker.setLatLng([lat, lon]);
            let arrowDistance = 0.01;
            let headingRad = heading * Math.PI / 180;
            let arrowLat = lat + arrowDistance * Math.cos(headingRad);
            let arrowLon = lon + arrowDistance * Math.sin(headingRad);

            if (headingArrow) {
                map.removeLayer(headingArrow);
            }

            headingArrow = L.marker([arrowLat, arrowLon], {
                icon: L.divIcon({
                    className: '',
                    html: `<div style="transform: rotate(${heading}deg); width: 24px; height: 24px;">
                        <svg width="24" height="24" viewBox="0 0 24 24" fill="black" xmlns="http://www.w3.org/2000/svg">
                            <path d="M12 2L15 8H9L12 2ZM12 22V10" stroke="black" stroke-width="2" stroke-linecap="round"/>
                        </svg>
                    </div>`
                })
            }).addTo(map);

            vesselPath.setLatLngs(path);
            vesselMarker.setStyle({ color: compliance === "Compliant" ? "green" : "red" });
            map.setView([lat, lon]);
        }

        function updateDetails(data, vesselInfo) {
            document.getElementById('live-data').innerHTML = `
                <strong>Latitude:</strong> ${data.lat.toFixed(6)}<br>
                <strong>Longitude:</strong> ${data.lon.toFixed(6)}<br>
                <strong>Heading:</strong> ${data.heading}&deg;<br>
                <strong>SO₂/CO₂ Ratio:</strong> ${data.so2_co2}<br>
                <strong>Sulphur %:</strong> ${data.sulphur}<br>
                <strong>Compliance:</strong> <span style="color:${data.compliance === "Compliant" ? "lime" : "red"}">${data.compliance}</span><br>
                <strong>In ECA:</strong> ${data.in_eca}<br>
                <strong>Speed (knots):</strong> ${data.speed}
            `;
            document.getElementById('vessel-info').innerHTML = `
                <strong>Name:</strong> ${vesselInfo["Vessel Name"]}<br>
                <strong>IMO:</strong> ${vesselInfo["Vessel IMO"]}<br>
                <strong>Call Sign:</strong> ${vesselInfo["Call Sign"]}
            `;
        }

        function pollData() {
            fetch('/data')
                .then(response => response.json())
                .then(json => {
                    if (json.status === "ok") {
                        const lat = json.data.lat;
                        const lon = json.data.lon;
                        const compliance = json.data.compliance;
                        const heading = json.data.heading;
                        const path = json.path_history || [[lat, lon]];
                        updateMap(lat, lon, path, compliance, heading);
                        updateDetails(json.data, json.vessel_info);
                    } else if (json.status === "gps_lost") {
                        document.getElementById('live-data').innerHTML = "GPS signal lost.";
                    } else if (json.status === "waiting") {
                        document.getElementById('live-data').innerHTML = "Waiting for NMEA coordinates...";
                    }
                });
        }

        function pollAzureStatus() {
            fetch('/azure_status')
                .then(res => res.json())
                .then(status => {
                    const elem = document.getElementById("azure-status");
                    if (status.connected) {
                        elem.innerHTML = "Connected to Azure";
                        elem.style.color = "lime";
                    } else {
                        elem.innerHTML = "Failed to connect to Azure";
                        elem.style.color = "red";
                    }
                });
        }

        function pollErrors() {
            fetch('/errors')
                .then(res => res.json())
                .then(errors => {
                    const logElem = document.getElementById("error-logs");
                    if (errors.length > 0) {
                        logElem.innerHTML = errors.map(e => `<div>${e}</div>`).join("");
                    } else {
                        logElem.innerHTML = "<div>No errors</div>";
                    }
                });
        }

        setInterval(pollData, 2000);
        setInterval(pollAzureStatus, 10000);
        setInterval(pollErrors, 5000);
        pollData();
        pollAzureStatus();
        pollErrors();
    </script>
</body>
</html>
"""

driver = '{ODBC Driver 18 for SQL Server}'
server = 'vessellogs.database.windows.net'  
database = 'XYZ'
username = 'ABC'
password = 'XYZ'

def azure_upload_loop():
    global azure_connection_status
    while True:
        try:
            df = pd.read_csv("compliance_records.csv")
            if df.empty:
                print("[Azure Upload] No data to upload.")
                azure_connection_status = {"connected": True, "message": "No data to upload"}
                time.sleep(30)
                continue

            conn_str = f'DRIVER={driver};SERVER={server};DATABASE={database};UID={username};PWD={password}'
            conn = pyodbc.connect(conn_str)
            cursor = conn.cursor()

            print("[Azure Upload] Connected to Azure SQL.")
            azure_connection_status = {"connected": True, "message": "Connected to Azure"}

            cursor.execute("""
                IF NOT EXISTS (
                    SELECT * FROM INFORMATION_SCHEMA.TABLES
                    WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = 'VesselLogs'
                )
                BEGIN
                    CREATE TABLE dbo.VesselLogs (
                        timestamp VARCHAR(50) PRIMARY KEY,
                        lat FLOAT,
                        lon FLOAT,
                        heading FLOAT,
                        in_eca VARCHAR(10),
                        sulphur FLOAT,
                        so2_co2 FLOAT,
                        compliance VARCHAR(20),
                        vessel_name VARCHAR(100),
                        signature VARCHAR(MAX)
                    )
                END
            """)

            print("[Azure Upload] Attempting to upload data...")

            for index, row in df.iterrows():
                try:
                    cursor.execute("SELECT COUNT(*) FROM dbo.VesselLogs WHERE timestamp = ?", row['timestamp'])
                    exists = cursor.fetchone()[0]
                    if exists:
                        continue

                    cursor.execute("""
                        INSERT INTO dbo.VesselLogs (
                            timestamp, lat, lon, heading, in_eca,
                            sulphur, so2_co2, compliance, vessel_name, signature
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    row['timestamp'], row['lat'], row['lon'], row['heading'], row['in_eca'],
                    row['sulphur'], row['so2_co2'], row['compliance'],
                    row['vessel_name'], row['signature'])
                except Exception as row_err:
                    log_error(f"Row Insert Error at index {index}: {row_err}")

            conn.commit()
            print("[Azure Upload] Uploaded successfully!")
            cursor.close()
            conn.close()

        except Exception as e:
            azure_connection_status = {"connected": False, "message": f"Failed to connect: {e}"}
            log_error(f"Azure Upload Error: {e}")

        time.sleep(600)

def start_background_threads():
    threading.Thread(target=read_serial_data, daemon=True).start()
    threading.Thread(target=azure_upload_loop, daemon=True).start()
    threading.Thread(target=modbus_loop, daemon=True).start()

if __name__ == "__main__":
    start_background_threads()
    webbrowser.open("http://127.0.0.1:8503") 
    serve(app, host="127.0.0.1", port=8503)


