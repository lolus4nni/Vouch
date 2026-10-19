import os

from flask import Flask, session
from flask_migrate import Migrate

from packages.models import User, db
from packages.config import DevelopmentConfig

from dotenv import load_dotenv

from packages.admin.routes import admin
from packages.campaigns.routes import campaign
from packages.financial.routes import finance
from packages.users.routes import user


migrate = Migrate()


def create_app(config_object=None):
    app = Flask(__name__)
    app.config.from_object(config_object or DevelopmentConfig)

    @app.context_processor
    def inject_signed_in_account():
        user_id = (
            session.get("admin_user_id")
            if session.get("admin_logged_in")
            else session.get("user_id")
        )
        account = db.session.get(User, user_id) if user_id else None
        if account and (account.account_status != "active" or account.deleted_at is not None):
            account = None
        return {"signed_in_account": account}

    db.init_app(app)

    app.register_blueprint(user)
    app.register_blueprint(admin, url_prefix="/admin")
    app.register_blueprint(campaign, url_prefix="/campaigns")
    app.register_blueprint(finance, url_prefix="/finance")
    # app.register_blueprint(finance, url_prefix="/financial", name="financial")

    migrate = Migrate(app, db)

    return app


app = create_app()
