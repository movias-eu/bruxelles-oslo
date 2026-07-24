"""Settings for the read-only mimic of the client's `webtools` Django app.

This is NOT the client's real settings.py — it is the minimum needed to let the
Django ORM connect to the real `webtools` Postgres database (over the FortiClient
VPN) and read the `traffic_*` tables through the provided models.

Safety: every model is declared ``managed = False`` (see traffic/models.py), so
Django will never issue CREATE/ALTER/DROP against the client DB. We only read.

All connection settings come from the environment (see ../.envrc) — nothing
sensitive (host, user, db name, password) is hardcoded here. Every value is
REQUIRED: if it is missing the configuration fails loudly rather than falling
back to a baked-in default.
"""
import os

from django.core.exceptions import ImproperlyConfigured


def require_env(name):
    """Return os.environ[name] or fail loudly. No defaults for DB settings."""
    try:
        return os.environ[name]
    except KeyError:
        raise ImproperlyConfigured(
            f"Required environment variable {name!r} is not set. "
            "Define it in .envrc (see README) — there are no defaults."
        )


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SECRET_KEY = "mimic-only-not-a-real-secret"
DEBUG = True
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "traffic",
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": require_env("PG_METADATA_DB"),
        "USER": require_env("PG_USER"),
        "PASSWORD": require_env("PGPASSWORD"),
        "HOST": require_env("PG_METADATA_HOST"),
        "PORT": require_env("PG_METADATA_PORT"),
        # Defensive: ask Postgres to keep this session read-only.
        "OPTIONS": {"options": "-c default_transaction_read_only=on"},
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.AutoField"
USE_TZ = True
