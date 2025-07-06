# Generic Plane Tracker for Bluesky

This Python script monitors the flight status of a specific aircraft using ADS-B data and posts updates to Bluesky (and sends email notifications). It's designed to track a plane's takeoffs, landings, and provide periodic "still on the ground" updates when idle for an extended period.

## Features

* **Aircraft Monitoring:** Tracks a specific aircraft by its ICAO hex code.
* **Dual API Support:** Fetches data from ADSB.lol (primary) and falls back to ADSB.fi (secondary) if the primary fails.
* **Takeoff Notifications:** Posts to Bluesky and sends an email when the aircraft takes off.
* **Landing Notifications:** Posts to Bluesky (in two parts to respect character limits) and sends an email when the aircraft lands, including estimated flight metrics (distance, fuel, CO2, car equivalent).
* **Idle Notifications:** Posts to Bluesky every X hours (configurable) if the aircraft remains on the ground.
* **Persistent State:** Saves the aircraft's last known state (flying/landed, location, timestamps) to a local file to maintain continuity between runs.
* **Dynamic Timezones:** All posts and logs use the aircraft's actual local timezone, determined by its coordinates.
* **Rotating Logs:** Maintains a log file with a configurable retention period.
* **Critical Error Emails:** Sends email notifications for API timeouts, rate limits, HTTP errors, and geocoding service issues, ensuring you're alerted to problems.

## Setup Instructions

Follow these steps to get the Plane Tracker running on your system.

### 1. Prerequisites

* **Python 3:** Ensure you have Python 3 installed. You can download it from [python.org](https://www.python.org/downloads/).
* **`mail` command-line utility:** The script uses the `mail` command for sending email notifications. On most Linux distributions, this is part of the `mailutils` or `bsd-mailx` package.
    * **Debian/Ubuntu:** `sudo apt update && sudo apt install mailutils`
    * **RHEL/CentOS/Fedora:** `sudo yum install mailx` or `sudo dnf install mailx`
    * **Important:** You might need to configure `mailutils` (or your chosen mail transfer agent like Postfix) to send emails via an external SMTP server. This setup is specific to your system and is essential for email notifications to work.

### 2. Download the Script

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/upmcplanetracker/generic-plane-tracker.git
    cd generic-plane-tracker
    ```
2.  Alternatively, you can just download the `track_plane.py` file directly.

### 3. Install Python Dependencies

It's highly recommended to use a Python virtual environment to manage dependencies.

1.  **Create a virtual environment:**
    ```bash
    python3 -m venv venv
    ```
2.  **Activate the virtual environment:**
    * **Linux/macOS:**
        ```bash
        source venv/bin/activate
        ```
    * **Windows (PowerShell):**
        ```powershell
        .\venv\Scripts\Activate.ps1
        ```
3.  **Install the required Python packages:**
    ```bash
    pip install requests python-dotenv geopy timezonefinder pytz bsky-bridge
    ```

### 4. Create and Configure the `.env` File

This is the **only file you need to edit** for configuration. Create a file named `.env` in the same directory as `track_plane.py`.

Use a text editor like `nano` or `vim`:
```bash
nano .env
```

Copy the entire template below into the `.env` file and replace the placeholder values with your actual information.

```env
# ----------------------------------------------------
# CREDENTIALS & IDENTIFIERS
# ----------------------------------------------------

# REQUIRED: The ICAO 24-bit hex code of the plane to track (e.g., "ac0f4a").
PLANE_CODE="INSERT_YOUR_PLANE_ICAO_HEX_HERE"

# REQUIRED (if posting to Bluesky): Your full Bluesky handle (e.g., example.bsky.social).
BLUESKY_HANDLE="your_bluesky_handle.bsky.social"

# REQUIRED (if posting to Bluesky): Your Bluesky App Password.
# Note: Generate one from your Bluesky settings for security. Do NOT use your main password.
BLUESKY_APP_PASSWORD="YOUR_BLUESKY_APP_PASSWORD"

# REQUIRED (if sending emails): The email address to receive notifications.
RECIPIENT_EMAIL="your_notification_email@example.com"

# REQUIRED: A contact email for the Nominatim Geocoding service user agent.
# This is for their fair use policy to identify your script's usage.
GEOLOCATOR_EMAIL="your_contact_email@example.com"


# ----------------------------------------------------
# SCRIPT BEHAVIOR & THRESHOLDS (Optional)
# ----------------------------------------------------
# The defaults are sensible, but you can override them here.

# Altitude (in feet) above which the plane is considered "flying"
ALTITUDE_THRESHOLD=500

# Ground speed (in knots) above which the plane is considered "flying"
GROUND_SPEED_THRESHOLD=50

# Minimum time (in seconds) between state changes (e.g., takeoff/landing) to prevent false positives.
MIN_STATE_CHANGE_TIME=300

# Time (in hours) the plane must be idle on the ground before sending a notification.
IDLE_NOTIFICATION_THRESHOLD_HOURS=12

# How long (in hours) to keep log entries in the plane_tracker.log file.
LOG_RETENTION_HOURS=6

# Number of times to retry a failed API call.
RETRY_COUNT=2

# Delay (in seconds) between API call retries.
RETRY_DELAY=2


# ----------------------------------------------------
# AIRCRAFT & FLIGHT METRICS (Optional)
# ----------------------------------------------------
# To find specific aircraft performance data, search online for your aircraft's
# "Fuel Burn per hour" (GPH) and "Cruise Speed" (Knots).
# The formula is (GPH / Knots) = Gallons Per Nautical Mile.

# Fuel burn efficiency in US Gallons per Nautical Mile.
# Example: For a Bombardier BD-700 burning 475 GPH at 488 Knots, this would be 475 / 488 = ~0.97
DEFAULT_FUEL_BURN_GAL_PER_NM=0.97

# The values below are standard conversion factors and generally do not need to be changed.
JET_FUEL_CO2_LBS_PER_GALLON=21.1
LBS_PER_METRIC_TON=2204.62
CO2_TONS_PER_AVG_CAR_MILE=0.0004
```

### 5. Run the Script

You can run the script manually for testing, but for continuous tracking, it's best to set it up as a cron job.

#### Manual Run (for testing)

Make sure your virtual environment is active, then run:
```bash
python3 track_plane.py
```

#### Running as a Cron Job (Linux/macOS)

For continuous monitoring, schedule the script to run periodically (e.g., every 3-5 minutes).

1.  Open your user's crontab for editing:
    ```bash
    crontab -e
    ```
2.  Add the following line to the file. **You must use absolute paths.**
    ```bash
    */3 * * * * /home/user/your_project_folder/venv/bin/python3 /home/user/your_project_folder/track_plane.py >> /home/user/your_project_folder/cron.log 2>&1
    ```
    * **Replace `/home/user/your_project_folder`** with the actual, full path to your project directory. You can find this by running `pwd` from inside the directory.
    * This command runs the script every 3 minutes.
    * The `>> ... 2>&1` part redirects all standard output and error messages to a `cron.log` file, which is extremely useful for debugging.

### 6. Important Notes

* **State File (`plane_state.txt`):** This file stores the script's internal state. Do not delete it unless you want to reset all tracking history.
* **Log File (`plane_tracker.log`):** This file records all script activity and automatically prunes old entries based on the `LOG_RETENTION_HOURS` setting.
* **Security:** Never commit your `.env` file to a public GitHub repository! Ensure your `.gitignore` file includes `.env` to prevent accidental commits.
