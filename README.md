Corporate/Non-Profit Executive Jet Tracker for Blue Sky
=======================================================

This Python script tracks a fleet of aircraft using public ADS-B data and automatically posts takeoff, landing, and flight summary information to a Blue Sky social media account.

Purpose of the Project
----------------------

The goal of this project is to provide public transparency into the flight activities of corporate or private jets. By leveraging publicly available flight data, it automatically creates social media posts that detail the movements and environmental impact of these flights, fostering awareness and discussion.

What the Script Does
--------------------

*   **Multi-Aircraft Tracking:** Monitors any number of aircraft simultaneously based on their ICAO hex codes.
    
*   **Automatic Event Posting:** Connects to the Blue Sky API to post updates when a plane takes off or lands.
    
*   **Flight Summaries:** After a plane lands, it calculates and posts a summary including the estimated flight distance, fuel consumption, and CO₂ emissions.
    
*   **Custom Metrics:** Uses per-aircraft fuel burn rates, configured by the user, for more accurate environmental impact estimates.
    
*   **Idle Alerts:** Posts a notification if a plane has been on the ground at a single location for more than 24 hours. This report runs once per day.
    
*   **Monthly Summaries:** At the beginning of each month, it posts a summary of all tracked flights from the previous month and resets the monthly totals.
    
*   **Robust State Management:** It maintains a plane\_states.json file to remember the last known state (flying/landed, location, etc.) of each aircraft. This prevents duplicate posts and ensures that flight summaries are calculated correctly.
    
*   **API Reliability:** Includes retry mechanisms for API calls to improve robustness against transient network issues or API downtime.
    
*   **Detailed Logging:** Keeps a running plane\_tracker.log file of all actions, API calls, and errors for easy debugging, with configurable log retention.
    
*   **Email Notifications:** Sends critical error notifications to a specified email address.
    

Setup Instructions
------------------

### 1\. Prerequisites

*   Python 3.x
    
*   A Blue Sky account for the bot to post from.
    
*   An email account on a system with the mail command (for receiving critical error notifications).
    

### 2\. Installation

Clone this repository and navigate into the project directory. It is highly recommended to use a Python virtual environment to manage dependencies.

Plain textANTLR4BashCC#CSSCoffeeScriptCMakeDartDjangoDockerEJSErlangGitGoGraphQLGroovyHTMLJavaJavaScriptJSONJSXKotlinLaTeXLessLuaMakefileMarkdownMATLABMarkupObjective-CPerlPHPPowerShell.propertiesProtocol BuffersPythonRRubySass (Sass)Sass (Scss)SchemeSQLShellSwiftSVGTSXTypeScriptWebAssemblyYAMLXML`   # Create and activate a virtual environment  python3 -m venv venv  source venv/bin/activate  # Install the required Python packages from requirements.txt  pip install -r requirements.txt   `

You will need to create a requirements.txt file in your project directory with the following contents:

Plain textANTLR4BashCC#CSSCoffeeScriptCMakeDartDjangoDockerEJSErlangGitGoGraphQLGroovyHTMLJavaJavaScriptJSONJSXKotlinLaTeXLessLuaMakefileMarkdownMATLABMarkupObjective-CPerlPHPPowerShell.propertiesProtocol BuffersPythonRRubySass (Sass)Sass (Scss)SchemeSQLShellSwiftSVGTSXTypeScriptWebAssemblyYAMLXML`   requests  python-dotenv  geopy  timezonefinder  pytz  atprototools   `

### 3\. Configuration (.env file)

This script is configured using a .env file in the root of the project directory. You must create this file yourself; it is not included in the repository for security reasons.

**Important:** Never commit your .env file to a public GitHub repository.

#### .env File Template

Create a file named .env and add the following content, replacing the placeholder values with your actual information.

