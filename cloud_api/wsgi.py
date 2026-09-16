"""Production entry point: gunicorn "cloud_api.wsgi:app" (see Dockerfile).

Environment (set by infra/):
    AWS_REGION, DB_HOST, DB_PORT, DB_NAME
    JHI_APP_DB_USER / JHI_APP_DB_PASSWORD        login in the jhi_app role (user requests)
    JHI_ADMIN_DB_USER / JHI_ADMIN_DB_PASSWORD    login in the jhi_admin_api role (admin routes)
    COGNITO_USER_POOL_ID, COGNITO_CLIENT_ID
    RESUME_BUCKET, RESUME_KMS_KEY_ID, JHI_RESUME_KEY
    CORS_ORIGINS                                 comma-separated, e.g. https://app.example.com
    DEEPSEEK_API_KEY                             read by the LLM client
"""

from cloud_api.app import create_app
from cloud_api.auth.verify import CognitoVerifier
from cloud_api.settings import CachedJwks, cognito_issuer, database_url, fernet_key_from_secret, required
from resume.storage import S3ResumeStorage, make_s3_client
from resume.store import ResumeCipher


def build_app():
    region = required("AWS_REGION")
    issuer = cognito_issuer(region, required("COGNITO_USER_POOL_ID"))
    verifier = CognitoVerifier(issuer=issuer, client_id=required("COGNITO_CLIENT_ID"),
                               jwks=CachedJwks(f"{issuer}/.well-known/jwks.json"))
    storage = S3ResumeStorage(make_s3_client(region), required("RESUME_BUCKET"), required("RESUME_KMS_KEY_ID"))
    origins = tuple(o.strip() for o in required("CORS_ORIGINS").split(",") if o.strip())
    return create_app(
        database_url("JHI_APP_DB_USER", "JHI_APP_DB_PASSWORD"), verifier,
        admin_database_url=database_url("JHI_ADMIN_DB_USER", "JHI_ADMIN_DB_PASSWORD"),
        auth_mode="cognito", host="0.0.0.0", cors_origins=origins,
        storage=storage, cipher=ResumeCipher(fernet_key_from_secret(required("JHI_RESUME_KEY"))),
    )


app = build_app()
