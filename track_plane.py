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

# --- Configuration ---
PLANE_CODE = os.getenv("PLANE_CODE")  # ICAO hex of the plane to monitor, loaded from .env
BLUESKY_HANDLE = os.getenv("BLUESKY_HANDLE") 
BLUESKY_APP_PASSWORD = os.getenv("BLUESKY_APP_PASSWORD") 
RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL") 

# API Endpoints
ADSB_LOL_API_URL = "https://api.adsb.lol/v2/hex/{icao_hex}"
ADSB_FI_API_URL = "https://opendata.adsb.fi/api/v2/hex/{icao_hex}"

ALTITUDE_THRESHOLD = 500    # Feet
GROUND_SPEED_THRESHOLD = 50     # Knots
MIN_STATE_CHANGE_TIME = 300      # Minimum seconds between state changes (5 minutes)
IDLE_NOTIFICATION_THRESHOLD_HOURS = 12 # Hours
IDLE_NOTIFICATION_THRESHOLD_SECONDS = IDLE_NOTIFICATION_THRESHOLD_HOURS * 3600

# Get the absolute path of the directory where the script is located
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Define the state file and log file paths relative to the script's location
STATE_FILE = os.path.join(SCRIPT_DIR, "plane_state.txt")
LOG_FILE = os.path.join(SCRIPT_DIR, "plane_tracker.log")
LOG_RETENTION_HOURS = 6 # NEW: Keep logs for the last 6 hours

RETRY_COUNT = 2
RETRY_DELAY = 2

# Geocoding configuration
GEOLOCATOR_USER_AGENT = "PlaneTrackerApp/1.0 (your-email@example.com)" # Replace with your contact email

# Timezone finder instance
TZ_FINDER = TimezoneFinder()

# --- Aviation Constants for Calculations (Loaded from .env or default conservative estimates) ---
EARTH_RADIUS_NM = 3440.065  # Earth's mean radius in Nautical Miles
DEFAULT_FUEL_BURN_GAL_PER_NM = float(os.getenv("DEFAULT_FUEL_BURN_GAL_PER_NM", "1.05")) # Conservative estimate: 512 GPH / 487 Knots
JET_FUEL_CO2_LBS_PER_GALLON = 21.1 # Pounds of CO2 per US gallon of Jet A/A-1
LBS_PER_METRIC_TON = 2204.62 # Pounds per metric ton
CO2_TONS_PER_AVG_CAR_MILE = 0.0004 # Approx.

# --- Logging ---
def log_message(message, source_api=None):
    """
    Logs a message to the console and a rotating log file.
    Reports time in both local (America/New_York) and UTC.
    Includes the API source if provided.
    Log file is pruned to retain only entries from the last LOG_RETENTION_HOURS.
    """
    # Get current UTC time
    utc_now = datetime.datetime.now(pytz.UTC)
    utc_timestamp_str = utc_now.strftime("%Y-%m-%d %H:%M:%S UTC")

    # Define the local timezone for EDT (America/New_York)
    local_tz = pytz.timezone('America/New_York')
    local_now = utc_now.astimezone(local_tz)
    local_timestamp_str = local_now.strftime("%Y-%m-%d %I:%M:%S %p %Z")

    # Add source tag to the log entry if provided
    source_tag = f" [{source_api.upper()}]" if source_api else ""
    log_entry = f"{local_timestamp_str} ({utc_timestamp_str}){source_tag} - {message}\n"

    all_log_entries = []
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r") as f:
            all_log_entries = f.readlines()

    all_log_entries.append(log_entry) 

    # Filter out old entries based on time
    retention_delta = datetime.timedelta(hours=LOG_RETENTION_HOURS)
    current_utc_time_dt = datetime.datetime.now(pytz.UTC) 

    # Regex to find the UTC timestamp in the log line, e.g., "(YYYY-MM-DD HH:MM:SS UTC)"
    timestamp_pattern = re.compile(r'\((\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) UTC\)')

    filtered_entries = []
    for entry in all_log_entries:
        match = timestamp_pattern.search(entry)
        if match:
            log_utc_str_found = match.group(1)
            try:
                # Parse the UTC timestamp and make it timezone-aware
                log_dt_utc = datetime.datetime.strptime(log_utc_str_found, "%Y-%m-%d %H:%M:%S").replace(tzinfo=pytz.UTC)
                # Keep if within retention period
                if current_utc_time_dt - log_dt_utc <= retention_delta:
                    filtered_entries.append(entry)
            except ValueError:
                # If parsing fails, keep the line (might be malformed or an old format)
                filtered_entries.append(entry)
        else:
            # If no timestamp found, keep the line (e.g., from old script versions)
            filtered_entries.append(entry)

    with open(LOG_FILE, "w") as f:
        f.writelines(filtered_entries)

    print(log_entry.strip()) 

