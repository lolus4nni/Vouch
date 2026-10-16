from flask import Flask

from packages.admin.routes import admin
from packages.campaigns.routes import campaign
from packages.financial.routes import finance
from packages.users.routes import user


def create_app(test_config=None):
    app = Flask(__name__)
    if test_config:
        app.config.update(test_config)

    app.register_blueprint(user)
    app.register_blueprint(admin, url_prefix="/admin")
    app.register_blueprint(campaign, url_prefix="/campaigns")
    app.register_blueprint(finance, url_prefix="/finance")
    app.register_blueprint(finance, url_prefix="/financial", name="financial")

    return app


app = create_app()