Plain textANTLR4BashCC#CSSCoffeeScriptCMakeDartDjangoDockerEJSErlangGitGoGraphQLGroovyHTMLJavaJavaScriptJSONJSXKotlinLaTeXLessLuaMakefileMarkdownMATLABMarkupObjective-CPerlPHPPowerShell.propertiesProtocol BuffersPythonRRubySass (Sass)Sass (Scss)SchemeSQLShellSwiftSVGTSXTypeScriptWebAssemblyYAMLXML`   # ----------------------------------------------------  # AIRCRAFT FLEET CONFIGURATION  # Format: ICAO,"Owner",FuelBurn;ICAO,"Owner",FuelBurn;...  # ----------------------------------------------------  AIRCRAFT_FLEET='A3C19C,"Dick''s Sporting Goods",0.67;A66A59,"PNC",0.74'  # ----------------------------------------------------  # ADSBexchange API Key (from RapidAPI)  # ----------------------------------------------------  ADSBEXCHANGE_API_KEY="YOUR_ADSBEXCHANGE_RAPIDAPI_KEY"  # ----------------------------------------------------  # BLUE SKY CREDENTIALS  # ----------------------------------------------------  BLUESKY_HANDLE="your-handle.bsky.social"  BLUESKY_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx"  # ----------------------------------------------------  # OTHER CONFIGURATIONS  # ----------------------------------------------------  RECIPIENT_EMAIL="your-email@example.com"  GEOLOCATOR_EMAIL="your-personal-email@example.com"  DEFAULT_TIMEZONE="America/New_York" # e.g., "America/New_York", "Europe/London"  # ----------------------------------------------------  # SCRIPT BEHAVIOR & THRESHOLDS (Optional)  # ----------------------------------------------------  ALTITUDE_THRESHOLD=500              # Altitude in feet above which a plane is considered flying  GROUND_SPEED_THRESHOLD=50           # Ground speed in knots above which a plane is considered flying  MIN_STATE_CHANGE_TIME=900           # Minimum time in seconds (e.g., 900s = 15 minutes) before a state change (takeoff/landing) is posted to prevent "flapping"  LOG_RETENTION_HOURS=36              # How many hours of logs to keep in plane_tracker.log  RETRY_COUNT=2                       # Number of retries for API calls  RETRY_DELAY=2                       # Delay in seconds between API retries   `

#### Configuration Details