# --- Timezone Helper ---
def get_timezone_from_coordinates(latitude: float, longitude: float) -> pytz.BaseTzInfo:
    """
    Gets the timezone for given coordinates.
    """
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
    """
    Formates a UTC datetime object into both UTC and local timezone strings
    for the given coordinates.
    """
    if utc_dt.tzinfo is None or utc_dt.tzinfo.utcoffset(utc_dt) is None:
        utc_dt = pytz.UTC.localize(utc_dt)
    elif utc_dt.tzinfo != pytz.UTC:
        utc_dt = utc_dt.astimezone(pytz.UTC)

    utc_format = "%Y-%m-%d %H:%M:%S UTC"
    utc_time_str = utc_dt.strftime(utc_format)

    if latitude is None or longitude is None:
        return utc_time_str, utc_dt.strftime("%Y-%m-%d %H:%M:%S UTC (No location info)")

    local_tz = get_timezone_from_coordinates(latitude, longitude)
    local_time = utc_dt.astimezone(local_tz)

    local_format = "%Y-%m-%d %I:%M:%S %p %Z%z local time"
    local_time_str = local_time.strftime(local_format)

    return utc_time_str, local_time_str


# --- Data Fetching Functions ---
def _fetch_data_from_api(url: str, source_name: str, icao_hex: str) -> tuple[dict | None, bool, str | None]:
    """
    Helper function to fetch plane data from a specific API URL. Handles retries and logs errors with the source name.
    """
    full_url = url.format(icao_hex=icao_hex)
    error_msg = None
    for attempt in range(RETRY_COUNT + 1):
        try:
            response = requests.get(full_url, timeout=5)
            response.raise_for_status()
            data = response.json()

            if source_name == "adsb.lol":
                if data and 'ac' in data and data['ac']:
                    log_message(f"Successfully fetched data from {source_name}.", source_api=source_name)
                    return data['ac'][0], True, None
                else:
                    log_message(f"No aircraft data found for ICAO {icao_hex} from {source_name}.", source_api=source_name)
                    return None, True, None
            elif source_name == "adsb.fi":
                if data and 'aircraft' in data and data['aircraft']:
                    log_message(f"Successfully fetched data from {source_name}.", source_api=source_name)
                    return data['aircraft'][0], True, None
                else:
                    log_message(f"No aircraft data found for ICAO {icao_hex} from {source_name}.", source_api=source_name)
                    return None, True, None
            else:
                log_message(f"Unknown API source: {source_name}", source_api=source_name)
                error_msg = f"Unknown API source: {source_name}"
                return None, False, error_msg

        except requests.exceptions.Timeout:
            error_msg = f"Timeout fetching data from {source_name}: Read timed out."
            log_message(f"Attempt {attempt + 1} - {error_msg}", source_api=source_name)
            if attempt == RETRY_COUNT: # Send email only after all retries fail
                email_subject = f"CRITICAL: Plane Tracker - {source_name.upper()} API Timeout!"
                email_body = f"The plane tracker script failed to fetch data from {source_name} due to a timeout after {RETRY_COUNT + 1} attempts.\n\nError: {error_msg}\n\nPlease check the API service and your network connection."
                send_email(email_subject, email_body, RECIPIENT_EMAIL)
                log_message(f"Email sent about {source_name} API timeout.", source_api="email")
        except requests.exceptions.HTTPError as e:
            error_msg = f"HTTP Error fetching data from {source_name}: {e}"
            log_message(f"Attempt {attempt + 1} - {error_msg}", source_api=source_name)
            if e.response.status_code == 429:
                if attempt == RETRY_COUNT: # Send email only after all retries fail for 429
                    email_subject = f"CRITICAL: Plane Tracker - {source_name.upper()} API Rate Limit Exceeded!"
                    email_body = f"The plane tracker script was rate-limited by {source_name} API after {RETRY_COUNT + 1} attempts.\n\nError: {error_msg}\n\nPlease check your usage and the API's rate limit policies."
                    send_email(email_subject, email_body, RECIPIENT_EMAIL)
                    log_message(f"Email sent about {source_name} API rate limit.", source_api="email")
            elif attempt == RETRY_COUNT: # For other HTTP errors, send email after all retries fail
                email_subject = f"CRITICAL: Plane Tracker - {source_name.upper()} HTTP Error!"
                email_body = f"The plane tracker script encountered an HTTP error from {source_name} API after {RETRY_COUNT + 1} attempts.\n\nError: {error_msg}\n\nPlease check the API service."
                send_email(email_subject, email_body, RECIPIENT_EMAIL)
                log_message(f"Email sent about {source_name} HTTP error.", source_api="email")
        except requests.RequestException as e:
            error_msg = f"General Request Error fetching data from {source_name}: {e}"
            log_message(f"Attempt {attempt + 1} - {error_msg}", source_api=source_name)
            if attempt == RETRY_COUNT: # Send email only after all retries fail
                email_subject = f"CRITICAL: Plane Tracker - {source_name.upper()} General Request Error!"
                email_body = f"The plane tracker script encountered a general request error from {source_name} API after {RETRY_COUNT + 1} attempts.\n\nError: {error_msg}\n\nPlease check your network connection and the API service."
                send_email(email_subject, email_body, RECIPIENT_EMAIL)
                log_message(f"Email sent about {source_name} general request error.", source_api="email")
        except Exception as e:
            error_msg = f"Unexpected error fetching data from {source_name}: {e}"
            log_message(f"Attempt {attempt + 1} - {error_msg}", source_api=source_name)
            if attempt == RETRY_COUNT: # Send email only after all retries fail
                email_subject = f"CRITICAL: Plane Tracker - {source_name.upper()} Unexpected Error!"
                email_body = f"The plane tracker script encountered an unexpected error while fetching data from {source_name} after {RETRY_COUNT + 1} attempts.\n\nError: {error_msg}\n\nPlease investigate the script or API."
                send_email(email_subject, email_body, RECIPIENT_EMAIL)
                log_message(f"Email sent about {source_name} unexpected error.", source_api="email")

        if attempt < RETRY_COUNT:
            sleep(RETRY_DELAY)

    final_failure_msg = f"Failed to fetch data from {source_name} after {RETRY_COUNT + 1} attempts."
    log_message(final_failure_msg, source_api=source_name)
    return None, False, error_msg

