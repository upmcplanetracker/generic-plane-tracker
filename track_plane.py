#!/usr/bin/env python3
import requests
import os
import datetime
import json
import csv
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
AIRCRAFT_FLEET_STR = os.getenv("AIRCRAFT_FLEET")

# ADSBexchange API Key from RapidAPI
ADSBEXCHANGE_API_KEY = os.getenv("ADSBEXCHANGE_API_KEY")

# Blue Sky Credentials
BLUESKY_HANDLE = os.getenv("BLUESKY_HANDLE")
BLUESKY_APP_PASSWORD = os.getenv("BLUESKY_APP_PASSWORD")

# Other Configurations
RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL")
GEOLOCATOR_EMAIL = os.getenv("GEOLOCATOR_EMAIL", "plane-tracker@example.com")
DEFAULT_TIMEZONE = os.getenv("DEFAULT_TIMEZONE", "America/New_York")


# --- Script Behavior & Thresholds ---
ALTITUDE_THRESHOLD = int(os.getenv("ALTITUDE_THRESHOLD", "500"))
GROUND_SPEED_THRESHOLD = int(os.getenv("GROUND_SPEED_THRESHOLD", "50"))
MIN_STATE_CHANGE_TIME = int(os.getenv("MIN_STATE_CHANGE_TIME", "900")) # Changed to 15 minutes
LOG_RETENTION_HOURS = int(os.getenv("LOG_RETENTION_HOURS", "36"))
RETRY_COUNT = int(os.getenv("RETRY_COUNT", "2"))
RETRY_DELAY = int(os.getenv("RETRY_DELAY", "2"))
OFFLINE_THRESHOLD = 900 # 15 minutes

# --- Aircraft & Flight Metrics ---
DEFAULT_FUEL_BURN_GAL_PER_NM = float(os.getenv("DEFAULT_FUEL_BURN_GAL_PER_NM", "0.97"))
JET_FUEL_CO2_LBS_PER_GALLON = float(os.getenv("JET_FUEL_CO2_LBS_PER_GALLON", "21.1"))
LBS_PER_METRIC_TON = float(os.getenv("LBS_PER_METRIC_TON", "2204.62"))
CO2_TONS_PER_AVG_CAR_MILE = float(os.getenv("CO2_TONS_PER_AVG_CAR_MILE", "0.0004"))

# --- Static & Derived Configuration ---
EARTH_RADIUS_NM = 3440.065

# --- API Endpoints ---
ADSBEXCHANGE_API_URL = "https://adsbexchange-com1.p.rapidapi.com/v2/hex/"

# --- Geocoding configuration ---
GEOLOCATOR_USER_AGENT = f"PlaneTrackerApp/1.0 ({GEOLOCATOR_EMAIL})"

# --- File Paths ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(SCRIPT_DIR, "plane_states.json")
LOG_FILE = os.path.join(SCRIPT_DIR, "plane_tracker.log")
LOCK_FILE_DIR = SCRIPT_DIR


# --- Global Instances ---
TZ_FINDER = TimezoneFinder()

# --- Logging ---
def log_message(message, source_api=None):
    """Logs a message to the console and a file, with timezone info and log rotation."""
    utc_now = datetime.datetime.now(pytz.UTC)
    utc_timestamp_str = utc_now.strftime("%Y-%m-%d %H:%M:%S UTC")

    try:
        local_tz = pytz.timezone(DEFAULT_TIMEZONE)
    except pytz.UnknownTimeZoneError:
        print(f"Invalid DEFAULT_TIMEZONE '{DEFAULT_TIMEZONE}'. Falling back to UTC.")
        local_tz = pytz.UTC

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
    """Gets the timezone object from a given latitude and longitude."""
    if latitude is None or longitude is None:
        return pytz.UTC
    try:
        tz_name = TZ_FINDER.timezone_at(lat=latitude, lng=longitude)
        if tz_name:
            return pytz.timezone(tz_name)
        else:
            return pytz.UTC
    except Exception as e:
        log_message(f"Error getting timezone for {latitude}, {longitude}: {e}. Using UTC.")
        return pytz.UTC

