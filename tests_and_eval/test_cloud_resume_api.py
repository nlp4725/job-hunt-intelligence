"""Phase 5, slice 3A: resume upload through the cloud API.

Decided 2026-09-16: one private bucket, per-user keys (users/<id>/resumes/v<n>/),
presigned upload straight from the browser (a POST limited to that exact key,
1 byte to 5 MB, KMS encryption, 5 minutes) and short-lived presigned downloads.
The API never proxies the file; it reads it once to extract text and skills.
Local development uses DevSignedStorage: the same signed, expiring links,
served by a dev-only route.

API tests run as the restricted jhi_app login, so row-level security applies.
"""

import io
import time
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.fernet import Fernet
from moto import mock_aws

from tests_and_eval.test_cloud_db import needs_pg, pg_engine  # noqa: F401  (fixture)
from tests_and_eval.test_cloud_isolation import app_url  # noqa: F401  (fixture)

RESUME_TEXT = "SKILLS\nPython, SQL, Docker, LangGraph\nEXPERIENCE\nBuilt RAG systems on AWS.\n"


class TestDevSignedStorage:
    @pytest.fixture
    def storage(self, tmp_path):
        from resume.storage import DevSignedStorage

        return DevSignedStorage(root=tmp_path / "files", secret=b"test-secret", base_url="http://127.0.0.1:5060")

    def test_an_upload_ticket_is_signed_for_one_key(self, storage):
        ticket = storage.presign_upload("users/42/resumes/v1/cv.txt")
        fields = ticket.fields
        assert fields["key"] == "users/42/resumes/v1/cv.txt" and ticket.expires_in == 300
        assert storage.verify("upload", fields["key"], int(fields["expires"]), fields["signature"])
        assert not storage.verify("upload", "users/43/resumes/v1/cv.txt", int(fields["expires"]), fields["signature"])
        assert not storage.verify("download", fields["key"], int(fields["expires"]), fields["signature"])

    def test_an_expired_signature_is_rejected(self, storage):
        from resume.storage import sign

        expired = int(time.time()) - 1
        signature = sign(b"test-secret", "upload", "users/42/resumes/v1/cv.txt", expired)
        assert not storage.verify("upload", "users/42/resumes/v1/cv.txt", expired, signature)

    def test_files_over_the_size_limit_are_refused(self, storage):
        from resume.storage import MAX_RESUME_BYTES

        with pytest.raises(ValueError):
            storage.write("users/42/resumes/v1/big.pdf", b"x" * (MAX_RESUME_BYTES + 1))


class TestS3ResumeStorage:
    @pytest.fixture
    def storage(self):
        from resume.storage import S3ResumeStorage, make_s3_client

        with mock_aws():
            client = make_s3_client("us-east-1")
            client.create_bucket(Bucket="jhi-resumes")
            yield S3ResumeStorage(client, bucket="jhi-resumes", kms_key_id="alias/jhi-resumes")

    def test_upload_ticket_pins_key_size_and_encryption(self, storage):
        from resume.storage import MAX_RESUME_BYTES

        ticket = storage.presign_upload("users/42/resumes/v1/cv.pdf")
        assert ticket.fields["key"] == "users/42/resumes/v1/cv.pdf"
        assert ticket.fields["x-amz-server-side-encryption"] == "aws:kms"
        assert ticket.fields["x-amz-server-side-encryption-aws-kms-key-id"] == "alias/jhi-resumes"
        policy = ticket.policy_conditions
        assert ["content-length-range", 1, MAX_RESUME_BYTES] in policy
        assert {"key": "users/42/resumes/v1/cv.pdf"} in policy

    def test_download_link_is_short_lived_and_names_the_file(self, storage):
        url = urlparse(storage.presign_download("users/42/resumes/v1/cv.pdf", "my cv.pdf"))
        query = parse_qs(url.query)
        assert url.path.endswith("/users/42/resumes/v1/cv.pdf")
        assert query["X-Amz-Expires"] == ["300"]
        assert query["response-content-disposition"] == ['attachment; filename="my_cv.pdf"']

    def test_read_and_delete_a_users_files(self, storage):
        storage.client.put_object(Bucket="jhi-resumes", Key="users/42/resumes/v1/cv.txt", Body=b"hello")
        storage.client.put_object(Bucket="jhi-resumes", Key="users/42/resumes/v2/cv.txt", Body=b"again")
        storage.client.put_object(Bucket="jhi-resumes", Key="users/43/resumes/v1/cv.txt", Body=b"other")
        assert storage.read("users/42/resumes/v1/cv.txt") == b"hello"
        assert storage.read("users/42/resumes/v9/missing.txt") is None
        storage.delete_prefix("users/42/")
        keys = [o["Key"] for o in storage.client.list_objects_v2(Bucket="jhi-resumes").get("Contents", [])]
        assert keys == ["users/43/resumes/v1/cv.txt"]


@pytest.fixture
def client(app_url, tmp_path):  # noqa: F811
    from cloud_api.app import create_app
    from cloud_api.auth.verify import FakeVerifier
    from resume.storage import DevSignedStorage
    from resume.store import ResumeCipher

    storage = DevSignedStorage(root=tmp_path / "files", secret=b"test-secret", base_url="http://localhost")
    app = create_app(app_url, verifier=FakeVerifier(), auth_mode="dev", host="127.0.0.1",
                     storage=storage, cipher=ResumeCipher(Fernet.generate_key()))
    app.config["TESTING"] = True
    return app.test_client()