def get_plane_data(icao_hex: str, spoof_data: dict = None) -> dict:
    """
    Fetches plane data, attempting ADSB.lol first, then failing over to ADSB.fi if needed.
    """
    if spoof_data is not None:
        log_message(f"Using SPOOFED DATA: {spoof_data}")
        return spoof_data

    plane_data = None
    source_used = None

    log_message(f"Attempting to fetch data from ADSB.lol (primary).", source_api="adsb.lol")
    plane_data_lol, lol_api_successful, lol_error_msg = _fetch_data_from_api(ADSB_LOL_API_URL, "adsb.lol", icao_hex)

    if plane_data_lol:
        plane_data = plane_data_lol
        source_used = "adsb.lol"
        log_message(f"Data successfully retrieved from {source_used}.", source_api=source_used)
    elif not lol_api_successful:
        log_message(f"ADSB.lol API call failed. Attempting to fetch data from ADSB.fi (failover).", source_api="adsb.fi")
        plane_data_fi, fi_api_successful, fi_error_msg = _fetch_data_from_api(ADSB_FI_API_URL, "adsb.fi", icao_hex)

        if plane_data_fi:
            plane_data = plane_data_fi
            source_used = "adsb.fi"
            log_message(f"Data successfully retrieved from {source_used} after ADSB.lol failure.", source_api=source_used)
        else:
            source_used = "none"
            if not fi_api_successful:
                log_message(f"ADSB.fi also failed. No plane data could be retrieved from any source.", source_api="none")
            else:
                log_message(f"ADSB.fi responded successfully but found no aircraft data. No plane data could be retrieved from any source.", source_api="adsb.fi (no data)")

            # The dual API failure email is still handled here, outside the retry logic of _fetch_data_from_api
            # This is correct as it's a higher-level failure condition
            if not fi_api_successful: 
                email_subject = f"CRITICAL: Plane Tracker - Both APIs Failed!"
                email_body = (
                    f"Your plane tracker script failed to retrieve data from both ADSB.lol and ADSB.fi.\n\n"
                    f"ADSB.lol error: {lol_error_msg if lol_error_msg else 'No specific error message recorded.'}\n"
                    f"ADSB.fi error: {fi_error_msg if fi_error_msg else 'No specific error message recorded.'}\n\n"
                    f"Please check the log file ({LOG_FILE}) for more details and investigate the API services."
                )
                send_email(email_subject, email_body, RECIPIENT_EMAIL)
                log_message(f"Email sent about dual API failure.", source_api="email")
    else:
        log_message(f"ADSB.lol responded successfully but found no aircraft data. Not failing over.", source_api="adsb.lol (no data)")

    return plane_data

# --- Geolocation Helper ---
def get_location_name(latitude: float, longitude: float) -> str:
    """
    Reverse geocodes coordinates to a human-readable location name.
    """
    if latitude is None or longitude is None:
        return "an unknown location"

    geolocator = Nominatim(user_agent="PlaneTrackerApp/1.0 (your-email@example.com)") # Placeholder for user agent email

    try:
        location = geolocator.reverse(f"{latitude}, {longitude}", timeout=10)
        if location:
            address = location.raw.get('address', {})

            city = address.get('city') or address.get('town') or address.get('village')
            state = address.get('state')
            county = address.get('county')
            state_district = address.get('state_district')
            country = address.get('country')

            parts = []
            if city:
                parts.append(city)

            if state:
                parts.append(state)
            elif state_district:
                parts.append(state_district)
            elif county:
                parts.append(county)

            if country and (not parts or country not in parts[-1]):
                parts.append(country)

            if parts:
                return ", ".join(parts)
            else:
                return location.address
        return "an unknown location"
    except (GeocoderTimedOut, GeocoderServiceError) as e:
        error_msg = f"Geocoding error for {latitude}, {longitude}: {e}"
        log_message(error_msg)
        email_subject = "CRITICAL: Plane Tracker - Geocoding Service Error!"
        email_body = f"The plane tracker script encountered an error with the geocoding service (Nominatim).\n\nError: {error_msg}\n\nPlease check the service status or your internet connection."
        send_email(email_subject, email_body, RECIPIENT_EMAIL)
        log_message(f"Email sent about geocoding error.", source_api="email")
        return "an unknown location (geocoding error)"
    except Exception as e:
        error_msg = f"Unexpected geocoding error: {e}"
        log_message(error_msg)
        email_subject = "CRITICAL: Plane Tracker - Unexpected Geocoding Error!"
        email_body = f"The plane tracker script encountered an unexpected error while geocoding.\n\nError: {error_msg}\n\nPlease investigate the script or service."
        send_email(email_subject, email_body, RECIPIENT_EMAIL)
        log_message(f"Email sent about unexpected geocoding error.", source_api="email")
        return "an unknown location (unexpected geocoding error)"

