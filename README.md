canCorporate Jet Tracker for Blue SkyThis Python script tracks a fleet of aircraft using public ADS-B data and automatically posts takeoff, landing, and flight summary information to a Blue Sky social media account.Purpose of the ProjectThe goal of this project is to provide public transparency into the flight activities of corporate or private jets. By leveraging publicly available flight data, it automatically creates social media posts that detail the movements and environmental impact of these flights, fostering awareness and discussion.What the Script DoesMulti-Aircraft Tracking: Monitors any number of aircraft simultaneously based on their ICAO hex codes.Automatic Event Posting: Connects to the Blue Sky API to post updates when a plane takes off or lands.Flight Summaries: After a plane lands, it calculates and posts a summary including the estimated flight distance, fuel consumption, and CO₂ emissions.Custom Metrics: Uses per-aircraft fuel burn rates, configured by the user, for more accurate environmental impact estimates.Idle Alerts: Posts a notification if a plane has been on the ground at a single location for a configurable amount of time (e.g., 12 hours).Robust State Management: It maintains a plane_states.json file to remember the last known state (flying/landed, location, etc.) of each aircraft. This prevents duplicate posts and ensures that flight summaries are calculated correctly.API Failover: It uses a secondary flight data API if the primary one is unavailable, making the script more reliable.Detailed Logging: Keeps a running plane_tracker.log file of all actions, API calls, and errors for easy debugging.Setup Instructions1. PrerequisitesPython 3.xA Blue Sky account for the bot to post from.An email account on a system with the mail command (for receiving critical error notifications).2. InstallationClone this repository and navigate into the project directory. It is highly recommended to use a Python virtual environment to manage dependencies.# Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate

# Install the required Python packages from requirements.txt
pip install -r requirements.txt
You will need to create a requirements.txt file in your project directory with the following contents:requests
python-dotenv
geopy
timezonefinder
pytz
atprototools
3. Configuration (.env file)This script is configured using a .env file in the root of the project directory. You must create this file yourself; it is not included in the repository for security reasons.Important: Never commit your .env file to a public GitHub repository..env File TemplateCreate a file named .env and add the following content, replacing the placeholder values with your actual information.# ----------------------------------------------------
# AIRCRAFT FLEET CONFIGURATION
# Format: ICAO,"Owner",FuelBurn;ICAO,"Owner",FuelBurn;...
# ----------------------------------------------------
AIRCRAFT_FLEET='AC0F4A,"UPMC",0.97;A3C19C,"Dick''s Sporting Goods",0.67;A66A59,"PNC",0.74'

# ----------------------------------------------------
# CREDENTIALS & IDENTIFIERS
# ----------------------------------------------------
BLUESKY_HANDLE="your-handle.bsky.social"
BLUESKY_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx"
RECIPIENT_EMAIL="your-email@example.com"
GEOLOCATOR_EMAIL="your-personal-email@example.com"

# ----------------------------------------------------
# SCRIPT BEHAVIOR & THRESHOLDS (Optional)
# ----------------------------------------------------
ALTITUDE_THRESHOLD=500
GROUND_SPEED_THRESHOLD=50
MIN_STATE_CHANGE_TIME=300
IDLE_NOTIFICATION_THRESHOLD_HOURS=12
Configuration DetailsAIRCRAFT_FLEET: This is the most important variable. It's a single line containing all the aircraft to track, separated by semicolons (;).Format: ICAO_HEX,"Owner Name",Fuel_Burn_Gal_per_NMICAO Hex Code: The unique 6-character code for the aircraft. You can find this on sites like the FAA Registry by searching for the aircraft's tail number (N-Number).Owner Name: The name you want to display in posts. It must be in double quotes. If the name itself contains an apostrophe, use two apostrophes (e.g., "Dick''s Sporting Goods").Fuel Burn Rate: The estimated fuel consumption in US Gallons per Nautical Mile. This requires research. A good starting point is to search online for "[Aircraft Model] fuel consumption per hour" and "[Aircraft Model] cruise speed". Divide the gallons per hour by the cruise speed in knots (nautical miles per hour) to get a rough estimate.BLUESKY_HANDLE: Your bot's full Blue Sky handle (e.g., my-plane-bot.bsky.social).BLUESKY_APP_PASSWORD: An app-specific password. Do not use your main account password. You can generate one in Blue Sky under Settings -> App Passwords.RECIPIENT_EMAIL: The email address where the script will send critical error notifications.GEOLOCATOR_EMAIL: A contact email for the Nominatim geocoding service. It's a required part of their user agent policy.Running the ScriptManual ExecutionYou can run the script manually from your terminal to test it:python track_plane.py
Automatic Execution (Cron Job)For continuous, automatic tracking, set up a cron job. This example runs the script every 2 minutes.Open your crontab for editing:crontab -e
Add the following line. You must use the full, absolute paths to your Python executable (inside your venv) and to your script.*/2 * * * * /
