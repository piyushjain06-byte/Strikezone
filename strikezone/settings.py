"""
Django settings for strikezone project.
Production-ready for Railway deployment.
"""

from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent

# ── Security ──────────────────────────────────────────────────────────────────
# On Railway set SECRET_KEY in environment variables dashboard
SECRET_KEY = os.environ.get('SECRET_KEY', 'django-insecure-1!eazvx6jdvk(ea16)s#*6eggf6h%cfxd44^a3g#wmn(7w3644')

# DEBUG is False on Railway (set DEBUG=False in Railway env vars)
DEBUG = os.environ.get('DEBUG', 'True') == 'True'

ALLOWED_HOSTS = ['*']  # Railway handles SSL termination, * is fine

# Trust Railway's proxy headers for HTTPS detection
CSRF_TRUSTED_ORIGINS = [
    'https://*.railway.app',
    'https://*.up.railway.app',
    'http://localhost:8000',
    'http://127.0.0.1:8000',
]

# ── Apps ──────────────────────────────────────────────────────────────────────
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'channels',
    'accounts',
    'tournaments',
    'teams',
    'matches',
    'scoring',
    'knockout',
    'subscriptions',
    'employee',
    'ceo',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',   # ← serves static files on Railway
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'strikezone.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': ['templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'tournaments.context_processors.get_categories',
                'subscriptions.context_processors.subscription_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'strikezone.wsgi.application'
ASGI_APPLICATION  = 'strikezone.asgi.application'

# ── Django Channels ───────────────────────────────────────────────────────────
# InMemoryChannelLayer works fine for Railway (single dyno)
# If you later need multiple dynos, switch to Redis channel layer
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer"
    }
}

# ── Database ──────────────────────────────────────────────────────────────────
# Railway provides DATABASE_URL automatically when you add a Postgres service
# Falls back to SQLite locally
DATABASE_URL = os.environ.get('DATABASE_URL')

if DATABASE_URL:
    import dj_database_url
    DATABASES = {
        'default': dj_database_url.config(
            default=DATABASE_URL,
            conn_max_age=600,
            ssl_require=True,
        )
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

# ── Password Validation ───────────────────────────────────────────────────────
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# ── Internationalisation ──────────────────────────────────────────────────────
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Kolkata'   # ← changed to IST since your users are in India
USE_I18N = True
USE_TZ = True

# ── Static Files ──────────────────────────────────────────────────────────────
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'          # collectstatic writes here
STATICFILES_DIRS = [BASE_DIR / 'strikezone' / 'static']
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

# ── Media Files (player photos) ───────────────────────────────────────────────
# NOTE: Railway's filesystem is ephemeral — uploaded photos will be lost on redeploy.
# For now this works fine for testing. For production, switch to Cloudinary (see README).
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# ── Default Primary Key ───────────────────────────────────────────────────────
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ── SMS / OTP (Twilio) ────────────────────────────────────────────────────────
TWILIO_ACCOUNT_SID  = os.environ.get('TWILIO_ACCOUNT_SID',  'AC2ec47c0a8089ac515cc92d7cd0a85177')
TWILIO_AUTH_TOKEN   = os.environ.get('TWILIO_AUTH_TOKEN',   '71119eaa67aa79511e5e2e4a1644fc38')
TWILIO_PHONE_NUMBER = os.environ.get('TWILIO_PHONE_NUMBER', '+15096613947')

# ── Groq AI (CrickBot) ────────────────────────────────────────────────────────
os.environ.setdefault("GROQ_API_KEY", os.environ.get('GROQ_API_KEY', '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'))

# ── Razorpay ──────────────────────────────────────────────────────────────────
RAZORPAY_KEY_ID     = os.environ.get('RAZORPAY_KEY_ID',     'rzp_test_SOEWxkocanVZ8A')
RAZORPAY_KEY_SECRET = os.environ.get('RAZORPAY_KEY_SECRET', 'H4yBYQasEElQyEfoSFxCs0cj')