# --- State Handling ---
def get_current_state() -> tuple:
    """
    Reads the last saved plane state from the state file. All timestamps are stored as UTC.
    Returns: (state, last_lat, last_lon, last_change_time, last_takeoff_location_name, last_idle_notification_time)
    """
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            lines = f.readlines()
            # New format expects 6 lines for last_idle_notification_time
            if len(lines) >= 6: 
                state = lines[0].strip()
                try:
                    last_lat = float(lines[1].strip()) if lines[1].strip() else None
                    last_lon = float(lines[2].strip()) if lines[2].strip() else None
                    last_change_time = float(lines[3].strip()) if lines[3].strip() else 0
                    last_takeoff_location_name = lines[4].strip() if lines[4].strip() else None
                    # Read last_idle_notification_time, default to 0 if not present or unparseable
                    last_idle_notification_time = float(lines[5].strip()) if lines[5].strip() else 0 
                except ValueError:
                    log_message("Error parsing state file. Resetting to defaults.")
                    return "landed", None, None, 0, None, 0
                return state, last_lat, last_lon, last_change_time, last_takeoff_location_name, last_idle_notification_time
            # Older format with 5 lines for takeoff_location_name
            elif len(lines) >= 5: 
                state = lines[0].strip()
                try:
                    last_lat = float(lines[1].strip()) if lines[1].strip() else None
                    last_lon = float(lines[2].strip()) if lines[2].strip() else None
                    last_change_time = float(lines[3].strip()) if lines[3].strip() else 0
                    last_takeoff_location_name = lines[4].strip() if lines[4].strip() else None
                except ValueError:
                    log_message("Error parsing older state file format. Resetting to defaults.")
                    return "landed", None, None, 0, None, 0
                # Default last_idle_notification_time to 0 for older formats
                return state, last_lat, last_lon, last_change_time, last_takeoff_location_name, 0 
            else: # Even older format or corrupted, reset all
                log_message("Older/incomplete state file format. Resetting to defaults.")
                return "landed", None, None, 0, None, 0
    return "landed", None, None, 0, None, 0 # Default if file doesn't exist

def set_current_state(state: str, latitude: float = None, longitude: float = None, timestamp: float = None, takeoff_location_name: str = None, last_idle_notification_time: float = 0):
    """
    Writes the current plane state to the state file. All timestamps are stored as UTC.
    """
    if timestamp is None:
        timestamp = datetime.datetime.now(pytz.UTC).timestamp()

    with open(STATE_FILE, "w") as f:
        f.write(f"{state}\n")
        f.write(f"{latitude if latitude is not None else ''}\n")
        f.write(f"{longitude if longitude is not None else ''}\n")
        f.write(f"{timestamp}\n")
        f.write(f"{takeoff_location_name if takeoff_location_name is not None else ''}\n")
        f.write(f"{last_idle_notification_time}\n") # Store the timestamp
    log_message(f"State updated to: {state} (Lat: {latitude}, Lon: {longitude}, Takeoff Loc: {takeoff_location_name}, Last Idle Notif Time: {datetime.datetime.fromtimestamp(last_idle_notification_time, tz=pytz.UTC).strftime('%Y-%m-%d %H:%M:%S UTC') if last_idle_notification_time else 'N/A'})")

# --- Aircraft Info Helper ---
def get_aircraft_display_name(plane_data: dict) -> str:
    """
    Extracts aircraft registration/tail number and creates a display name.
    """
    # Use PLANE_CODE from environment variable, not a hardcoded default
    icao = PLANE_CODE.upper() if PLANE_CODE else "UNKNOWN_ICAO" 
    registration = plane_data.get("r") or plane_data.get("reg") or plane_data.get("registration")
    if registration:
        return f"The aircraft you are tracking ({registration}, ICAO: {icao})"
    else:
        return f"The aircraft you are tracking (ICAO: {icao})"

