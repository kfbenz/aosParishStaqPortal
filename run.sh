#!/bin/bash
# Run the FastAPI portal

# Activate virtual environment if it exists
if [ -d "venv" ]; then
    source venv/bin/activate
fi

# Development mode (with auto-reload)
if [ "$1" = "dev" ]; then
    echo "Starting in development mode..."
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
else
    # Production mode
    echo "Starting in production mode..."
    uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
fi
