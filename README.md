# Audio Intelligence Platform

This project is the backend for the Audio Intelligence Platform, built with FastAPI, SQLAlchemy, and Celery.

## Prerequisites

- Python 3.11+ (recommended)
- Git

## Installation & Setup

1. **Clone the repository** (if you haven't already):
   ```bash
   git clone https://github.com/harshal246/audio_Intelligence_backend.git
   cd audio_intelligence_platform
   ```

2. **Create and activate a virtual environment**:
   - On Windows:
     ```bash
     python -m venv venv
     .\venv\Scripts\activate
     ```
   - On macOS/Linux:
     ```bash
     python -m venv venv
     source venv/bin/activate
     ```

3. **Install dependencies**:
   Install the required Python packages using `pip`:
   ```bash
   pip install -r requirements.txt
   ```

4. **Environment Variables**:
   Create a `.env` file in the root directory (if it doesn't exist) and populate it with your configuration variables. 
   ```bash
   # Example .env contents
   DATABASE_URL=sqlite:///./test.db
   # Add other required variables here
   ```

## Running the Application

### Start the FastAPI Server

To run the application for local development, use `uvicorn`:

```bash
uvicorn app.main:app --reload
```

The API will be available at: http://127.0.0.1:8000
Interactive API documentation (Swagger UI) will be available at: http://127.0.0.1:8000/docs

### Background Tasks (Celery)

If your setup utilizes Celery for background processing, you will need a message broker (like Redis or RabbitMQ) and to start the celery worker:

```bash
celery -A app.celery_app worker --loglevel=info
```
*(Make sure to configure your broker URL in the `.env` file or `config.py`)*