def get_Maps_link(latitude: float, longitude: float) -> str:
    """
    Generates a Google Maps link for the given coordinates.
    """
    if latitude is None or longitude is None:
        return "N/A"
    return f"https://www.google.com/maps/search/?api=1&query={latitude},{longitude}"

# --- Email Notification ---
def send_email(subject: str, body: str, recipient_email: str):
    """
    Sends an email using the 'mail' command-line utility.
    """
    if not recipient_email:
        log_message("RECIPIENT_EMAIL not set. Skipping email notification.")
        return

    try:
        command = ['mail', '-s', subject, recipient_email]

        process = subprocess.run(command, input=body.encode('utf-8'), capture_output=True, check=True)
        log_message(f"Email sent to {recipient_email} with subject '{subject}'.")
        log_message(f"Mail command stdout: {process.stdout.decode('utf-8').strip()}")
        log_message(f"Mail command stderr: {process.stderr.decode('utf-8').strip()}")
    except subprocess.CalledProcessError as e:
        log_message(f"Error sending email: Command '{' '.join(e.cmd)}' failed with exit code {e.returncode}")
        log_message(f"Stderr: {e.stderr.decode('utf-8').strip()}")
    except FileNotFoundError:
        log_message(f"Error: 'mail' command not found. Please ensure 'mailutils' or a similar package is installed.")
    except Exception as e:
        log_message(f"An unexpected error occurred while sending email: {e}")

# --- BlueSky Posting ---
def post_to_bluesky(message: str, test_mode: bool = False):
    """
    Posts a message to Bluesky.
    """
    if not BLUESKY_HANDLE or not BLUESKY_APP_PASSWORD:
        log_message("Bluesky credentials not set. Skipping post.")
        return

    if test_mode:
        message = f"[TEST] {message}"
        log_message(f"Bluesky Test Post (will attempt): {message}")
    else:
        log_message(f"Bluesky Live Post (will will attempt): {message}")

    try:
        session = BskySession(BLUESKY_HANDLE, BLUESKY_APP_PASSWORD)
        response = post_text(session, message)
        log_message(f"Posted to Bluesky: {response}")
    except Exception as e:
        log_message(f"Error posting to Bluesky: {e}")

