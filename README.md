# Vessel ECA Compliance Dashboard

## Overview

The **Vessel ECA Compliance Dashboard** is a real-time maritime monitoring application designed to track vessel location, monitor emissions, determine whether a vessel is operating inside an Emission Control Area (ECA), and evaluate fuel sulphur compliance.

The system receives live GPS data in NMEA format, reads SO₂/CO₂ values through Modbus TCP, estimates sulphur content, and displays vessel information and compliance status on an interactive web dashboard. Compliance records are digitally signed using HMAC-SHA256, stored locally in CSV format, and periodically uploaded to an Azure SQL database.

## Features

* Real-time vessel GPS tracking using NMEA data
* Automatic detection of GPS serial ports
* Support for `$GPRMC` and `$GPGGA` NMEA sentences
* Vessel heading and speed calculation
* Interactive OpenStreetMap-based dashboard
* ECA boundary detection using GeoJSON
* Real-time SO₂/CO₂ ratio acquisition using Modbus TCP
* Sulphur content estimation using interpolation
* Automatic compliance evaluation inside and outside ECAs
* Entry and exit audio alarms for ECA boundaries
* Vessel path history visualization
* GPS signal loss detection
* Automatic error logging and recovery
* HMAC-SHA256 digital signatures for compliance records
* Local CSV data logging
* Periodic Azure SQL database synchronization
* Live Azure connection status monitoring

## System Architecture

```text
GPS Device
    │
    ▼
NMEA Serial Data
    │
    ▼
Python Backend
    │
    ├── GPS Parsing
    ├── Heading Calculation
    ├── Speed Calculation
    ├── ECA Detection
    └── Compliance Evaluation
    │
    ├─────────────── Modbus TCP ───────────────┐
    │                                           ▼
    │                                    SO₂/CO₂ Sensor
    │
    ▼
Flask Web API
    │
    ▼
Interactive Vessel Dashboard
    │
    ├── Live Map
    ├── Vessel Information
    ├── Compliance Status
    ├── Azure Status
    └── System Error Logs
    │
    ▼
CSV Compliance Records
    │
    ▼
Azure SQL Database
```

## Compliance Logic

The application determines vessel compliance based on the estimated sulphur percentage and whether the vessel is located inside an ECA.

| Location    | Maximum Sulphur Content |
| ----------- | ----------------------: |
| Inside ECA  |                   0.10% |
| Outside ECA |                   0.50% |

The system marks the vessel as:

* **Compliant** when the sulphur percentage is within the permitted limit.
* **Non Compliant** when the sulphur percentage exceeds the permitted limit.

The application uses interpolation between predefined SO₂/CO₂ ratio and sulphur-content values to estimate the sulphur percentage.

## Project Structure

```text
Navigation-WebApp/
│
├── main.py
├── compliance_records.csv
├── vessel_info.json
├── enter_alarm.mp3
├── exit_alarm.mp3
│
├── static/
│   └── eca_boundaries.geojson
│
├── build/                 # Ignored by Git
├── dist/                  # Ignored by Git
├── __pycache__/           # Ignored by Git
│
├── requirements.txt
└── README.md
```

## Required Python Libraries

Install the required dependencies:

```bash
pip install flask numpy pandas scipy pyserial geopandas folium shapely playsound waitress pyodbc pymodbus pywebview
```

## Additional System Requirements

Depending on the operating system, the following may also be required:

* Python 3.9 or later
* ODBC Driver 18 for SQL Server
* Access to a GPS device providing NMEA data
* Modbus TCP connection to the emissions monitoring device
* Azure SQL Database credentials
* Required audio files:

  * `enter_alarm.mp3`
  * `exit_alarm.mp3`

## Configuration

### Modbus Configuration

Update the Modbus device details in the Python application:

```python
MODBUS_IP = "YOUR_MODBUS_IP"
MODBUS_PORT = 502
MODBUS_REGISTER = 0
```

### Azure SQL Configuration

Configure the Azure SQL Database credentials:

```python
driver = '{ODBC Driver 18 for SQL Server}'
server = 'YOUR_SERVER'
database = 'YOUR_DATABASE'
username = 'YOUR_USERNAME'
password = 'YOUR_PASSWORD'
```

> **Important:** Do not upload real database credentials or secret keys to GitHub. Use environment variables or a separate configuration file excluded through `.gitignore`.

## Vessel Information

Create a `vessel_info.json` file:

```json
{
    "Vessel Name": "MSC NORA",
    "Vessel IMO": "1234567",
    "Call Sign": "ABC1234"
}
```

The dashboard reads this information and displays it alongside the live vessel data.

## Running the Application

Run the main Python file:

```bash
python main.py
```

The application will start the background threads for:

* GPS/NMEA data processing
* Azure SQL data upload
* Modbus TCP data acquisition

The dashboard will then be available at:

```text
http://127.0.0.1:8503
```

The application uses a Waitress server running locally on port `8503`.

## Dashboard

The web dashboard provides:

### Live Vessel Map

Displays the vessel's current position, movement path, heading direction, and ECA boundaries.

### Vessel Information

Displays:

* Vessel Name
* IMO Number
* Call Sign

### Live Emission Data

Displays:

* Latitude
* Longitude
* Heading
* SO₂/CO₂ Ratio
* Estimated Sulphur Percentage
* Compliance Status
* ECA Status
* Vessel Speed in Knots

### System Monitoring

Displays:

* Azure SQL connection status
* GPS connection status
* Recent system errors

The frontend periodically polls the Flask API for live updates.

## API Endpoints

| Endpoint        | Description                                   |
| --------------- | --------------------------------------------- |
| `/`             | Main Vessel ECA Compliance Dashboard          |
| `/data`         | Returns the latest vessel and compliance data |
| `/azure_status` | Returns Azure SQL connection status           |
| `/errors`       | Returns recent system errors                  |

## Data Logging and Security

Compliance records include timestamp, vessel position, heading, ECA status, sulphur content, SO₂/CO₂ ratio, compliance result, and vessel name.

Each record is protected using an **HMAC-SHA256 signature** before being stored. Records are logged locally in `compliance_records.csv` and periodically uploaded to the Azure SQL database.

## Git Ignore

The following folders should be included in `.gitignore`:

```gitignore
build/
dist/
__pycache__/
```

These folders contain generated build files, packaged executables, and Python cache files and should not be committed to the repository.

## Future Improvements

* Support multiple vessels simultaneously
* Add historical voyage analytics
* Integrate real-time AIS data
* Add user authentication
* Add cloud-based notifications
* Implement machine learning for emissions prediction
* Add downloadable compliance reports
* Use environment variables for credentials and API secrets
* Containerize the application using Docker

## Author

Developed as a maritime vessel navigation and **Emission Control Area (ECA) compliance monitoring system** for real-time vessel tracking, emissions monitoring, and regulatory compliance assessment.
