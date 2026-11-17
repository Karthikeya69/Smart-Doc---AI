import json
import os
import boto3
from functools import lru_cache


@lru_cache()
def get_settings() -> dict:
    region = os.getenv("AWS_REGION", "us-east-1")
    secret_name = os.getenv("SECRET_NAME", "smartdoc-ai/config")
    defaults = {
        "S3_BUCKET": os.getenv("S3_BUCKET", "smartdoc-ai-pdfs-290723902454"),
        "AWS_REGION": region,
        "EMBED_MODEL": os.getenv("EMBED_MODEL", "amazon.titan-embed-text-v2:0"),
        "CHAT_MODEL": os.getenv("CHAT_MODEL", "us.amazon.nova-lite-v1:0"),
    }
    try:
        client = boto3.client("secretsmanager", region_name=region)
        resp = client.get_secret_value(SecretId=secret_name)
        secret_data = json.loads(resp["SecretString"])
        defaults.update(secret_data)
    except Exception as e:
        print(f"[Config] Using env/default settings (Secrets Manager note: {e})")
    return defaults