# --- Flight Metrics Calculation ---
def calculate_flight_metrics(lat1: float, lon1: float, lat2: float, lon2: float) -> dict:
    """
    Calculates distance, estimated fuel used, CO2 produced, and equivalent car miles
    for a flight.
    """
    if any(coord is None for coord in [lat1, lon1, lat2, lon2]):
        log_message("Invalid coordinates provided for flight metrics calculation. Returning zeros.")
        return {
            'distance_nm': 0.0,
            'fuel_gallons': 0.0,
            'co2_tons': 0.0,
            'equivalent_car_miles': 0.0
        }

    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2)
    lon2_rad = math.radians(lon2)

    dlon = lon2_rad - lon1_rad
    dlat = lat2_rad - lat1_rad

    a = math.sin(dlat / 2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    distance_nm = EARTH_RADIUS_NM * c

    fuel_gallons = distance_nm * DEFAULT_FUEL_BURN_GAL_PER_NM # Using configurable fuel burn rate
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


# --- Main Logic ---
def main(spoof_data: dict = None, test_mode: bool = False):
    """
    Main function to fetch plane data, determine state, and post updates.
    """
    log_message("--- Main script execution started ---")

    # Retrieve all state variables, including the new takeoff_location_name and last_idle_notification_time
    current_state, last_lat, last_lon, last_change_time, last_takeoff_location, last_idle_notification_time = get_current_state()

    current_utc_dt = datetime.datetime.now(pytz.UTC)
    current_time_timestamp = current_utc_dt.timestamp()

    time_since_last_change = current_time_timestamp - last_change_time

    log_message(f"State file says: {current_state} (Last Lat: {last_lat}, Last Lon: {last_lon}, Last Change Time: {datetime.datetime.fromtimestamp(last_change_time, tz=pytz.UTC).strftime('%Y-%m-%d %H:%M:%S UTC') if last_change_time else 'N/A'}, Last Takeoff Loc: {last_takeoff_location}, Last Idle Notif Time: {datetime.datetime.fromtimestamp(last_idle_notification_time, tz=pytz.UTC).strftime('%Y-%m-%d %H:%M:%S UTC') if last_idle_notification_time else 'N/A'})")
    log_message(f"Time since last state change: {time_since_last_change:.1f} seconds")

    plane_data = get_plane_data(PLANE_CODE, spoof_data=spoof_data)

    aircraft_name = get_aircraft_display_name(plane_data or {})

    # Case 1: Plane data is NOT available
    if not plane_data:
        log_message("No plane data received from APIs.")
        if current_state == "flying":
            log_message("Plane was previously flying, but now no data received. Assuming landing.")

            if time_since_last_change < MIN_STATE_CHANGE_TIME and not test_mode:
                log_message(f"Minimum time threshold not met ({MIN_STATE_CHANGE_TIME}s). Skipping assumed landing notification.")
                return

            # --- Calculate metrics for assumed landing (distance will be 0) ---
            # We use last_lat/lon for both points as we assume it landed where it was last seen.
            flight_metrics = calculate_flight_metrics(last_lat, last_lon, last_lat, last_lon)
            distance_str = f"Distance: {flight_metrics['distance_nm']} nm" if flight_metrics['distance_nm'] > 0 else ""
            fuel_str = f"Fuel: {flight_metrics['fuel_gallons']:.2f} gal" if flight_metrics['fuel_gallons'] > 0 else ""
            co2_str = f"CO2: {flight_metrics['co2_tons']:.2f} tons" if flight_metrics['co2_tons'] > 0 else ""
            car_equiv_str = f"Car Equiv: {flight_metrics['equivalent_car_miles']} mi" if flight_metrics['equivalent_car_miles'] > 0 else ""

            metrics_display_short = ""
            metrics_display_long = ""
            if distance_str:
                metrics_display_short += distance_str
                metrics_display_long += distance_str
            if fuel_str:
                if metrics_display_short: metrics_display_short += ", "
                metrics_display_short += fuel_str
                if metrics_display_long: metrics_display_long += ", "
                metrics_display_long += fuel_str
            if co2_str:
                if metrics_display_short: metrics_display_short += ", "
                metrics_display_short += co2_str
                if metrics_display_long: metrics_display_long += ", "
                metrics_display_long += co2_str
            if car_equiv_str:
                if metrics_display_short: metrics_display_short += ", "
                metrics_display_short += car_equiv_str
                if metrics_display_long: metrics_display_long += ", "
                metrics_display_long += car_equiv_str

            if metrics_display_short:
                metrics_display_short = f"\n{metrics_display_short}"

            landing_location = get_location_name(last_lat, last_lon) 
            # Ensure coordinates are not None before formatting
            landing_coordinates = f"{last_lat:.4f}, {last_lon:.4f}" if last_lat is not None and last_lon is not None else "N/A"
            landing_maps_link = get_Maps_link(last_lat, last_lon)

            landing_utc_dt = current_utc_dt
            landing_utc_str, landing_local_str = format_full_time_for_location(landing_utc_dt, last_lat, last_lon)

            msg = (
                f"The {aircraft_name} landed (last seen near {landing_location}) at {landing_local_str} "
                f"(UTC: {landing_utc_str.split('UTC')[0].strip()}). 🛬"
                f"{metrics_display_short}\n"
                f"GPS: {landing_coordinates}\n"
                f"Track: https://globe.adsb.fi/?icao={PLANE_CODE}"
            )
            post_to_bluesky(msg, test_mode=test_mode)

            if flight_metrics['distance_nm'] > 0:
                sleep(2)
                last_takeoff_location_for_post = last_takeoff_location if last_takeoff_location else "previous point"
                msg_metrics = (
                    f"(Conservative estimate) From {last_takeoff_location_for_post} to {landing_location}: "
                    f"Flight length: {flight_metrics['distance_nm']}nm. "
                    f"Fuel used: {flight_metrics['fuel_gallons']:.2f}gal. "
                    f"CO2 Emitted: {flight_metrics['co2_tons']:.2f}ton. "
                    f"EPA Estimated Car Equivalent: {flight_metrics['equivalent_car_miles']}mi."
                )
                log_message(f"Attempting to send second Bluesky post with metrics: '{msg_metrics}'")
                post_to_bluesky(msg_metrics, test_mode=test_mode)


            email_subject = f"Plane Status Change: {aircraft_name} - LANDED (Data Lost)!"
            email_body = (
                f"The {aircraft_name} has just landed.\n\n"
                f"It was last seen flying and is now not providing data. This suggests a landing.\n"
                f"Last known location: {landing_location}\n"
                f"GPS Coordinates: {landing_coordinates}\n"
                f"Google Maps: {landing_maps_link}\n"
                f"Landing Time (Local TZ): {landing_local_str}\n"
                f"Landing Time (UTC): {landing_utc_str}\n\n"
                f"{metrics_display_long.replace(', ', '\n')}\n\n"
                f"View last known position: https://globe.adsb.fi/?icao={PLANE_CODE}\n\n"
                f"This notification was sent by your plane tracker script."
            )
            send_email(email_subject, email_body, RECIPIENT_EMAIL)

            # After an assumed landing, set last_idle_notification_time to 0 to allow new idle notifications
            set_current_state("landed", last_lat, last_lon, current_time_timestamp, takeoff_location_name=None, last_idle_notification_time=0)
            log_message(f"Assumed plane landed at (last seen): {landing_location} at Local: {landing_local_str} (UTC: {landing_utc_str})")

        elif current_state == "landed":
            # Calculate time since last idle notification
            time_since_last_idle_notification = current_time_timestamp - last_idle_notification_time
            
            # If no previous idle notification, or enough time has passed since the last one
            if last_idle_notification_time == 0 or time_since_last_idle_notification >= IDLE_NOTIFICATION_THRESHOLD_SECONDS:
                log_message(f"Plane has been idle for over {IDLE_NOTIFICATION_THRESHOLD_HOURS} hours since last notification or no notification sent yet. Sending notification.")

                idle_location_name = get_location_name(last_lat, last_lon)
                # Ensure coordinates are not None before formatting
                idle_coordinates = f"{last_lat:.4f}, {last_lon:.4f}" if last_lat is not None and last_lon is not None else "N/A"

                current_utc_str, current_local_str = format_full_time_for_location(current_utc_dt, last_lat, last_lon)

                msg_idle = (
                    f"The {aircraft_name} (ICAO: {PLANE_CODE}) still appears to be on the ground "
                    f"at {idle_location_name} (GPS: {idle_coordinates}).\n"
                    f"Time checked: {current_local_str} (UTC: {current_utc_str.split('UTC')[0].strip()}).\n"
                    f"Track: https://globe.adsb.fi/?icao={PLANE_CODE}"
                )
                post_to_bluesky(msg_idle, test_mode=test_mode)

                # Update the last_idle_notification_time to the current timestamp
                set_current_state("landed", last_lat, last_lon, last_change_time, takeoff_location_name=last_takeoff_location, last_idle_notification_time=current_time_timestamp)
            else:
                log_message("No plane data, and plane was already landed, but not yet time for another idle notification.")
        return

    # If we reach here, plane_data IS available
    alt = plane_data.get("alt_baro")
    gnd = plane_data.get("gnd")
    gs = plane_data.get("gs")
    lat = plane_data.get("lat")
    lon = plane_data.get("lon")

    is_flying = False
    if (alt is not None and isinstance(alt, (int, float)) and alt > ALTITUDE_THRESHOLD and (gnd is False or gnd is None)) or \
       (gs is not None and isinstance(gs, (int, float)) and gs > GROUND_SPEED_THRESHOLD and (gnd is False or gnd is None)):
        is_flying = True

    log_message(f"API data: alt_baro={alt}, gnd={gnd}, gs={gs}, lat={lat}, lon={lon}")
    log_message(f"Detected is_flying={is_flying}")

    if time_since_last_change < MIN_STATE_CHANGE_TIME and not test_mode:
        log_message(f"Minimum time threshold not met ({MIN_STATE_CHANGE_TIME}s). Skipping state change.")
        # Preserve takeoff_location and last_idle_notification_time status
        set_current_state(current_state, lat, lon, last_change_time, takeoff_location_name=last_takeoff_location, last_idle_notification_time=last_idle_notification_time)
        return

    # Logic for state transitions (when data IS available)
    if is_flying and current_state == "landed":
        # Plane just took off - reset last_idle_notification_time
        takeoff_location = get_location_name(last_lat, last_lon) 
        # Ensure coordinates are not None before formatting
        takeoff_coordinates = f"{last_lat:.4f}, {last_lon:.4f}" if last_lat is not None and last_lon is not None else "N/A"
        takeoff_maps_link = get_Maps_link(last_lat, last_lon)

        takeoff_utc_dt = current_utc_dt
        takeoff_utc_str, takeoff_local_str = format_full_time_for_location(takeoff_utc_dt, last_lat, last_lon)

        msg = (
            f"The {aircraft_name} took off from {takeoff_location} at {takeoff_local_str} "
            f"(UTC: {takeoff_utc_str.split('UTC')[0].strip()}). 🚀\n"
            f"GPS: {takeoff_coordinates}\n"
            f"Track: https://globe.adsb.fi/?icao={PLANE_CODE}"
        )
        post_to_bluesky(msg, test_mode=test_mode)

        email_subject = f"Plane Status Change: {aircraft_name} - TAKEN OFF!"
        email_body = (
            f"The {aircraft_name} has just taken off from {takeoff_location}.\n\n"
            f"GPS Coordinates: {takeoff_coordinates}\n"
            f"Google Maps: {takeoff_maps_link}\n"
            f"Takeoff Time (Local TZ): {takeoff_local_str}\n"
            f"Takeoff Time (UTC): {takeoff_utc_str}\n\n"
            f"Current altitude: {alt} feet\n"
            f"Current ground speed: {gs} knots\n"
            f"Track it live: https://globe.adsb.fi/?icao={PLANE_CODE}\n\n"
            f"This notification was sent by your plane tracker script."
        )
        send_email(email_subject, email_body, RECIPIENT_EMAIL)

        # Update state to flying and save the takeoff location, reset last_idle_notification_time
        set_current_state("flying", lat, lon, current_time_timestamp, takeoff_location_name=takeoff_location, last_idle_notification_time=0)
        log_message(f"Plane took off from: {takeoff_location} at Local: {takeoff_local_str} (UTC: {takeoff_utc_str})")

    elif not is_flying and current_state == "flying":
        # Plane just landed - reset last_idle_notification_time for the new landed period
        landing_location = get_location_name(lat, lon) 
        # Ensure coordinates are not None before formatting
        landing_coordinates = f"{lat:.4f}, {lon:.4f}" if lat is not None and lon is not None else "N/A"
        landing_maps_link = get_Maps_link(lat, lon)

        landing_utc_dt = current_utc_dt
        landing_utc_str, landing_local_str = format_full_time_for_location(landing_utc_dt, lat, lon)

        # --- Calculate flight metrics for actual landing ---
        flight_metrics = calculate_flight_metrics(last_lat, last_lon, lat, lon)
        distance_str = f"Distance: {flight_metrics['distance_nm']} nm" if flight_metrics['distance_nm'] > 0 else ""
        fuel_str = f"Fuel: {flight_metrics['fuel_gallons']:.2f} gal" if flight_metrics['fuel_gallons'] > 0 else ""
        co2_str = f"CO2: {flight_metrics['co2_tons']:.2f} tons" if flight_metrics['co2_tons'] > 0 else ""
        car_equiv_str = f"Car Equiv: {flight_metrics['equivalent_car_miles']} mi" if flight_metrics['equivalent_car_miles'] > 0 else ""

        metrics_display_short = ""
        metrics_display_long = ""
        if distance_str:
            metrics_display_short += distance_str
            metrics_display_long += distance_str
        if fuel_str:
            if metrics_display_short: metrics_display_short += ", "
            metrics_display_short += fuel_str
            if metrics_display_long: metrics_display_long += ", "
            metrics_display_long += fuel_str
        if co2_str:
            if metrics_display_short: metrics_display_short += ", "
            metrics_display_short += co2_str
            if metrics_display_long: metrics_display_long += ", "
            metrics_display_long += co2_str
        if car_equiv_str:
            if metrics_display_short: metrics_display_short += ", "
            metrics_display_short += car_equiv_str
            if metrics_display_long: metrics_display_long += ", "
            metrics_display_long += car_equiv_str

        if metrics_display_short:
            metrics_display_short = f"\n{metrics_display_short}"


        # First Bluesky post (main landing notification)
        msg_main = (
            f"The {aircraft_name} landed at {landing_location} at {landing_local_str} "
            f"(UTC: {landing_utc_str.split('UTC')[0].strip()}). 🛬"
            f"{metrics_display_short}\n"
            f"GPS: {landing_coordinates}\n"
            f"Track: https://globe.adsb.fi/?icao={PLANE_CODE}"
        )
        post_to_bluesky(msg_main, test_mode=test_mode)

        # Second Bluesky post (concise flight metrics, only if distance > 0)
        if flight_metrics['distance_nm'] > 0:
            sleep(2)
            last_takeoff_location_for_post = last_takeoff_location if last_takeoff_location else "previous point"
            msg_metrics = (
                f"(Conservative estimate) From {last_takeoff_location_for_post} to {landing_location}: "
                f"Flight length: {flight_metrics['distance_nm']}nm. "
                f"Fuel used: {flight_metrics['fuel_gallons']:.2f}gal. "
                f"CO2 Emitted: {flight_metrics['co2_tons']:.2f}ton. "
                f"EPA Estimated Car Equivalent: {flight_metrics['equivalent_car_miles']}mi."
            )
            log_message(f"Attempting to send second Bluesky post with metrics: '{msg_metrics}'")
            post_to_bluesky(msg_metrics, test_mode=test_mode)


        email_subject = f"Plane Status Change: {aircraft_name} - LANDED!"
        email_body = (
            f"The {aircraft_name} has just landed at {landing_location}.\n\n"
            f"GPS Coordinates: {landing_coordinates}\n"
            f"Google Maps: {landing_maps_link}\n"
            f"Landing Time (Local TZ): {landing_local_str}\n"
            f"Landing Time (UTC): {landing_utc_str}\n\n"
            f"Current altitude: {alt} feet\n"
            f"Current ground speed: {gs} knots\n"
            f"{metrics_display_long.replace(', ', '\n')}\n\n"
            f"View last known position: https://globe.adsb.fi/?icao={PLANE_CODE}\n\n"
            f"This notification was sent by your plane tracker script."
        )
        send_email(email_subject, email_body, RECIPIENT_EMAIL)

        # After an actual landing, reset last_idle_notification_time for the new landed state
        set_current_state("landed", lat, lon, current_time_timestamp, takeoff_location_name=None, last_idle_notification_time=0)
        log_message(f"Plane landed at: {landing_location} at Local: {landing_local_str} (UTC: {landing_utc_str})")

    else:
        log_message("No change in plane status.")
        # If no state change, preserve the last_change_time and last_idle_notification_time
        # We update lat/lon here to always have the latest known position if available
        if lat is not None and lon is not None:
            set_current_state(current_state, lat, lon, last_change_time, takeoff_location_name=last_takeoff_location, last_idle_notification_time=last_idle_notification_time)

    log_message("--- Main script execution finished ")

# --- Entrypoint ---
if __name__ == "__main__":
    main()
