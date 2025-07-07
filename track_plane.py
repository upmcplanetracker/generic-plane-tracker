# Plane Tracker - Public GitHub Version
# This script uses the public APIs from adsb.lol and adsb.fi.
# It does not require you to be an ADS-B data feeder.

import requests
import os
import datetime
from time import sleep
from bsky_bridge import BskySession, post_text
from dotenv import load_dotenv
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError
from timezonefinder import TimezoneFinder
import pytz
import subprocess
import math
import re

# --- Load Environment Variables ---
load_dotenv()

# --- Configuration (from .env file) ---
PLANE_CODE = os.getenv("PLANE_CODE")
BLUESKY_HANDLE = os.getenv("BLUESKY_HANDLE")
BLUESKY_APP_PASSWORD = os.getenv("BLUESKY_APP_PASSWORD")
RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL")
GEOLOCATOR_EMAIL = os.getenv("GEOLOCATOR_EMAIL", "plane-tracker@example.com")

# --- Script Behavior & Thresholds ---
ALTITUDE_THRESHOLD = int(os.getenv("ALTITUDE_THRESHOLD", "500"))
GROUND_SPEED_THRESHOLD = int(os.getenv("GROUND_SPEED_THRESHOLD", "50"))
MIN_STATE_CHANGE_TIME = int(os.getenv("MIN_STATE_CHANGE_TIME", "300"))
IDLE_NOTIFICATION_THRESHOLD_HOURS = int(os.getenv("IDLE_NOTIFICATION_THRESHOLD_HOURS", "12"))
LOG_RETENTION_HOURS = int(os.getenv("LOG_RETENTION_HOURS", "6"))
RETRY_COUNT = int(os.getenv("RETRY_COUNT", "2"))
RETRY_DELAY = int(os.getenv("RETRY_DELAY", "2"))

# --- Aircraft & Flight Metrics ---
DEFAULT_FUEL_BURN_GAL_PER_NM = float(os.getenv("DEFAULT_FUEL_BURN_GAL_PER_NM", "0.97"))
JET_FUEL_CO2_LBS_PER_GALLON = float(os.getenv("JET_FUEL_CO2_LBS_PER_GALLON", "21.1"))
LBS_PER_METRIC_TON = float(os.getenv("LBS_PER_METRIC_TON", "2204.62"))
CO2_TONS_PER_AVG_CAR_MILE = float(os.getenv("CO2_TONS_PER_AVG_CAR_MILE", "0.0004"))

# --- Static & Derived Configuration ---
IDLE_NOTIFICATION_THRESHOLD_SECONDS = IDLE_NOTIFICATION_THRESHOLD_HOURS * 3600
EARTH_RADIUS_NM = 3440.065

# --- API Endpoints (Using Public APIs) ---
ADSB_LOL_API_URL = "https://api.adsb.lol/v2/aircraft/{icao_hex}"
ADSB_FI_API_URL = "https://opendata.adsb.fi/api/v2/hex/{icao_hex}"

# --- Geocoding configuration ---
GEOLOCATOR_USER_AGENT = f"PlaneTrackerApp/1.0 ({GEOLOCATOR_EMAIL})"

# --- File Paths ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(SCRIPT_DIR, "plane_state.txt")
LOG_FILE = os.path.join(SCRIPT_DIR, "plane_tracker.log")

# --- Global Instances ---
TZ_FINDER = TimezoneFinder()
SCRIPT_RUN_TZ = pytz.UTC

