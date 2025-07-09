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

# --- API Endpoints ---
ADSB_LOL_API_URL = "https://re-api.adsb.lol/v2/aircraft.json"
ADSB_FI_API_URL = "https://opendata.adsb.fi/api/v2/hex/{icao_hex}"

# --- Geocoding configuration ---
GEOLOCATOR_USER_AGENT = f"PlaneTrackerApp/1.0 ({GEOLOCATOR_EMAIL})"

# --- File Paths ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(SCRIPT_DIR, "plane_states.json")
LOG_FILE = os.path.join(SCRIPT_DIR, "plane_tracker.log")

# --- Global Instances ---
TZ_FINDER = TimezoneFinder()

# --- Logging ---
def log_message(message, source_api=None):
    """Logs a message to the console and a file, with timezone info and log rotation."""
    utc_now = datetime.datetime.now(pytz.UTC)
    utc_timestamp_str = utc_now.strftime("%Y-%m-%d %H:%M:%S UTC")

    try:
        local_tz = pytz.timezone("America/New_York")
    except pytz.UnknownTimeZoneError:
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
def _fetch_data_from_api(url: str, source_name: str, icao_hex: str, suppress_email_on_fail: bool = False) -> tuple[dict | None, bool, str | None]:
    """Internal function to fetch data from a single API with retries."""
    error_msg = None
    for attempt in range(RETRY_COUNT + 1):
        try:
            if source_name == "adsb.lol":
                params = {"find_hex": icao_hex}
                response = requests.get(url, params=params, timeout=5)
            else:
                full_url = url.format(icao_hex=icao_hex)
                response = requests.get(full_url, timeout=5)

            response.raise_for_status()
            
            data = response.json()
            if data and 'aircraft' in data and data['aircraft']:
                log_message(f"Successfully fetched data for {icao_hex} from {source_name}.", source_api=source_name)
                return data['aircraft'][0], True, None
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
            
    log_message(f"Failed to fetch data for {icao_hex} from {source_name} after {RETRY_COUNT + 1} attempts.", source_api=source_name)
    return None, False, error_msg

def get_plane_data(icao_hex: str, spoof_data: dict = None) -> dict:
    """Fetches plane data from primary API, with a failover to a secondary API."""
    if spoof_data is not None:
        log_message(f"Using SPOOFED DATA for {icao_hex}: {spoof_data}")
        return spoof_data

    plane_data_lol, lol_api_successful, lol_error_msg = _fetch_data_from_api(ADSB_LOL_API_URL, "adsb.lol", icao_hex, suppress_email_on_fail=True)
    if plane_data_lol:
        return plane_data_lol

    if not lol_api_successful:
        log_message(f"ADSB.lol API call failed for {icao_hex}. Attempting to fetch data from ADSB.fi (failover).", source_api="adsb.fi")
        plane_data_fi, fi_api_successful, fi_error_msg = _fetch_data_from_api(ADSB_FI_API_URL, "adsb.fi", icao_hex)
        if plane_data_fi:
            log_message(f"Data successfully retrieved for {icao_hex} from adsb.fi after ADSB.lol failure.", source_api="adsb.fi")
            return plane_data_fi
        else:
            if not fi_api_successful:
                log_message(f"ADSB.fi also failed for {icao_hex}. No plane data could be retrieved from any source.", source_api="none")
                email_subject = f"CRITICAL: Plane Tracker - Both APIs Failed for {icao_hex}!"
                email_body = (
                    f"Your plane tracker script failed to retrieve data for {icao_hex} from both ADSB.lol and ADSB.fi.\n\n"
                    f"ADSB.lol error: {lol_error_msg if lol_error_msg else 'No specific error message recorded.'}\n"
                    f"ADSB.fi error: {fi_error_msg if fi_error_msg else 'No specific error message recorded.'}\n\n"
                    f"Please check the log file for more details."
                )
                send_email(email_subject, email_body, RECIPIENT_EMAIL)
            else:
                log_message(f"ADSB.fi responded successfully but found no aircraft data for {icao_hex}.", source_api="adsb.fi (no data)")
    else:
        log_message(f"ADSB.lol responded successfully but found no aircraft data for {icao_hex}. Not failing over.", source_api="adsb.lol (no data)")

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
    return all_states.get(icao_hex, {
        "state": "landed",
        "last_lat": None,
        "last_lon": None,
        "last_change_time": 0,
        "last_takeoff_location_name": None,
        "last_idle_notification_time": 0
    })

