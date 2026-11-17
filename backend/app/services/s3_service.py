import boto3
from app.config import get_settings


def _s3_client():
    cfg = get_settings()
    return boto3.client("s3", region_name=cfg["AWS_REGION"]), cfg["S3_BUCKET"]


def upload_pdf_to_s3(file_bytes: bytes, filename: str) -> str:
    client, bucket = _s3_client()
    s3_key = f"pdfs/{filename}"
    client.put_object(
        Bucket=bucket,
        Key=s3_key,
        Body=file_bytes,
        ContentType="application/pdf",
        ContentDisposition="inline",
    )
    return s3_key


def get_pdf_presigned_url(filename: str, expires_in: int = 3600) -> str:
    client, bucket = _s3_client()
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": f"pdfs/{filename}"},
        ExpiresIn=expires_in,
    )