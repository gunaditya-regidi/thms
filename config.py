import os
from dotenv import load_dotenv

# Load variables from .env file
load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'treasure-hunt-super-secret-key-12345')
    
    # Database configuration - defaults to SQLite database.db in workspace directory
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        'DATABASE_URL', 
        f'sqlite:///{os.path.join(BASE_DIR, "database.db")}'
    )
    # Fix for Render/Heroku PostgreSQL URLs (which use postgres:// instead of postgresql://)
    if SQLALCHEMY_DATABASE_URI.startswith("postgres://"):
        SQLALCHEMY_DATABASE_URI = SQLALCHEMY_DATABASE_URI.replace("postgres://", "postgresql://", 1)
        
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Upload folder for clue images
    UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER', os.path.join(BASE_DIR, 'uploads'))
    
    # Folder for generated QR codes
    QR_FOLDER = os.environ.get('QR_FOLDER', os.path.join(BASE_DIR, 'generated_qr'))
    
    # Password gate for the statistics page
    STATS_PASSWORD = os.environ.get('STATS_PASSWORD', 'nstl@321')
    
    # Maximum content size (e.g. 5MB) for uploaded clue images
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024
