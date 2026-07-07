from fastapi import APIRouter, Depends
from cryptography.fernet import Fernet
import base64
from app.config import settings
from app.utils.auth import get_current_user

router = APIRouter(prefix="/config", tags=["config"])

@router.get("/gemini-key")
def get_encrypted_gemini_key(current_user = Depends(get_current_user)):
    """
    Returns the Gemini API key encrypted. 
    Only authenticated users can request this.
    """
    # Use the encryption key from settings
    f = Fernet(base64.urlsafe_b64encode(settings.ENCRYPTION_KEY.encode()))
    
    # Encrypt the Gemini key from your settings
    gemini_key = settings.GEMINI_API_KEY.encode()
    encrypted_key = f.encrypt(gemini_key).decode()
    
    return {
        "encrypted_key": encrypted_key
    }