*   AIRCRAFT\_FLEET: This is the most important variable. It's a single line containing all the aircraft to track, separated by semicolons (;). Add as many planes as you like, but the more planes you add, the more API calls you make and the slower the script runs.
    
    *   **Format:** ICAO\_HEX,"Owner Name",Fuel\_Burn\_Gal\_per\_NM
        
    *   **ICAO Hex Code:** The unique 6-character code for the aircraft. You can find this on sites like the [FAA Registry](https://www.google.com/search?q=https://registry.faa.gov/aircraftinquiry/Search_NNumber.aspx) by searching for the aircraft's tail number (N-Number).
        
    *   **Owner Name:** The name you want to display in posts. It **must be in double quotes**. If the name itself contains an apostrophe, use two apostrophes (e.g., "Dick''s Sporting Goods").
        
    *   **Fuel Burn Rate:** The estimated fuel consumption in US Gallons per Nautical Mile. This requires research. A good starting point is to search online for "\[Aircraft Model\] fuel consumption per hour" and "\[Aircraft Model\] cruise speed". Divide the gallons per hour by the cruise speed in knots (nautical miles per hour) to get a rough estimate.
        
*   ADSBEXCHANGE\_API\_KEY: Your API key for ADSBexchange from RapidAPI.
    
    *   **How to get your ADSBexchange API Key:**
        
        1.  Go to the [ADSBexchange API page on RapidAPI](https://www.google.com/search?q=https://rapidapi.com/adsbexchange/api/adsbexchange-com1).
            
        2.  You will need to sign up for a RapidAPI account if you don't already have one.
            
        3.  Once logged in, subscribe to a pricing plan that suits your needs (they often have a free tier for basic usage).
            
        4.  After subscribing, your API key will be displayed on the API page. Look for "X-RapidAPI-Key" in the "Code Snippets" or "API Key" section. Copy this key.
            
*   BLUESKY\_HANDLE: Your bot's full Blue Sky handle (e.g., my-plane-bot.bsky.social).
    
*   BLUESKY\_APP\_PASSWORD: An app-specific password. **Do not use your main account password.** You can generate one in Blue Sky under Settings -> App Passwords.
    
*   RECIPIENT\_EMAIL: The email address where the script will send critical error notifications.
    
*   GEOLOCATOR\_EMAIL: A contact email for the Nominatim geocoding service. It's a required part of their user agent policy.
    
*   DEFAULT\_TIMEZONE: The local timezone for logging and daily report timing (e.g., America/New\_York).
    
*   ALTITUDE\_THRESHOLD: Configurable minimum altitude for considering a plane flying.
    
*   GROUND\_SPEED\_THRESHOLD: Configurable minimum ground speed for considering a plane flying.
    
*   MIN\_STATE\_CHANGE\_TIME: Time in seconds to prevent rapid "flapping" between flying/landed states from triggering multiple posts.
    
*   LOG\_RETENTION\_HOURS: Number of hours to retain log entries in plane\_tracker.log.
    
*   RETRY\_COUNT: Number of times to retry API calls if they fail.
    
*   RETRY\_DELAY: Delay in seconds between API retries.
    

Running the Script
------------------

### Manual Execution

You can run the script manually from your terminal to test it:

Plain textANTLR4BashCC#CSSCoffeeScriptCMakeDartDjangoDockerEJSErlangGitGoGraphQLGroovyHTMLJavaJavaScriptJSONJSXKotlinLaTeXLessLuaMakefileMarkdownMATLABMarkupObjective-CPerlPHPPowerShell.propertiesProtocol BuffersPythonRRubySass (Sass)Sass (Scss)SchemeSQLShellSwiftSVGTSXTypeScriptWebAssemblyYAMLXML`   python track_plane.py   `

### Automatic Execution (Cron Job)

For continuous, automatic tracking, set up a cron job. This example runs the script every 2 minutes.

Open your crontab for editing:

Plain textANTLR4BashCC#CSSCoffeeScriptCMakeDartDjangoDockerEJSErlangGitGoGraphQLGroovyHTMLJavaJavaScriptJSONJSXKotlinLaTeXLessLuaMakefileMarkdownMATLABMarkupObjective-CPerlPHPPowerShell.propertiesProtocol BuffersPythonRRubySass (Sass)Sass (Scss)SchemeSQLShellSwiftSVGTSXTypeScriptWebAssemblyYAMLXML`   crontab -e   `

Add the following line. **You must use the full, absolute paths** to your Python executable (inside your venv) and to your script.

Plain textANTLR4BashCC#CSSCoffeeScriptCMakeDartDjangoDockerEJSErlangGitGoGraphQLGroovyHTMLJavaJavaScriptJSONJSXKotlinLaTeXLessLuaMakefileMarkdownMATLABMarkupObjective-CPerlPHPPowerShell.propertiesProtocol BuffersPythonRRubySass (Sass)Sass (Scss)SchemeSQLShellSwiftSVGTSXTypeScriptWebAssemblyYAMLXML`   */2 * * * * cd /home/your_user/plane_tracker_project/ && /home/your_user/plane_tracker_project/venv/bin/python3 track_plane.py >> /home/your_user/plane_tracker_project/cron.log 2>&1   `

This command will:

*   Run the script every two minutes. Change this to whatever frequency you desire. The more frequently it runs, the more granular the data it gathers, at the expense of API calls.
    
*   Use the correct Python interpreter from your virtual environment.
    
*   Append all output (both standard and error messages) to a cron.log file in your project directory for easy debugging.