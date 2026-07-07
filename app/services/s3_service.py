import boto3
import logging
from app.config import settings

logger = logging.getLogger(__name__)

def upload_audio_to_s3(file_path: str, s3_key: str) -> dict:
    """
    Uploads an audio file to AWS S3.
    """
    try:
        # Initialize client with credentials from settings
        s3_client = boto3.client(
            's3',
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_REGION
        )
        
        # Determine content type based on extension
        content_type = "audio/wav"
        if file_path.lower().endswith(".mp3"):
            content_type = "audio/mpeg"
        elif file_path.lower().endswith(".ogg"):
            content_type = "audio/ogg"
        
        # Upload file
        s3_client.upload_file(
            Filename=file_path,
            Bucket=settings.AWS_S3_BUCKET,
            Key=s3_key,
            ExtraArgs={
                'ContentType': content_type
            }
        )
        
        # Construct the S3 URL
        url = f"https://{settings.AWS_S3_BUCKET}.s3.{settings.AWS_REGION}.amazonaws.com/{s3_key}"
        logger.info(f"Successfully uploaded {file_path} to S3: {url}")
        
        return {
            "url": url,
            "key": s3_key
        }
    except Exception as e:
        logger.error(f"Failed to upload {file_path} to S3: {str(e)}")
        raise e


def generate_presigned_url(audio_url: str, expiration: int = 3600) -> str:
    """
    Generate a presigned URL from a stored S3 URL so the frontend
    can securely stream/download the audio.

    Args:
        audio_url:  The plain S3 URL stored in the database
                    (e.g. https://bucket.s3.region.amazonaws.com/key)
        expiration: URL lifetime in seconds (default 1 hour)

    Returns:
        A time-limited presigned URL that grants temporary read access.
        Returns the original URL unchanged if it's not an S3 URL.
    """
    # Only process S3 URLs
    if not audio_url or "s3" not in audio_url:
        return audio_url

    try:
        # Extract the S3 key from the stored URL
        # Format: https://<bucket>.s3.<region>.amazonaws.com/<key>
        prefix = f"https://{settings.AWS_S3_BUCKET}.s3.{settings.AWS_REGION}.amazonaws.com/"
        if audio_url.startswith(prefix):
            s3_key = audio_url[len(prefix):]
        else:
            logger.warning(f"Unexpected S3 URL format: {audio_url}")
            return audio_url

        s3_client = boto3.client(
            's3',
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_REGION
        )

        presigned_url = s3_client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': settings.AWS_S3_BUCKET,
                'Key': s3_key
            },
            ExpiresIn=expiration
        )
        return presigned_url

    except Exception as e:
        logger.error(f"Failed to generate presigned URL: {str(e)}")
        return audio_url