# --- Logging ---
def log_message(message, source_api=None):
    utc_now = datetime.datetime.now(pytz.UTC)
    utc_timestamp_str = utc_now.strftime("%Y-%m-%d %H:%M:%S UTC")

    local_tz = SCRIPT_RUN_TZ
    local_now = utc_now.astimezone(local_tz)
    local_timestamp_str = local_now.strftime("%Y-%m-%d %I:%M:%S %p %Z")

    source_tag = f" [{source_api.upper()}]" if source_api else ""
    log_entry = f"{local_timestamp_str} ({utc_timestamp_str}){source_tag} - {message}\n"

    all_log_entries = []
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r") as f:
            all_log_entries = f.readlines()

    all_log_entries.append(log_entry)

    retention_delta = datetime.timedelta(hours=LOG_RETENTION_HOURS)
    current_utc_time_dt = datetime.datetime.now(pytz.UTC)
    timestamp_pattern = re.compile(r'\((\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) UTC\)')

    filtered_entries = []
    for entry in all_log_entries:
        match = timestamp_pattern.search(entry)
        if match:
            log_utc_str_found = match.group(1)
            try:
                log_dt_utc = datetime.datetime.strptime(log_utc_str_found, "%Y-%m-%d %H:%M:%S").replace(tzinfo=pytz.UTC)
                if current_utc_time_dt - log_dt_utc <= retention_delta:
                    filtered_entries.append(entry)
            except ValueError:
                filtered_entries.append(entry)
        else:
            filtered_entries.append(entry)

    with open(LOG_FILE, "w") as f:
        f.writelines(filtered_entries)

    print(log_entry.strip())

# --- Timezone Helper ---
def get_timezone_from_coordinates(latitude: float, longitude: float) -> pytz.BaseTzInfo:
    if latitude is None or longitude is None:
        return pytz.UTC
    try:
        tz_name = TZ_FINDER.timezone_at(lat=latitude, lng=longitude)
        if tz_name:
            return pytz.timezone(tz_name)
        else:
            log_message(f"No timezone found for coordinates {latitude}, {longitude}. Using UTC.")
            return pytz.UTC
    except Exception as e:
        log_message(f"Error getting timezone for {latitude}, {longitude}: {e}. Using UTC.")
        return pytz.UTC

def format_full_time_for_location(utc_dt: datetime.datetime, latitude: float, longitude: float) -> tuple[str, str]:
    if not isinstance(utc_dt, datetime.datetime):
        return "Invalid Time", "Invalid Time"

    if utc_dt.tzinfo is None or utc_dt.tzinfo.utcoffset(utc_dt) is None:
        utc_dt = pytz.UTC.localize(utc_dt)
    elif utc_dt.tzinfo != pytz.UTC:
        utc_dt = utc_dt.astimezone(pytz.UTC)

    utc_time_str = utc_dt.strftime("%Y-%m-%d, %H:%M UTC")

    if latitude is None or longitude is None:
        return utc_time_str, utc_dt.strftime("%Y-%m-%d, %H:%M UTC (No location info)")

    local_tz = get_timezone_from_coordinates(latitude, longitude)
    local_time = utc_dt.astimezone(local_tz)
    local_time_str = local_time.strftime("%Y-%m-%d, %I:%M %p %Z")
    return utc_time_str, local_time_str

