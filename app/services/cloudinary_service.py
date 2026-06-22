import cloudinary
import cloudinary.uploader
import logging
from app.config import settings

logger = logging.getLogger(__name__)

# Configure the Cloudinary library globally
cloudinary.config( 
  cloud_name = settings.CLOUDINARY_CLOUD_NAME, 
  api_key = settings.CLOUDINARY_API_KEY, 
  api_secret = settings.CLOUDINARY_API_SECRET,
  secure = True
)

def upload_audio_to_cloudinary(file_path: str, public_id: str = None) -> dict:
    """
    Uploads an audio file to Cloudinary.
    Note: Cloudinary categorizes audio under the "video" resource type.
    """
    try:
        response = cloudinary.uploader.upload(
            file_path, 
            resource_type="video",
            public_id=public_id,
            folder="audio_uploads"
        )
        
        return {
            "url": response.get("secure_url"),
            "public_id": response.get("public_id"),
            "duration": response.get("duration")
        }
    except Exception as e:
        logger.error(f"Failed to upload {file_path} to Cloudinary: {str(e)}")
        raise e