A = {"Authorization": "Bearer dev:a@example.com"}
B = {"Authorization": "Bearer dev:b@example.com"}


def _upload(client, headers, filename="cv.txt", body=RESUME_TEXT.encode(), tamper_key=None):
    """Start an upload, send the file the way a browser would, finish it."""
    started = client.post("/api/v1/me/resume", json={"filename": filename}, headers=headers)
    assert started.status_code == 201, started.get_json()
    ticket = started.get_json()
    fields = dict(ticket["upload"]["fields"])
    if tamper_key:
        fields["key"] = tamper_key
    sent = client.post(urlparse(ticket["upload"]["url"]).path,
                       data={**fields, "file": (io.BytesIO(body), filename)}, content_type="multipart/form-data")
    return ticket, sent


@needs_pg
class TestResumeApi:
    def test_upload_complete_and_list(self, client):
        ticket, sent = _upload(client, A)
        assert sent.status_code == 204
        done = client.post(f"/api/v1/me/resume/{ticket['version']}/complete", headers=A)
        assert done.status_code == 200
        assert {"Python", "SQL", "Docker", "LangGraph", "RAG", "AWS"} <= set(done.get_json()["skills_extracted"])

        listing = client.get("/api/v1/me/resume", headers=A).get_json()
        assert [v["version"] for v in listing["versions"]] == [1]
        assert listing["versions"][0]["skills_confirmed"] is None
        assert client.get("/api/v1/me", headers=A).get_json()["onboarding"]["resume"] is True

    def test_a_pending_upload_is_not_a_resume_yet(self, client):
        client.post("/api/v1/me/resume", json={"filename": "cv.txt"}, headers=A)
        assert client.get("/api/v1/me/resume", headers=A).get_json()["versions"] == []
        assert client.get("/api/v1/me", headers=A).get_json()["onboarding"]["resume"] is False

    def test_finishing_before_the_file_arrived_is_a_conflict(self, client):
        started = client.post("/api/v1/me/resume", json={"filename": "cv.txt"}, headers=A).get_json()
        assert client.post(f"/api/v1/me/resume/{started['version']}/complete", headers=A).status_code == 409

    def test_an_upload_to_a_key_the_ticket_did_not_sign_is_refused(self, client):
        _, sent = _upload(client, A, tamper_key="users/999/resumes/v1/cv.txt")
        assert sent.status_code == 403

    def test_unsupported_types_are_refused(self, client):
        assert client.post("/api/v1/me/resume", json={"filename": "cv.pages"}, headers=A).status_code == 415
        ticket, _ = _upload(client, A, filename="cv.pdf", body=b"not really a pdf")
        assert client.post(f"/api/v1/me/resume/{ticket['version']}/complete", headers=A).status_code == 415
        assert client.get("/api/v1/me/resume", headers=A).get_json()["versions"] == []

    def test_confirming_skills(self, client):
        ticket, _ = _upload(client, A)
        client.post(f"/api/v1/me/resume/{ticket['version']}/complete", headers=A)
        confirmed = client.put(f"/api/v1/me/resume/{ticket['resume_id']}/skills", json={"skills": ["SQL", "Python"]}, headers=A)
        assert confirmed.status_code == 200 and confirmed.get_json()["skills_confirmed"] == ["Python", "SQL"]
        bad = client.put(f"/api/v1/me/resume/{ticket['resume_id']}/skills", json={"skills": ["Basket Weaving"]}, headers=A)
        assert bad.status_code == 400

    def test_the_download_link_returns_the_same_file_and_expires(self, client):
        ticket, _ = _upload(client, A)
        client.post(f"/api/v1/me/resume/{ticket['version']}/complete", headers=A)
        link = client.get(f"/api/v1/me/resume/{ticket['resume_id']}/file", headers=A).get_json()
        assert link["expires_in"] == 300
        got = client.get(urlparse(link["url"]).path + "?" + urlparse(link["url"]).query)
        assert got.status_code == 200 and got.data == RESUME_TEXT.encode()
        assert "attachment" in got.headers["Content-Disposition"]
        tampered = link["url"].replace("signature=", "signature=0")
        assert client.get(urlparse(tampered).path + "?" + urlparse(tampered).query).status_code == 403

    def test_another_user_cannot_see_finish_confirm_or_download_it(self, client):
        ticket, _ = _upload(client, A)
        client.post(f"/api/v1/me/resume/{ticket['version']}/complete", headers=A)
        client.get("/api/v1/me", headers=B)   # B signs in
        assert client.get("/api/v1/me/resume", headers=B).get_json()["versions"] == []
        assert client.post(f"/api/v1/me/resume/{ticket['version']}/complete", headers=B).status_code == 404
        assert client.put(f"/api/v1/me/resume/{ticket['resume_id']}/skills", json={"skills": ["Python"]}, headers=B).status_code == 404
        assert client.get(f"/api/v1/me/resume/{ticket['resume_id']}/file", headers=B).status_code == 404

    def test_every_resume_route_needs_sign_in(self, client):
        for method, path in (("post", "/api/v1/me/resume"), ("get", "/api/v1/me/resume"),
                             ("post", "/api/v1/me/resume/1/complete"), ("put", "/api/v1/me/resume/1/skills"),
                             ("get", "/api/v1/me/resume/1/file")):
            assert getattr(client, method)(path).status_code == 401, path