# --- Data Fetching Functions ---
def _fetch_data_from_api(url: str, source_name: str, icao_hex: str, suppress_email_on_fail: bool = False) -> tuple[dict | None, bool, str | None]:
    error_msg = None
    for attempt in range(RETRY_COUNT + 1):
        try:
            full_url = url.format(icao_hex=icao_hex)
            response = requests.get(full_url, timeout=5)
            response.raise_for_status()
            log_message(f"Successfully fetched data from {source_name}.", source_api=source_name)

            data = response.json()
            # Public APIs use the 'ac' key for the aircraft list
            if data and 'ac' in data and data['ac']:
                return data['ac'][0], True, None
            else:
                log_message(f"No aircraft data found for ICAO {icao_hex} from {source_name}.", source_api=source_name)
                return None, True, None
        except requests.exceptions.Timeout:
            error_msg = f"Timeout fetching data from {source_name}: Read timed out."
            if attempt == RETRY_COUNT and not suppress_email_on_fail:
                email_subject = f"CRITICAL: Plane Tracker - {source_name.upper()} API Timeout!"
                email_body = f"The plane tracker script failed to fetch data from {source_name} due to a timeout after {RETRY_COUNT + 1} attempts.\n\nError: {error_msg}"
                send_email(email_subject, email_body, RECIPIENT_EMAIL)
        except requests.exceptions.HTTPError as e:
            error_msg = f"HTTP Error fetching data from {source_name}: {e}"
            if not suppress_email_on_fail and attempt == RETRY_COUNT:
                email_subject = f"CRITICAL: Plane Tracker - {source_name.upper()} HTTP Error!"
                email_body = f"The plane tracker script encountered an HTTP error from {source_name} API after {RETRY_COUNT + 1} attempts.\n\nError: {error_msg}"
                send_email(email_subject, email_body, RECIPIENT_EMAIL)
        except requests.RequestException as e:
            error_msg = f"General Request Error fetching data from {source_name}: {e}"
            if attempt == RETRY_COUNT and not suppress_email_on_fail:
                email_subject = f"CRITICAL: Plane Tracker - {source_name.upper()} General Request Error!"
                email_body = f"The plane tracker script encountered a general request error from {source_name} API after {RETRY_COUNT + 1} attempts.\n\nError: {error_msg}"
                send_email(email_subject, email_body, RECIPIENT_EMAIL)
        except Exception as e:
            error_msg = f"Unexpected error fetching data from {source_name}: {e}"
            if attempt == RETRY_COUNT and not suppress_email_on_fail:
                email_subject = f"CRITICAL: Plane Tracker - {source_name.upper()} Unexpected Error!"
                email_body = f"The plane tracker script encountered an unexpected error while fetching data from {source_name} after {RETRY_COUNT + 1} attempts.\n\nError: {error_msg}"
                send_email(email_subject, email_body, RECIPIENT_EMAIL)
        if attempt < RETRY_COUNT:
            sleep(RETRY_DELAY)
    log_message(f"Failed to fetch data from {source_name} after {RETRY_COUNT + 1} attempts.", source_api=source_name)
    return None, False, error_msg

def get_plane_data(icao_hex: str, spoof_data: dict = None) -> dict:
    if spoof_data is not None:
        log_message(f"Using SPOOFED DATA: {spoof_data}")
        return spoof_data
    log_message(f"Attempting to fetch data from ADSB.lol (primary).", source_api="adsb.lol")
    plane_data_lol, lol_api_successful, lol_error_msg = _fetch_data_from_api(ADSB_LOL_API_URL, "adsb.lol", icao_hex, suppress_email_on_fail=True)
    if plane_data_lol:
        return plane_data_lol
    if not lol_api_successful:
        log_message(f"ADSB.lol API call failed. Attempting to fetch data from ADSB.fi (failover).", source_api="adsb.fi")
        plane_data_fi, fi_api_successful, fi_error_msg = _fetch_data_from_api(ADSB_FI_API_URL, "adsb.fi", icao_hex)
        if plane_data_fi:
            log_message(f"Data successfully retrieved from adsb.fi after ADSB.lol failure.", source_api="adsb.fi")
            return plane_data_fi
        else:
            if not fi_api_successful:
                log_message(f"ADSB.fi also failed. No plane data could be retrieved from any source.", source_api="none")
                email_subject = f"CRITICAL: Plane Tracker - Both APIs Failed!"
                email_body = (
                    f"Your plane tracker script failed to retrieve data from both ADSB.lol and ADSB.fi.\n\n"
                    f"ADSB.lol error: {lol_error_msg if lol_error_msg else 'No specific error message recorded.'}\n"
                    f"ADSB.fi error: {fi_error_msg if fi_error_msg else 'No specific error message recorded.'}\n\n"
                    f"Please check the log file for more details."
                )
                send_email(email_subject, email_body, RECIPIENT_EMAIL)
            else:
                log_message(f"ADSB.fi responded successfully but found no aircraft data.", source_api="adsb.fi (no data)")
    else:
        log_message(f"ADSB.lol responded successfully but found no aircraft data. Not failing over.", source_api="adsb.lol (no data)")
    return None