def format_full_time_for_location(utc_dt: datetime.datetime, latitude: float, longitude: float) -> tuple[str, str]:
    """Formats a UTC datetime into a user-friendly local time string."""
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
def get_plane_data(icao_hex: str, spoof_data: dict = None) -> dict:
    """Fetches plane data from ADSBexchange API via RapidAPI, with retries and email alerts."""
    if spoof_data is not None:
        log_message(f"Using SPOOFED DATA for {icao_hex}: {spoof_data}")
        return spoof_data

    headers = {
        "x-rapidapi-host": "adsbexchange-com1.p.rapidapi.com",
        "x-rapidapi-key": ADSBEXCHANGE_API_KEY
    }

    url = f"{ADSBEXCHANGE_API_URL}{icao_hex}/"

    for attempt in range(RETRY_COUNT + 1):
        try:
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            if data and 'ac' in data and data['ac']:
                log_message(f"Successfully fetched data for {icao_hex} from ADSBexchange.", source_api="ADSBexchange")
                return data['ac'][0]
            else:
                log_message(f"No aircraft data found for ICAO {icao_hex} from ADSBexchange.", source_api="ADSBexchange")
                return None
        except requests.exceptions.RequestException as e:
            log_message(f"ADSBexchange API call for {icao_hex} failed on attempt {attempt + 1}: {e}", source_api="ADSBexchange")
            if attempt < RETRY_COUNT:
                sleep(RETRY_DELAY)
            else: # This is the final attempt
                log_message(f"CRITICAL: All API requests to ADSBexchange failed for {icao_hex}.")
                email_subject = f"Plane Tracker CRITICAL: ADSBexchange API Failure"
                email_body = f"The script failed to get data for ICAO {icao_hex} from ADSBexchange after {RETRY_COUNT + 1} attempts.\n\nLast error: {e}"
                send_email(email_subject, email_body, RECIPIENT_EMAIL)

    return None


def get_location_name(latitude: float, longitude: float) -> str:
    """Converts coordinates into a human-readable location string."""
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

# --- State Management Functions (JSON-based) ---
def load_all_states() -> dict:
    """Loads all aircraft states from the JSON file."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except json.JSONDecodeError:
            log_message(f"Error decoding JSON from {STATE_FILE}. Starting with a fresh state.")
            return {}
    return {}

def save_all_states(states: dict):
    """Saves all aircraft states to the JSON file."""
    with open(STATE_FILE, "w") as f:
        json.dump(states, f, indent=4)

def get_current_state_for_plane(icao_hex: str, all_states: dict) -> dict:
    """Gets the state for a specific aircraft, returning a default if not found."""
    default_state = {
        "state": "landed",
        "last_lat": None,
        "last_lon": None,
        "takeoff_lat": None,
        "takeoff_lon": None,
        "last_change_time": 0,
        "last_seen_time": 0,
        "last_takeoff_location_name": None,
        "monthly_distance": 0.0,
        "monthly_co2": 0.0,
        "monthly_car_miles": 0.0
    }
    saved_state = all_states.get(icao_hex, {})
    return {**default_state, **saved_state}

def set_current_state_for_plane(icao_hex: str, owner_name: str, all_states: dict, new_state_data: dict):
    """Updates the state for a specific aircraft in the main states dictionary."""
    all_states[icao_hex] = new_state_data
    log_message(f"[{owner_name} / {icao_hex}] State updated in memory to: {new_state_data['state']}")

# --- Utility & Formatting Functions ---
def get_aircraft_display_name(plane_data: dict, icao_hex: str, owner_name: str) -> str:
    """Creates a display name for the aircraft."""
    return f"**{owner_name}** ({icao_hex.upper()})"

def send_email(subject: str, body: str, recipient_email: str):
    """Sends an email using the system's 'mail' command."""
    if not recipient_email:
        log_message("RECIPIENT_EMAIL not set. Skipping email notification.")
        return
    try:
        command = ['mail', '-s', subject, recipient_email]
        subprocess.run(command, input=body.encode('utf-8'), capture_output=True, check=True)
        log_message(f"Email sent to {recipient_email} with subject '{subject}'.")
    except FileNotFoundError:
        log_message("CRITICAL: 'mail' command not found. Cannot send email notification.")
    except Exception as e:
        log_message(f"An error occurred while sending email: {e}")

