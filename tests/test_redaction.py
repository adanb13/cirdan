from cirdan.access.redaction import REDACTED, redact_obj, redact_text


def test_redacts_url_credentials():
    out = redact_text("postgres://admin:hunter2@db.internal:5432/app")
    assert "hunter2" not in out
    assert "db.internal" in out


def test_redacts_secret_env_pairs():
    out = redact_text("DATABASE_PASSWORD=supersecret PORT=8080")
    assert "supersecret" not in out
    assert "PORT=8080" in out


def test_redacts_aws_key():
    assert "AKIA" not in redact_text("key id AKIAIOSFODNN7EXAMPLE here")


def test_redact_obj_drops_secret_keys():
    obj = {"api_key": "abc123", "name": "web", "nested": {"TOKEN": "zzz"}}
    out = redact_obj(obj)
    assert out["api_key"] == REDACTED
    assert out["nested"]["TOKEN"] == REDACTED
    assert out["name"] == "web"
