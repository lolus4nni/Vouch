from flask import Flask
from flask_migrate import Migrate
from packages.admin.routes import admin
from packages.users.routes import user
from packages.campaigns.routes import campaign
from packages.financial.routes import finance

def create_app():
    # from package.models import db

    app = Flask(__name__)

    # app.config.from_object(DevelopmentConfig)

    # app.register_blueprint(posts)
    app.register_blueprint(user)
    app.register_blueprint(admin, url_prefix= '/admin')
    app.register_blueprint(campaign, url_prefix= '/campaigns')
    app.register_blueprint(finance, url_prefix= '/finance')
    # app.register_blueprint(api, url_prefix= '/api/v1')

    # db.init_app(app)

    # migrate = Migrate(app, db)

    return app

app = create_app()