def set_current_state_for_plane(icao_hex: str, owner_name: str, all_states: dict, new_state_data: dict):
    """Updates the state for a specific aircraft in the main states dictionary."""
    all_states[icao_hex] = new_state_data
    log_message(f"[{owner_name} / {icao_hex}] State updated in memory to: {new_state_data['state']}")

# --- Utility & Formatting Functions ---
def get_aircraft_display_name(plane_data: dict, icao_hex: str, owner_name: str) -> str:
    """Creates a display name for the aircraft, e.g., 'UPMC jet (N950UP / A55555)'."""
    registration = plane_data.get("r")
    if registration:
        return f"**{owner_name}** jet ({registration} / {icao_hex.upper()})"
    else:
        return f"The **{owner_name}** aircraft ({icao_hex.upper()})"

def get_Maps_link(latitude: float, longitude: float) -> str:
    """Generates a Google Maps link for the given coordinates."""
    if latitude is None or longitude is None:
        return "N/A"
    return f"https://www.google.com/maps/search/?api=1&query={latitude},{longitude}"

def send_email(subject: str, body: str, recipient_email: str):
    """Sends an email using the system's 'mail' command."""
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
    """Posts a message to a Blue Sky account."""
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
    last_takeoff_location = plane_state_data["last_takeoff_location_name"]
    last_idle_notification_time = plane_state_data["last_idle_notification_time"]

    current_utc_dt = datetime.datetime.now(pytz.UTC)
    current_time_timestamp = current_utc_dt.timestamp()
    time_since_last_change = current_time_timestamp - last_change_time
    track_link = f"Track: https://globe.adsb.fi/?icao={icao_hex.lower()}"

    log_message(f"{log_prefix} Current state: {current_state}. Time since last change: {time_since_last_change:.1f}s")
    plane_data = get_plane_data(icao_hex, spoof_data=spoof_data)
    
    if not plane_data:
        log_message(f"{log_prefix} No plane data received from APIs.")
        if current_state == "landed":
            start_time_for_idle_check = last_idle_notification_time or last_change_time
            if not start_time_for_idle_check:
                start_time_for_idle_check = current_time_timestamp
                plane_state_data["last_change_time"] = start_time_for_idle_check
                set_current_state_for_plane(icao_hex, owner_name, all_states, plane_state_data)
            
            time_since_idle_event = current_time_timestamp - start_time_for_idle_check

            if time_since_idle_event >= IDLE_NOTIFICATION_THRESHOLD_SECONDS:
                log_message(f"{log_prefix} Plane has been idle for over {IDLE_NOTIFICATION_THRESHOLD_HOURS} hours. Sending notification.")
                idle_location = get_location_name(last_lat, last_lon)
                aircraft_name_idle = f"The **{owner_name}** aircraft ({icao_hex.upper()})"
                idle_message = (
                    f"✈️ {aircraft_name_idle} has been idle on the ground at **{idle_location}** for over {IDLE_NOTIFICATION_THRESHOLD_HOURS} hours.\n\n"
                    f"{track_link}"
                )
                post_to_bluesky(idle_message, test_mode=test_mode)
                send_email(f"Plane Tracker: Idle Alert for {owner_name}!", idle_message, RECIPIENT_EMAIL)
                plane_state_data["last_idle_notification_time"] = current_time_timestamp
                set_current_state_for_plane(icao_hex, owner_name, all_states, plane_state_data)
            else:
                log_message(f"{log_prefix} No plane data, and plane was already landed, but not yet time for another idle notification.")
        return

    aircraft_name = get_aircraft_display_name(plane_data, icao_hex, owner_name)
    alt = plane_data.get("alt_baro")
    gs = plane_data.get("gs")
    lat = plane_data.get("lat")
    lon = plane_data.get("lon")
    is_flying = (alt is not None and alt > ALTITUDE_THRESHOLD) or (gs is not None and gs > GROUND_SPEED_THRESHOLD)
    log_message(f"{log_prefix} API data: alt_baro={alt}, gs={gs}, lat={lat}, lon={lon}")
    log_message(f"{log_prefix} Detected is_flying={is_flying}")

    # --- State Change Logic ---
    state_changed = (is_flying and current_state == "landed") or (not is_flying and current_state == "flying")

    if state_changed:
        if time_since_last_change < MIN_STATE_CHANGE_TIME and not test_mode:
            log_message(f"{log_prefix} State changed, but minimum time threshold not met ({MIN_STATE_CHANGE_TIME}s). Skipping post to prevent flapping.")
            return

        # TAKEOFF
        if is_flying:
            takeoff_location = get_location_name(last_lat, last_lon) if last_lat and last_lon else "an unknown location"
            log_message(f"{log_prefix} TAKEOFF detected from {takeoff_location}")

            bluesky_takeoff_message = (
                f"✈️ {aircraft_name} has taken off from **{takeoff_location}**.\n"
                f"📍 {get_Maps_link(lat, lon)}\n"
                f"{track_link}"
            )
            post_to_bluesky(bluesky_takeoff_message, test_mode=test_mode)
            
            new_state = { "state": "flying", "last_lat": lat, "last_lon": lon, "last_change_time": current_time_timestamp, "last_takeoff_location_name": takeoff_location, "last_idle_notification_time": 0 }
            set_current_state_for_plane(icao_hex, owner_name, all_states, new_state)
        
        # LANDING
        else:
            landing_location = get_location_name(lat, lon)
            log_message(f"{log_prefix} LANDING detected in {landing_location}")

            bluesky_landing_message_1 = (
                f"🛬 {aircraft_name} has landed in **{landing_location}**.\n"
                f"📍 {get_Maps_link(lat, lon)}\n"
                f"{track_link}"
            )
            post_to_bluesky(bluesky_landing_message_1, test_mode=test_mode)

            if last_takeoff_location:
                flight_metrics = calculate_flight_metrics(last_lat, last_lon, lat, lon, fuel_burn_rate)
                bluesky_landing_message_2 = (
                    f"📊 **Flight Summary for {owner_name} jet:**\n"
                    f"• **Route:** {last_takeoff_location} to {landing_location}\n"
                    f"• **Distance:** ~{flight_metrics['distance_nm']:.0f} nautical miles\n"
                    f"• **CO₂ Emissions:** ~{flight_metrics['co2_tons']:.1f} tons\n"
                    f"• **Equivalent to:** ~{flight_metrics['equivalent_car_miles']:,} miles driven by an average car."
                )
                post_to_bluesky(bluesky_landing_message_2, test_mode=test_mode)

            new_state = { "state": "landed", "last_lat": lat, "last_lon": lon, "last_change_time": current_time_timestamp, "last_takeoff_location_name": None, "last_idle_notification_time": 0 }
            set_current_state_for_plane(icao_hex, owner_name, all_states, new_state)
    
    # If no state change, but plane is flying, update its last known coordinates
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
    if not config_str:
        return fleet
    
    # Use a semicolon as the main delimiter between aircraft records
    records = config_str.strip().split(';')
    for record in records:
        record = record.strip()
        if not record:
            continue
        try:
            # Use the csv module on each individual record to handle quoted names
            reader = csv.reader([record], quotechar='"', delimiter=',', quoting=csv.QUOTE_MINIMAL, skipinitialspace=True)
            parts = next(reader)

            if len(parts) != 3:
                log_message(f"CRITICAL: Incorrect number of parts in fleet config record: '{record}'")
                continue
            
            icao = parts[0]
            owner = parts[1] # The csv module handles un-quoting
            fuel_burn = float(parts[2])
            fleet.append({'icao': icao, 'owner': owner, 'fuel_burn': fuel_burn})
        except (IndexError, ValueError, StopIteration) as e:
            log_message(f"CRITICAL: Could not parse fleet config record: '{record}'. Error: {e}")
    return fleet

def main():
    """Main function to run the aircraft tracker for all configured planes."""
    log_message("--- Main script execution started ---")

    if not AIRCRAFT_FLEET_STR:
        log_message("CRITICAL: AIRCRAFT_FLEET not set in .env file. Exiting.")
        return

    aircraft_fleet = parse_fleet_config(AIRCRAFT_FLEET_STR)
    if not aircraft_fleet:
        log_message("CRITICAL: AIRCRAFT_FLEET is set but could not be parsed or is empty. Exiting.")
        return

    all_states = load_all_states()

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
