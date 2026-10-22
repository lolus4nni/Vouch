import os


class GeneralConfig(object):
    APP_NAME = 'Vouch'
    SECRET_KEY = 'notsecured'
    ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "").strip().lower()
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
    PAYSTACK_SECRET_KEY = os.environ.get("PAYSTACK_SECRET_KEY", "")
    PAYSTACK_CALLBACK_URL = os.environ.get(
        "PAYSTACK_CALLBACK_URL",
        "http://127.0.0.1:8000/finance/donation_success",
    )

class DevelopmentConfig(GeneralConfig):
    SECRET_KEY = 'f1cfe0cf4bee155444a85aa08f1ce2bf8b99538d18f9b16710335493e784f83f24fb848a'
    SQLALCHEMY_DATABASE_URI = 'mysql+pymysql://root@localhost/vouch'
    SQLALCHEMY_TRACK_MODIFICATIONS = False