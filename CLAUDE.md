# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Pendelplaner** is a German commute planning tool that optimizes departure times using the Google Routes API. It's a single Python script that plans optimal commute times for a work week, considering traffic patterns, home office days, gym schedules, and flexible work arrangements.

## Development Commands

### Setup
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Running the Application
```bash
source .venv/bin/activate
python pendelplaner.py
```

### CLI Options
```bash
python pendelplaner.py [--color auto|always|never] [--ascii] [--width N] [--compact-weekly|--no-compact-weekly]
```

### Watch Mode (continuous monitoring)
```bash
nohup sh -c 'while true; do printf "\n===== %s =====\n" "$(date)"; \
  .venv/bin/python pendelplaner.py 2>&1; sleep 300; done' \
  >> watch.log 2>&1 & echo $! > watch.pid
```

Stop watch mode:
```bash
kill $(cat watch.pid)
```

## Architecture Overview

### Single File Structure
The entire application is contained in `pendelplaner.py` (~2000+ lines) with the following key components:

- **Configuration Management**: Environment-based config via `.env` file with extensive customization options
- **Google Routes API Client**: Traffic-aware route optimization using Google's Routes API v2
- **Caching System**: Persistent disk cache (`routes_cache.json`) with TTL and granular time-based keys
- **Weekly Planning Engine**: Optimizes entire work weeks considering home office quotas, gym schedules, and timebank management
- **Commute Optimization**: Multi-objective optimization balancing travel time, flexible schedules, and personal constraints

### Key Classes

- `AppConfig`: Configuration management and environment variable processing
- `RoutesApiClient`: Google Routes API integration with rate limiting and error handling  
- `CommuteLeg`: Data structure for individual commute segments
- `DayPlanDM`: Daily plan data model
- `ProgressReporter`: Progress tracking for long operations

### Core Functions

- `weekly_plan()`: Generates optimized weekly schedules (line ~1431)
- `scan_morning_best_departure()`: Finds optimal morning departure times (line ~1654)  
- `choose_best_evening_departure*()`: Family of functions for evening optimization with extensions and timebank (lines ~1755, ~1824, ~1852)
- `optimize_day_with_extension()`: Day-level optimization with evening extension logic (line ~1976)

## Configuration System

The application uses a comprehensive `.env` configuration system with 50+ parameters covering:

- **Basic Setup**: API keys, addresses, work hours, time zones
- **Schedule Flexibility**: Morning/afternoon windows, lunch breaks, personal breaks  
- **Weekly Planning**: Home office quotas, block scheduling, slot-based half-days
- **Gym Integration**: Multi-location gym optimization with weekly caps and preferred days
- **Timebank Management**: Flexible time compensation system
- **API Management**: Rate limiting, caching, budget controls
- **Output Customization**: Color modes, ASCII fallbacks, compact formatting

### Key Configuration Areas

- Required: `GOOGLE_MAPS_API_KEY`, origin/destination addresses, work hours
- Weekly Planning: `WEEKLY_BLOCKS` or slot-based configuration (`MO_AM`, `TU_PM`, etc.)
- Gym Features: `GYM_ENABLED`, gym locations, training parameters
- Timebank: `TIMEBANK_CURRENT_MIN`, spending limits, activity preferences

## Development Notes

### API Integration
- Uses Google Routes API v2 (`https://routes.googleapis.com/directions/v2:computeRoutes`)
- Implements intelligent caching with time-granular keys to minimize API calls
- Rate limiting with `MAX_API_CALLS_PER_RUN` (default 300)
- Handles traffic-aware routing with departure time optimization

### Caching Strategy
- Persistent JSON cache (`routes_cache.json`) with configurable TTL
- Cache keys based on origin/destination/departure time rounded to configured granularity
- Supports cache invalidation and size limits (`ROUTE_CACHE_MAX_ENTRIES`)

### Optimization Logic
- Multi-step morning departure scanning with configurable time windows
- Evening optimization with extension algorithms (stay longer to avoid traffic)
- Timebank integration for flexible departure times with gym/activity scheduling
- Weekly constraint satisfaction (home office quotas, gym day limits)

### Output and Formatting
- Responsive terminal output with UTF-8/ASCII fallbacks
- Color support with `NO_COLOR` environment variable respect
- Progress reporting for long-running operations
- Compact and detailed weekly view modes

## Security Considerations

- API keys must be stored only in `.env` file (git-ignored)
- No secrets should be logged or committed to version control
- API key validation on startup with clear error messaging