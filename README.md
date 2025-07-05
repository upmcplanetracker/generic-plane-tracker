# generic-plane-tracker
Generic Plane Tracker
# Generic Plane Tracker for Bluesky

This Python script monitors the flight status of a specific aircraft using ADS-B data and posts updates to Bluesky (and sends email notifications). It's designed to track a plane's takeoffs, landings, and provide periodic "still on the ground" updates when idle for an extended period.

## Features

* **Aircraft Monitoring:** Tracks a specific aircraft by its ICAO hex code.
* **Dual API Support:** Fetches data from ADSB.lol (primary) and falls back to ADSB.fi (secondary) if the primary fails.
* **Takeoff Notifications:** Posts to Bluesky and sends an email when the aircraft takes off.
* **Landing Notifications:** Posts to Bluesky and sends an email when the aircraft lands, including estimated flight metrics (distance, fuel, CO2, car equivalent).
* **Idle Notifications:** Posts to Bluesky every X hours (configurable) the aircraft remains on the ground at the same location. This timer is not reset by ground movements (like taxiing).
* **Persistent State:** Saves the aircraft's last known state (flying/landed, location, timestamps) to a local file to maintain continuity between runs.
* **Rotating Logs:** Maintains a log file with a configurable retention period.
* **Critical Error Emails:** Sends email notifications for API timeouts, rate limits, HTTP errors, and geocoding service issues, ensuring you're alerted to problems.

## Setup Instructions

Follow these steps to get the Plane Tracker running on your system.

### 1. Prerequisites