def get_location_name(latitude: float, longitude: float) -> str:
    if latitude is None or longitude is None:
        return "an unknown location"
    geolocator = Nominatim(user_agent=GEOLOCATOR_USER_AGENT)
    try:
        location = geolocator.reverse(f"{latitude}, {longitude}", timeout=10)
        if location:
            address = location.raw.get('address', {})
            city = address.get('city') or address.get('town') or address.get('village')
            state = address.get('state')
            country = address.get('country')
            parts = [part for part in [city, state, country] if part]
            if parts:
                return ", ".join(parts)
            else:
                return location.address
        return "an unknown location"
    except (GeocoderTimedOut, GeocoderServiceError) as e:
        log_message(f"Geocoding error: {e}")
        return "an unknown location (geocoding error)"
    except Exception as e:
        log_message(f"Unexpected geocoding error: {e}")
        return "an unknown location (unexpected geocoding error)"

def get_current_state() -> tuple:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            lines = f.readlines()
            if len(lines) >= 6:
                try:
                    state = lines[0].strip()
                    last_lat = float(lines[1].strip()) if lines[1].strip() else None
                    last_lon = float(lines[2].strip()) if lines[2].strip() else None
                    last_change_time = float(lines[3].strip()) if lines[3].strip() else 0
                    last_takeoff_location_name = lines[4].strip() if lines[4].strip() else None
                    last_idle_notification_time = float(lines[5].strip()) if lines[5].strip() else 0
                    return state, last_lat, last_lon, last_change_time, last_takeoff_location_name, last_idle_notification_time
                except (ValueError, IndexError):
                    pass
    return "landed", None, None, 0, None, 0

def set_current_state(state: str, latitude: float = None, longitude: float = None, timestamp: float = None, takeoff_location_name: str = None, last_idle_notification_time: float = 0):
    if timestamp is None:
        timestamp = datetime.datetime.now(pytz.UTC).timestamp()
    with open(STATE_FILE, "w") as f:
        f.write(f"{state}\n")
        f.write(f"{latitude if latitude is not None else ''}\n")
        f.write(f"{longitude if longitude is not None else ''}\n")
        f.write(f"{timestamp}\n")
        f.write(f"{takeoff_location_name if takeoff_location_name is not None else ''}\n")
        f.write(f"{last_idle_notification_time}\n")
    log_message(f"State updated to: {state}")

def get_aircraft_display_name(plane_data: dict) -> str:
    icao = PLANE_CODE.upper() if PLANE_CODE else "UNKNOWN_ICAO"
    # Public APIs use 'flight' for callsign/registration
    registration = plane_data.get("flight")
    if registration:
        return f"{registration.strip()} ({icao})"
    else:
        return f"The aircraft ({icao})"

def get_Maps_link(latitude: float, longitude: float) -> str:
    if latitude is None or longitude is None:
        return "N/A"
    return f"https://www.google.com/maps/search/?api=1&query={latitude},{longitude}"

def send_email(subject: str, body: str, recipient_email: str):
    if not recipient_email:
        log_message("RECIPIENT_EMAIL not set. Skipping email notification.")
        return
    try:
        command = ['mail', '-s', subject, recipient_email]
        subprocess.run(command, input=body.encode('utf-8'), capture_output=True, check=True)
        log_message(f"Email sent to {recipient_email} with subject '{subject}'.")
    except Exception as e:
        log_message(f"An error occurred while sending email: {e}")

def post_to_bluesky(message: str, test_mode: bool = False):
    if not BLUESKY_HANDLE or not BLUESKY_APP_PASSWORD:
        log_message("Bluesky credentials not set. Skipping post.")
        return
    if test_mode:
        message = f"[TEST]\n{message}"
    log_message(f"Bluesky Post: {message}")
    try:
        session = BskySession(BLUESKY_HANDLE, BLUESKY_APP_PASSWORD)
        post_text(session, message)
        log_message(f"Posted to Bluesky successfully.")
    except Exception as e:
        log_message(f"Error posting to Bluesky: {e}")