def post_to_bluesky(message: str, test_mode: bool = False):
    """Posts a message to a Blue Sky account, optionally as a reply."""
    if not BLUESKY_HANDLE or not BLUESKY_APP_PASSWORD:
        log_message("Bluesky credentials not set. Skipping post.")
        return None
    if test_mode:
        message = f"[TEST]\n{message}"
    log_message(f"Bluesky Post: {message}")
    try:
        session = BskySession(BLUESKY_HANDLE, BLUESKY_APP_PASSWORD)
        post_ref = post_text(session, message)
        log_message(f"Posted to Bluesky successfully.")
        return post_ref
    except Exception as e:
        log_message(f"Error posting to Bluesky: {e}")
        return None

def calculate_flight_metrics(lat1: float, lon1: float, lat2: float, lon2: float, fuel_burn_rate: float) -> dict:
    """Calculates flight distance, fuel, and CO2 emissions using a specific fuel burn rate."""
    if any(coord is None for coord in [lat1, lon1, lat2, lon2]):
        return {'distance_nm': 0.0, 'fuel_gallons': 0.0, 'co2_tons': 0.0, 'equivalent_car_miles': 0.0}

    lat1_rad, lon1_rad, lat2_rad, lon2_rad = map(math.radians, [lat1, lon1, lat2, lon2])
    dlon = lon2_rad - lon1_rad
    dlat = lat2_rad - lat1_rad
    a = math.sin(dlat / 2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    distance_nm = EARTH_RADIUS_NM * c

    fuel_gallons = distance_nm * fuel_burn_rate
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

def validate_coordinates(lat, lon):
    """Checks if latitude and longitude values are physically possible."""
    return (lat is not None and lon is not None and
            -90 <= lat <= 90 and -180 <= lon <= 180)

def validate_config():
    """Checks that all critical environment variables are set before the script runs."""
    required_vars = ['AIRCRAFT_FLEET', 'BLUESKY_HANDLE', 'BLUESKY_APP_PASSWORD', 'ADSBEXCHANGE_API_KEY']
    missing = [var for var in required_vars if not os.getenv(var)]
    if missing:
        log_message(f"CRITICAL: Missing required environment variables: {missing}")
        return False
    return True

# --- Main Processing Logic ---
def process_plane(aircraft_details: dict, all_states: dict, spoof_data: dict = None, test_mode: bool = False):
    """Processes a single aircraft for status changes."""
    icao_hex = aircraft_details['icao']
    owner_name = aircraft_details['owner']
    fuel_burn_rate = aircraft_details['fuel_burn']

    log_prefix = f"[{owner_name} / {icao_hex}]"
    log_message(f"--- Processing aircraft: {owner_name} ({icao_hex}) ---")
    plane_state_data = get_current_state_for_plane(icao_hex, all_states)

    current_state = plane_state_data["state"]
    last_lat = plane_state_data["last_lat"]
    last_lon = plane_state_data["last_lon"]
    last_change_time = plane_state_data["last_change_time"]
    last_seen_time = plane_state_data["last_seen_time"]
    last_takeoff_location = plane_state_data["last_takeoff_location_name"]
    takeoff_lat = plane_state_data.get('takeoff_lat')
    takeoff_lon = plane_state_data.get('takeoff_lon')

    current_utc_dt = datetime.datetime.now(pytz.UTC)
    current_time_timestamp = current_utc_dt.timestamp()
    time_since_last_change = current_time_timestamp - last_change_time
    time_since_last_seen = current_time_timestamp - last_seen_time

    log_message(f"{log_prefix} Current state: {current_state}. Time since last change: {time_since_last_change:.1f}s")
    plane_data = get_plane_data(icao_hex, spoof_data=spoof_data)

    if not plane_data:
        log_message(f"{log_prefix} No plane data received from APIs.")
        if current_state == "flying" and (time_since_last_seen < OFFLINE_THRESHOLD):
            log_message(f"{log_prefix} Plane was flying and seen recently. Assuming it has landed.")
            landing_location = get_location_name(last_lat, last_lon)
            log_message(f"{log_prefix} LANDING (assumed from no data) detected in {landing_location}")

            utc_time_str, local_time_str = format_full_time_for_location(current_utc_dt, last_lat, last_lon)
            timestamp_line = f"⏰ {local_time_str} / {utc_time_str}"
            gps_line = f"📍 ({last_lat:.4f}, {last_lon:.4f})"
            aircraft_display = get_aircraft_display_name(plane_data, icao_hex, owner_name)

            bluesky_landing_message_1 = (
                f"🛬 {aircraft_display} has landed in **{landing_location}**.\n"
                f"{timestamp_line}\n"
                f"{gps_line}"
            )
            post_to_bluesky(bluesky_landing_message_1, test_mode=test_mode)

            if last_takeoff_location:
                metrics = calculate_flight_metrics(takeoff_lat, takeoff_lon, last_lat, last_lon, fuel_burn_rate)
                plane_state_data['monthly_distance'] += metrics.get('distance_nm', 0)
                plane_state_data['monthly_co2'] += metrics.get('co2_tons', 0)
                plane_state_data['monthly_car_miles'] += metrics.get('equivalent_car_miles', 0)

                bluesky_landing_message_2 = (
                    f"📊 **Flight Summary for {owner_name} jet:**\n"
                    f"• **Route:** {last_takeoff_location} to {landing_location}\n"
                    f"• **Distance:** ~{metrics.get('distance_nm', 0):.0f} nautical miles\n"
                    f"• **CO₂ Emissions:** ~{metrics.get('co2_tons', 0):.1f} tons\n"
                    f"• **Equivalent to:** ~{metrics.get('equivalent_car_miles', 0):,} miles driven by an average car."
                )
                post_to_bluesky(bluesky_landing_message_2, test_mode=test_mode)

            plane_state_data.update({
                "state": "landed", "last_lat": last_lat, "last_lon": last_lon,
                "last_change_time": current_time_timestamp, "last_takeoff_location_name": None,
                "takeoff_lat": None, "takeoff_lon": None
            })
            set_current_state_for_plane(icao_hex, owner_name, all_states, plane_state_data)
        elif current_state == "landed":
            log_message(f"{log_prefix} No plane data, and plane was already landed. No new action taken.")
        return

    plane_state_data['last_seen_time'] = current_time_timestamp

    lat, lon = plane_data.get("lat"), plane_data.get("lon")
    if not validate_coordinates(lat, lon):
        log_message(f"{log_prefix} Invalid coordinates received from API: lat={lat}, lon={lon}. Skipping processing for this cycle.")
        return

    try:
        alt = plane_data.get("alt_baro")
        gs = plane_data.get("gs")
        is_flying = (int(alt) > ALTITUDE_THRESHOLD if alt != "ground" else False) or (gs is not None and float(gs) > GROUND_SPEED_THRESHOLD)
    except (ValueError, TypeError):
        is_flying = False

    log_message(f"{log_prefix} API data: alt_baro={alt}, gs={gs}, lat={lat}, lon={lon}")
    log_message(f"{log_prefix} Determined flight status: is_flying={is_flying}")

    state_changed = (is_flying and current_state == "landed") or (not is_flying and current_state == "flying")

    if state_changed:
        if time_since_last_change < MIN_STATE_CHANGE_TIME and not test_mode:
            log_message(f"{log_prefix} State changed, but flapping threshold not met. Skipping post.")
            return

        aircraft_display = get_aircraft_display_name(plane_data, icao_hex, owner_name)
        if is_flying:
            location = get_location_name(last_lat, last_lon) or "an unknown location"
            log_message(f"{log_prefix} TAKEOFF detected from {location}")

            utc_time_str, local_time_str = format_full_time_for_location(current_utc_dt, last_lat, last_lon)

            post_to_bluesky(
                f"✈️ {aircraft_display} has taken off from **{location}**.\n"
                f"⏰ {local_time_str} / {utc_time_str}\n"
                f"📍 ({last_lat:.4f}, {last_lon:.4f})\n"
                f"Track: https://globe.adsb.fi/?icao={icao_hex.lower()}",
                test_mode=test_mode
            )

            plane_state_data.update({
                "state": "flying", "last_lat": lat, "last_lon": lon,
                "takeoff_lat": last_lat, "takeoff_lon": last_lon,
                "last_change_time": current_time_timestamp, "last_takeoff_location_name": location
            })
        else: # Plane is landing
            location = get_location_name(lat, lon) or "an unknown location"
            log_message(f"{log_prefix} LANDING detected in {location}")

            utc_time_str, local_time_str = format_full_time_for_location(current_utc_dt, lat, lon)

            post_to_bluesky(
                f"🛬 {aircraft_display} has landed in **{location}**.\n"
                f"⏰ {local_time_str} / {utc_time_str}\n"
                f"📍 ({lat:.4f}, {lon:.4f})",
                test_mode=test_mode
            )

            if last_takeoff_location:
                metrics = calculate_flight_metrics(takeoff_lat, takeoff_lon, lat, lon, fuel_burn_rate)
                plane_state_data['monthly_distance'] += metrics.get('distance_nm', 0)
                plane_state_data['monthly_co2'] += metrics.get('co2_tons', 0)
                plane_state_data['monthly_car_miles'] += metrics.get('equivalent_car_miles', 0)

                post_to_bluesky(
                    f"📊 **Flight Summary for {owner_name} jet:**\n"
                    f"• **Route:** {last_takeoff_location} to {location}\n"
                    f"• **Distance:** ~{metrics.get('distance_nm', 0):.0f} nautical miles\n"
                    f"• **CO₂ Emissions:** ~{metrics.get('co2_tons', 0):.1f} tons\n"
                    f"• **Equivalent to:** ~{metrics.get('equivalent_car_miles', 0):,} miles driven by an average car.",
                    test_mode=test_mode
                )

            plane_state_data.update({
                "state": "landed", "last_lat": lat, "last_lon": lon,
                "last_change_time": current_time_timestamp, "last_takeoff_location_name": None
            })

        set_current_state_for_plane(icao_hex, owner_name, all_states, plane_state_data)
    elif is_flying and current_state == "flying":
        log_message(f"{log_prefix} No change in plane status (still flying).")
        plane_state_data["last_lat"] = lat
        plane_state_data["last_lon"] = lon
        set_current_state_for_plane(icao_hex, owner_name, all_states, plane_state_data)
    else:
        log_message(f"{log_prefix} No change in plane status (still landed).")

def parse_fleet_config(config_str: str) -> list:
    """Parses the semi-colon delimited fleet config string from the .env file."""
    fleet = []
    if not config_str: return []
    for record in config_str.strip().split(';'):
        if not record.strip(): continue
        try:
            parts = next(csv.reader([record], quotechar='"', delimiter=',', skipinitialspace=True))
            if len(parts) == 3:
                fleet.append({'icao': parts[0].lower(), 'owner': parts[1], 'fuel_burn': float(parts[2])})
            else:
                log_message(f"CRITICAL: Incorrect parts in fleet record: '{record}'")
        except (IndexError, ValueError, StopIteration) as e:
            log_message(f"CRITICAL: Could not parse fleet record: '{record}'. Error: {e}")
    return fleet

# --- NEW: Replaces the old calendar-day logic ---
def post_daily_stationary_report(all_states: dict, aircraft_fleet: list, test_mode: bool = False):
    """
    Checks for planes that have been stationary for more than 24 hours
    and posts a single summary report. Runs only once per day.
    """
    log_message("--- Checking for Daily Stationary Report ---")
    local_tz = pytz.timezone(DEFAULT_TIMEZONE)
    today_str = datetime.datetime.now(local_tz).strftime('%Y-%m-%d')
    lock_file_path = os.path.join(LOCK_FILE_DIR, f"daily_report_sent_{today_str}.lock")

    if os.path.exists(lock_file_path):
        log_message("Daily stationary report has already been sent today. Skipping.")
        return # Exit if the report for today has already been sent

    stationary_aircraft_names = []
    current_time_ts = datetime.datetime.now(pytz.UTC).timestamp()
    SECONDS_IN_24_HOURS = 24 * 60 * 60

    # Create a quick lookup for owner names from the fleet config
    fleet_info = {ac['icao']: ac['owner'] for ac in aircraft_fleet}

    for icao, state_data in all_states.items():
        if icao == 'global_state':
            continue

        if state_data.get("state") == "landed":
            time_since_change = current_time_ts - state_data.get("last_change_time", 0)
            if time_since_change > SECONDS_IN_24_HOURS:
                owner_name = fleet_info.get(icao, icao.upper())
                stationary_aircraft_names.append(f"**{owner_name}**")

    if stationary_aircraft_names:
        log_message(f"Found {len(stationary_aircraft_names)} stationary aircraft. Posting summary.")
        # Sort for consistent post formatting
        stationary_aircraft_names.sort()

        message = "The following aircraft have not flown in the last 24 hours:\n\n"
        message += "\n".join([f"• {name}" for name in stationary_aircraft_names])

        post_to_bluesky(message, test_mode=test_mode)
    else:
        log_message("No aircraft have been stationary for over 24 hours. No report needed.")

    # Create the lock file to prevent this function from running again today
    try:
        with open(lock_file_path, 'w') as f:
            f.write(f"Report sent at {datetime.datetime.now(local_tz).isoformat()}")
        log_message(f"Created daily report lock file: {lock_file_path}")
    except Exception as e:
        log_message(f"CRITICAL: Failed to create daily lock file: {e}")


def handle_monthly_summary(all_states: dict, aircraft_fleet: list, test_mode: bool = False):
    """Checks if a new month has started, posts a summary, and resets monthly totals."""
    now = datetime.datetime.now(pytz.UTC)
    current_month_str = now.strftime('%Y-%m')
    global_state = all_states.get('global_state', {})

    if global_state.get('last_summary_month') == current_month_str:
        return

    if last_summary_month := global_state.get('last_summary_month'):
        summary_lines = []
        fleet_info = {ac['icao']: ac['owner'] for ac in aircraft_fleet}

        for icao, state_data in all_states.items():
            if icao == 'global_state' or not state_data.get('monthly_distance', 0) > 0: continue
            owner = fleet_info.get(icao, icao)
            dist = state_data.get('monthly_distance', 0)
            co2 = state_data.get('monthly_co2', 0)
            car_miles = state_data.get('monthly_car_miles', 0)
            summary_lines.append(f"• **{owner}**: ~{dist:,.0f} nm, ~{co2:,.1f} tons CO₂, ~{car_miles:,.0f} car miles")

        if summary_lines:
            header = f"📊 Monthly Flight Summary for {datetime.datetime.strptime(last_summary_month, '%Y-%m').strftime('%B %Y')}:"
            if root_post := post_to_bluesky(header, test_mode=test_mode):
                parent_post = root_post
                for line in summary_lines:
                    if reply_post := post_to_bluesky(line, test_mode=test_mode):
                        parent_post = reply_post

        for icao in all_states:
            if icao != 'global_state':
                all_states[icao].update({'monthly_distance': 0.0, 'monthly_co2': 0.0, 'monthly_car_miles': 0.0})
        log_message("Monthly flight summaries have been reset.")

    global_state['last_summary_month'] = current_month_str
    all_states['global_state'] = global_state

def main():
    """Main function to run the aircraft tracker for all configured planes."""
    log_message("--- Main script execution started ---")

    if not validate_config(): return

    aircraft_fleet = parse_fleet_config(AIRCRAFT_FLEET_STR)
    if not aircraft_fleet:
        log_message("CRITICAL: AIRCRAFT_FLEET is empty or could not be parsed. Exiting.")
        return

    all_states = load_all_states()

    # Afer 12:01 AM local time, run the daily stationary report
    post_daily_stationary_report(all_states, aircraft_fleet)

    # The rest of the script runs every time, regardless of the daily report
    handle_monthly_summary(all_states, aircraft_fleet)

    for aircraft in aircraft_fleet:
        try:
            process_plane(aircraft, all_states)
        except Exception as e:
            log_message(f"CRITICAL ERROR processing {aircraft['owner']} ({aircraft['icao']}): {e}")
            send_email(f"Plane Tracker CRITICAL ERROR for {aircraft['owner']}", f"An unhandled exception occurred: {e}", RECIPIENT_EMAIL)

    save_all_states(all_states)
    log_message("--- Main script execution finished ---")

if __name__ == "__main__":
    main()
