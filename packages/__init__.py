import os

from flask import Flask
from flask_migrate import Migrate

from packages.db import db

from packages.admin.routes import admin
from packages.campaigns.routes import campaign
from packages.financial.routes import finance
from packages.users.routes import user


migrate = Migrate()


def create_app(test_config=None):
    app = Flask(__name__)
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    if test_config:
        app.config.update(test_config)

    database_url = app.config.get("SQLALCHEMY_DATABASE_URI") or os.environ.get("DATABASE_URL")
    if not database_url:
        if os.environ.get("APP_ENV", "development").lower() == "production":
            raise RuntimeError("DATABASE_URL must be set in production")
        database_url = "sqlite:///vouch-dev.db"
    elif database_url.startswith("mysql://"):
        database_url = database_url.replace("mysql://", "mysql+pymysql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url

    db.init_app(app)
    migrate.init_app(app, db)

    app.register_blueprint(user)
    app.register_blueprint(admin, url_prefix="/admin")
    app.register_blueprint(campaign, url_prefix="/campaigns")
    app.register_blueprint(finance, url_prefix="/finance")
    app.register_blueprint(finance, url_prefix="/financial", name="financial")

    return app


app = create_app()