def calculate_flight_metrics(lat1: float, lon1: float, lat2: float, lon2: float) -> dict:
    if any(coord is None for coord in [lat1, lon1, lat2, lon2]):
        return {'distance_nm': 0.0, 'fuel_gallons': 0.0, 'co2_tons': 0.0, 'equivalent_car_miles': 0.0}
    lat1_rad, lon1_rad, lat2_rad, lon2_rad = map(math.radians, [lat1, lon1, lat2, lon2])
    dlon = lon2_rad - lon1_rad
    dlat = lat2_rad - lat1_rad
    a = math.sin(dlat / 2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    distance_nm = EARTH_RADIUS_NM * c
    fuel_gallons = distance_nm * DEFAULT_FUEL_BURN_GAL_PER_NM
    co2_lbs = fuel_gallons * JET_FUEL_CO2_LBS_PER_GALLON
    co2_tons = co2_lbs / LBS_PER_METRIC_TON
    equivalent_car_miles = 0.0
    if CO2_TONS_PER_AVG_CAR_MILE > 0:
        equivalent_car_miles = co2_tons / CO2_TONS_PER_AVG_CAR_MILE
    return {
        'distance_nm': round(distance_nm, 2),
        'fuel_gallons': round(fuel_gallons, 2),
        'co2_tons': round(co2_tons, 2),
        'equivalent_car_miles': round(equivalent_car_miles)
    }

def main(spoof_data: dict = None, test_mode: bool = False):
    global SCRIPT_RUN_TZ
    if not PLANE_CODE:
        print(f"{datetime.datetime.now(pytz.UTC).strftime('%Y-%m-%d %H:%M:%S UTC')} - CRITICAL: PLANE_CODE is not set in the .env file. Exiting.")
        return

    current_state, last_lat, last_lon, last_change_time, last_takeoff_location, last_idle_notification_time = get_current_state()

    if current_state == "landed" and not last_change_time:
        log_message("State file has invalid last_change_time. Initializing it now to establish a baseline.")
        current_time_for_init = datetime.datetime.now(pytz.UTC).timestamp()
        set_current_state(
            current_state,
            last_lat,
            last_lon,
            current_time_for_init,
            last_takeoff_location,
            last_idle_notification_time
        )
        current_state, last_lat, last_lon, last_change_time, last_takeoff_location, last_idle_notification_time = get_current_state()

    SCRIPT_RUN_TZ = get_timezone_from_coordinates(last_lat, last_lon)
    log_message(f"--- Main script execution started (using timezone: {SCRIPT_RUN_TZ}) ---")
    current_utc_dt = datetime.datetime.now(pytz.UTC)
    current_time_timestamp = current_utc_dt.timestamp()
    time_since_last_change = current_time_timestamp - last_change_time
    log_message(f"State file says: {current_state} (Last Lat: {last_lat}, Last Lon: {last_lon}, Last Change Time: {datetime.datetime.fromtimestamp(last_change_time, tz=pytz.UTC).strftime('%Y-%m-%d %H:%M:%S UTC') if last_change_time else 'N/A'}, Last Takeoff Loc: {last_takeoff_location}, Last Idle Notif Time: {datetime.datetime.fromtimestamp(last_idle_notification_time, tz=pytz.UTC).strftime('%Y-%m-%d %H:%M:%S UTC') if last_idle_notification_time else 'N/A'})")
    log_message(f"Time since last state change: {time_since_last_change:.1f} seconds")
    plane_data = get_plane_data(PLANE_CODE, spoof_data=spoof_data)
    aircraft_name = get_aircraft_display_name(plane_data or {})

    # --- IDLE NOTIFICATION LOGIC ---
    if not plane_data:
        log_message("No plane data received from APIs.")
        if current_state == "landed":
            start_time_for_idle_check = last_idle_notification_time or last_change_time
            time_since_idle_event = current_time_timestamp - start_time_for_idle_check
            
            if time_since_idle_event >= IDLE_NOTIFICATION_THRESHOLD_SECONDS:
                log_message(f"Plane has been idle for over {IDLE_NOTIFICATION_THRESHOLD_HOURS} hours. Sending notification.")
                
                idle_location = get_location_name(last_lat, last_lon)
                idle_message = f"✈️ {aircraft_name} has been idle on the ground at **{idle_location}** for over {IDLE_NOTIFICATION_THRESHOLD_HOURS} hours."
                
                post_to_bluesky(idle_message, test_mode=test_mode)
                send_email(f"Plane Tracker: Idle Alert!", idle_message, RECIPIENT_EMAIL)
                
                set_current_state(current_state, last_lat, last_lon, last_change_time, takeoff_location_name=last_takeoff_location, last_idle_notification_time=current_time_timestamp)
            else:
                log_message("No plane data, and plane was already landed, but not yet time for another idle notification.")
        return
    
    # --- FLIGHT STATUS CHANGE LOGIC ---
    alt = plane_data.get("alt_baro")
    gs = plane_data.get("gs")
    lat = plane_data.get("lat")
    lon = plane_data.get("lon")
    is_flying = (alt is not None and alt > ALTITUDE_THRESHOLD) or (gs is not None and gs > GROUND_SPEED_THRESHOLD)
    log_message(f"API data: alt_baro={alt}, gs={gs}, lat={lat}, lon={lon}")
    log_message(f"Detected is_flying={is_flying}")

    if time_since_last_change < MIN_STATE_CHANGE_TIME and not test_mode:
        log_message(f"Minimum time threshold not met ({MIN_STATE_CHANGE_TIME}s). Skipping state change.")
        if lat is not None and lon is not None:
            set_current_state(current_state, lat, lon, last_change_time, takeoff_location_name=last_takeoff_location, last_idle_notification_time=last_idle_notification_time)
        return

    # TAKEOFF LOGIC
    if is_flying and current_state == "landed":
        takeoff_location = get_location_name(last_lat, last_lon)
        if lat and lon:
            utc_time_str, local_time_str = format_full_time_for_location(current_utc_dt, lat, lon)
            
            bluesky_takeoff_message = (
                f"✈️ {aircraft_name} has taken off from **{takeoff_location}**.\n"
                f"* **Coords:** {lat:.4f}, {lon:.4f}\n"
                f"* **Time:** {local_time_str} ({utc_time_str})"
            )
            post_to_bluesky(bluesky_takeoff_message, test_mode=test_mode)
            
        set_current_state("flying", lat, lon, current_time_timestamp, takeoff_location_name=takeoff_location, last_idle_notification_time=0)
    
    # LANDING LOGIC
    elif not is_flying and current_state == "flying":
        landing_location = get_location_name(lat, lon)
        if lat and lon and last_lat and last_lon:
            utc_time_str, local_time_str = format_full_time_for_location(current_utc_dt, lat, lon)
            
            bluesky_landing_message_1 = (
                f"🛬 {aircraft_name} has landed in **{landing_location}**.\n"
                f"* **Coords:** {lat:.4f}, {lon:.4f}\n"
                f"* **Time:** {local_time_str} ({utc_time_str})"
            )
            post_to_bluesky(bluesky_landing_message_1, test_mode=test_mode)
            
            flight_metrics = calculate_flight_metrics(last_lat, last_lon, lat, lon)
            bluesky_landing_message_2 = (
                f"📊 **Flight Summary:**\n"
                f"* **Distance:** ~{flight_metrics['distance_nm']:.0f} nautical miles\n"
                f"* **CO₂ Emissions:** ~{flight_metrics['co2_tons']:.1f} tons\n"
                f"* **Equivalent to:** ~{flight_metrics['equivalent_car_miles']:,} miles driven by an average car."
            )
            post_to_bluesky(bluesky_landing_message_2, test_mode=test_mode)
            
        set_current_state("landed", lat, lon, current_time_timestamp, takeoff_location_name=None, last_idle_notification_time=0)
        
    else:
        log_message("No change in plane status.")
        if lat is not None and lon is not None:
            set_current_state(current_state, lat, lon, last_change_time, takeoff_location_name=last_takeoff_location, last_idle_notification_time=last_idle_notification_time)
            
    log_message("--- Main script execution finished ---")

if __name__ == "__main__":
    main()
```
