import os

from flask import Flask, send_from_directory, session
from flask_migrate import Migrate

from packages.models import Organization, User, db
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

    @app.get("/static/image_uploads/<path:filename>")
    def uploaded_campaign_image(filename):
        upload_folder = os.path.join(app.root_path, "..", "static", "image_uploads")
        return send_from_directory(upload_folder, filename)

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
        organization = (
            Organization.query.filter_by(owner_user_id=account.id, deleted_at=None).first()
            if account
            else None
        )
        return {
            "signed_in_account": account,
            "signed_in_organization": organization,
        }

    db.init_app(app)

    app.register_blueprint(user)
    app.register_blueprint(admin, url_prefix="/admin")
    app.register_blueprint(campaign, url_prefix="/campaigns")
    app.register_blueprint(finance, url_prefix="/finance")
    # app.register_blueprint(finance, url_prefix="/financial", name="financial")

    migrate = Migrate(app, db)

    return app


app = create_app()
