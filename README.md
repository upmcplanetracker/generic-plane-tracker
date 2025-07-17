# Plane Tracker

A Python script that tracks aircraft movements using ADSBexchange API data and posts flight status updates to Bluesky (formerly Twitter). The script monitors takeoffs, landings, and provides flight summaries including CO₂ emissions and equivalent car miles.

## Features

- **Real-time Flight Tracking**: Monitors aircraft using ICAO hex codes via ADSBexchange API
- **Social Media Integration**: Posts flight updates to Bluesky with formatted messages
- **Flight Metrics**: Calculates distance, fuel consumption, CO₂ emissions, and car mile equivalents
- **Geographic Information**: Converts coordinates to human-readable locations
- **Daily Reports**: Summarizes aircraft that haven't flown in 24+ hours
- **Monthly Summaries**: Provides monthly flight statistics and resets counters
- **Email Notifications**: Sends alerts for critical errors
- **State Persistence**: Maintains aircraft states between script runs
- **Log Rotation**: Automatically manages log file size and retention

## Requirements

### Python Dependencies

```bash
pip install requests python-dotenv geopy timezonefinder pytz
```

### External Dependencies

- `mail` command (for email notifications)
- Custom `bsky_bridge` module for Bluesky integration

### API Keys Required

- **ADSBexchange API Key** (via RapidAPI)
- **Bluesky App Password**

## Configuration

Create a `.env` file in the same directory as the script:

```env
# Aircraft Fleet Configuration (semicolon-separated)
# Format: "icao_hex,owner_name,fuel_burn_rate_per_nm"
AIRCRAFT_FLEET="a12345,Elmo Mush,0.97;b67890,Geoff Bozos,1.2"

# API Keys
ADSBEXCHANGE_API_KEY=your_rapidapi_key_here
BLUESKY_HANDLE=your_bluesky_handle
BLUESKY_APP_PASSWORD=your_bluesky_app_password

# Email Configuration
RECIPIENT_EMAIL=alerts@example.com
GEOLOCATOR_EMAIL=plane-tracker@example.com

# Thresholds & Behavior
ALTITUDE_THRESHOLD=500
GROUND_SPEED_THRESHOLD=50
MIN_STATE_CHANGE_TIME=900
LOG_RETENTION_HOURS=36
RETRY_COUNT=2
RETRY_DELAY=2

# Flight Metrics
DEFAULT_FUEL_BURN_GAL_PER_NM=0.97
JET_FUEL_CO2_LBS_PER_GALLON=21.1
LBS_PER_METRIC_TON=2204.62
CO2_TONS_PER_AVG_CAR_MILE=0.0004

# Timezone
DEFAULT_TIMEZONE=America/New_York
```

## Usage

### Basic Execution

```bash
python3 track_plane.py
```

### Running as a Cron Job

Add to your crontab for regular monitoring:

```bash
# Run every 5 minutes
*/5 * * * * /usr/bin/python3 /path/to/track_plane.py

# Run every 10 minutes
*/10 * * * * /usr/bin/python3 /path/to/track_plane.py
```
Each plane tracked counts as one call per script run. Be careful as API free calls are very limited.

## File Structure

```
plane_tracker/
├── track_plane.py          # Main script
├── .env                    # Configuration file
├── bsky_bridge.py          # Bluesky integration module
├── plane_states.json       # Aircraft state persistence
├── plane_tracker.log       # Application logs
└── daily_report_sent_*.lock # Daily report lock files
```

## Key Functions

### Core Processing

- `main()`: Entry point that processes all configured aircraft
- `process_plane()`: Handles individual aircraft status changes
- `get_plane_data()`: Fetches data from ADSBexchange API
- `calculate_flight_metrics()`: Computes distance, fuel, and emissions

### State Management

- `load_all_states()`: Loads aircraft states from JSON file
- `save_all_states()`: Persists states to JSON file
- `get_current_state_for_plane()`: Retrieves individual aircraft state

### Reporting

- `post_daily_stationary_report()`: Daily summary of non-flying aircraft
- `handle_monthly_summary()`: Monthly flight statistics and reset
- `post_to_bluesky()`: Social media posting with test mode support

### Utilities

- `get_location_name()`: Geocoding for human-readable locations
- `format_full_time_for_location()`: Timezone-aware time formatting
- `validate_coordinates()`: Coordinate validation
- `send_email()`: Email notification system

## Sample Output

### Takeoff Notification
```
✈️ **Elmo Mush** (A12345) has taken off from **Austin, Texas, United States**.
⏰ 2024-01-15, 10:30 AM CST / 2024-01-15, 16:30 UTC
📍 (30.1945, -97.6699)
Track: https://globe.adsb.fi/?icao=a12345
```

### Landing Notification
```
🛬 **Elmo Mush** (A12345) has landed in **Los Angeles, California, United States**.
⏰ 2024-01-15, 01:45 PM PST / 2024-01-15, 21:45 UTC
📍 (34.0522, -118.2437)
```

### Flight Summary
```
📊 **Flight Summary for Elmo Mush jet:**
• **Route:** Austin, Texas, United States to Los Angeles, California, United States
• **Distance:** ~1,200 nautical miles
• **CO₂ Emissions:** ~24.5 tons
• **Equivalent to:** ~61,250 miles driven by an average car.
```

## Configuration Options

### Thresholds

- `ALTITUDE_THRESHOLD`: Minimum altitude (feet) to consider "flying" (default: 500)
- `GROUND_SPEED_THRESHOLD`: Minimum ground speed (knots) to consider "flying" (default: 50)
- `MIN_STATE_CHANGE_TIME`: Minimum time between state changes to prevent flapping (default: 900 seconds)

### Logging

- `LOG_RETENTION_HOURS`: How long to keep log entries (default: 36 hours)
- `RETRY_COUNT`: Number of API retry attempts (default: 2)
- `RETRY_DELAY`: Delay between retries in seconds (default: 2)

### Flight Metrics

- `DEFAULT_FUEL_BURN_GAL_PER_NM`: Default fuel consumption per nautical mile
- `JET_FUEL_CO2_LBS_PER_GALLON`: CO₂ emissions per gallon of jet fuel
- `CO2_TONS_PER_AVG_CAR_MILE`: Average car CO₂ emissions per mile

## Error Handling

The script includes comprehensive error handling for:

- API failures with retry logic
- Invalid coordinates and data validation
- Email notification failures
- JSON parsing errors
- Geocoding service timeouts
- Network connectivity issues

## Security Considerations

- Store API keys in `.env` file (never commit to version control)
- Use app passwords for Bluesky authentication
- Validate all external API responses
- Implement rate limiting to respect API quotas

## License

This project is provided as-is for educational and personal use. Please respect API terms of service and privacy considerations when tracking aircraft. See LICENSE.md