* **Python 3:** Ensure you have Python 3 installed. You can download it from [python.org](https://www.python.org/downloads/).
* **`mail` command-line utility:** The script uses the `mail` command for sending email notifications. On most Linux distributions, this is part of the `mailutils` or `bsd-mailx` package.
    * **Debian/Ubuntu:** `sudo apt update && sudo apt install mailutils`
    * **RHEL/CentOS/Fedora:** `sudo yum install mailx` or `sudo dnf install mailx`
    * **Important:** You might need to configure `mailutils` (or your chosen mail transfer agent like Postfix, Sendmail, or a simpler client like `ssmtp`) to send emails via an external SMTP server. This setup is specific to your system and email provider and is essential for email notifications to work.

### 2. Download the Script

1.  **Clone the repository:**
    ```bash
    git clone [https://github.com/upmcplanetracker/generic-plane-tracker.git](https://github.com/upmcplanetracker/generic-plane-tracker.git)
    cd generic-plane-tracker
    ```
    
    Alternatively, you can just download the `track_plane.py` file directly.

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
    * **Windows (Command Prompt):**
        ```cmd
        venv\Scripts\activate.bat
        ```
    * **Windows (PowerShell):**
        ```powershell
        .\venv\Scripts\Activate.ps1
        ```
3.  **Install the required Python packages:**
    ```bash
    pip install requests python-dotenv geopy timezonefinder pytz bsky-bridge
    ```

### 4. Create the `.env` File

Create a file named `.env` in the same directory as `track_plane.py`. This file will store your sensitive credentials and configurable parameters.

Replace the placeholder values with your actual information:
```bash
PLANE_CODE="INSERT_YOUR_PLANE_ICAO_HEX_HERE" # REQUIRED: The ICAO 24-bit hex code of the plane to track (e.g., "ac0f4a"). Find this on flight tracking sites (e.g., ADS-B Exchange, FlightAware, FlightRadar24).
BLUESKY_HANDLE="your_bluesky_handle.bsky.social" # REQUIRED (if posting to Bluesky): Your full Bluesky handle (e.g., example.bsky.social).
BLUESKY_APP_PASSWORD="YOUR_BLUESKY_APP_PASSWORD" # REQUIRED (if posting to Bluesky): Your Bluesky App Password. Generate one from your Bluesky settings for security.
RECIPIENT_EMAIL="your_notification_email@example.com" # REQUIRED (if sending emails): The email address to receive notifications from the script.
```
--- Optional: Flight Metrics Configuration ---
These values affect the estimated fuel burn and CO2 emissions.
If not set, conservative default estimates are used.
Find specific aircraft performance data on sites like:
- Business Jet Traveller (for general models)
- Manufacturer specifications (e.g., Gulfstream, Bombardier, Cessna)
- Aviation forums or databases
Look for values like "Fuel Burn per hour" (GPH) and "Cruise Speed" (Knots).
The formula is (GPH / Knots) = Gallons Per Nautical Mile.
Example: For an aircraft burning 512 GPH at 487 Knots cruise speed, DEFAULT_FUEL_BURN_GAL_PER_NM would be 512 / 487 = ~1.05
DEFAULT_FUEL_BURN_GAL_PER_NM="1.05"
JET_FUEL_CO2_LBS_PER_GALLON="21.1" # Pounds of CO2 per US gallon of Jet A/A-1 (standard value)
LBS_PER_METRIC_TON="2204.62" # Pounds per metric ton (standard value)
CO2_TONS_PER_AVG_CAR_MILE="0.0004" # Approx. tons of CO2 per average car mile (standard value, ~400 grams/km)

**Note:** The `bsky-bridge` library requires an **App Password**, not your regular Bluesky account password. Generate one in your Bluesky settings for security.

### 5. Configure the Script

Open `track_plane.py` in a text editor. You might want to adjust some of the constant parameters at the top of the file:

* `ALTITUDE_THRESHOLD`: Altitude in feet to consider the plane "flying".
* `GROUND_SPEED_THRESHOLD`: Ground speed in knots to consider the plane "flying".
* `MIN_STATE_CHANGE_TIME`: Minimum time (in seconds) that must pass between state change (takeoff/landing) notifications to prevent rapid, noisy updates from transient data.
* `IDLE_NOTIFICATION_THRESHOLD_HOURS`: How many hours the plane must be on the ground before an "idle" notification is sent, and then repeated.
* `LOG_RETENTION_HOURS`: How many hours of log entries to keep in `plane_tracker.log`.
* `RETRY_COUNT`, `RETRY_DELAY`: API retry settings for data fetching.
* `GEOLOCATOR_USER_AGENT`: **Important**: On line ~336, change `(your-email@example.com)` to your actual contact email for Nominatim's fair use policy. This identifies your usage of their service.

**For Flight Metrics (Fuel/CO2):**
The script uses `DEFAULT_FUEL_BURN_GAL_PER_NM` for calculations.
* This value is now loaded from your `.env` file if you set it, otherwise, it defaults to a conservative estimate (1.05).
* **To find a more accurate value for a specific aircraft:**
    * Search online for the aircraft's "fuel consumption per hour" (often in Gallons Per Hour, GPH) and its typical "cruise speed" (in Knots).
    * The formula to calculate **Gallons Per Nautical Mile (GAL/NM)** is: `(Fuel Burn in GPH) / (Cruise Speed in Knots)`.
    * For example, if an aircraft burns 300 GPH and cruises at 400 Knots, `DEFAULT_FUEL_BURN_GAL_PER_NM` would be `300 / 400 = 0.75`.
* Other constants like `JET_FUEL_CO2_LBS_PER_GALLON`, `LBS_PER_METRIC_TON`, and `CO2_TONS_PER_AVG_CAR_MILE` are standard environmental conversion factors and generally do not need to be changed.

### 6. Run the Script

You can run the script manually for testing, but for continuous tracking, it's best to set it up as a cron job (on Linux/macOS) or a scheduled task (on Windows).

#### Manual Run (for testing)

```bash
python3 track_plane.py
```
Running as a Cron Job (Linux/macOS)
For continuous monitoring, schedule the script to run periodically (e.g., every 5 minutes).

Open your crontab:

```bash

crontab -e
```
Add the following line (adjust paths to your script and Python interpreter):

Code snippet
```
*/5 * * * * /usr/bin/python3 /path/to/your/plane_tracker_repo/track_plane.py >> /path/to/your/plane_tracker_repo/plane_tracker_cron.log 2>&1
```
Replace /usr/bin/python3 with the correct path to your Python 3 interpreter (you can find it with which python3 or echo $VIRTUAL_ENV/bin/python3 if in an active virtual environment).

Replace /path/to/your/plane_tracker_repo/ with the actual absolute path to the directory where you cloned/downloaded the script.

The >> ... 2>&1 part redirects all standard output and error messages from the cron job to a separate plane_tracker_cron.log file, which is extremely useful for debugging any issues specific to cron execution.

7. Important Notes
State File (plane_state.txt): This file stores the script's internal state (last known location, timestamps, etc.). Do not delete it unless you want to completely reset all tracking history and force the script to start as if the plane just took off (or landed for the first time).

Log File (plane_tracker.log): This file records all script activity, including API interactions, state changes, and error messages. It automatically prunes old entries based on the LOG_RETENTION_HOURS setting.

Bluesky App Password Security: Treat your Bluesky App Password like any other sensitive credential. Never commit your .env file to a public GitHub repository! Ensure your .gitignore file (if you have one) includes .env to prevent accidental commits.

Mail Configuration: For email alerts to work, your system's mail command must be properly configured to send external emails. This often involves setting up a Mail Transfer Agent (MTA) like Postfix or configuring a simpler SMTP client (e.g., ssmtp). This is a system-level setup, not specific to this Python script.

Contributing
Feel free to open issues or submit pull requests if you have improvements or bug fixes!
