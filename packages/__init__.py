import os

from flask import Flask
from flask_migrate import Migrate

from packages.models import db
from packages.config import DevelopmentConfig

from dotenv import load_dotenv

from packages.admin.routes import admin
from packages.campaigns.routes import campaign
from packages.financial.routes import finance
from packages.users.routes import user


migrate = Migrate()


def create_app():
    app = Flask(__name__)

    app.config.from_object(DevelopmentConfig)


    db.init_app(app)

    app.register_blueprint(user)
    app.register_blueprint(admin, url_prefix="/admin")
    app.register_blueprint(campaign, url_prefix="/campaigns")
    app.register_blueprint(finance, url_prefix="/finance")
    # app.register_blueprint(finance, url_prefix="/financial", name="financial")

    migrate = Migrate(app, db)

    return app


app = create_app()
