class GeneralConfig(object):
    APP_NAME = 'Vouch'
    SECRET_KEY = 'notsecured'

class DevelopmentConfig(GeneralConfig):
    SECRET_KEY = 'f1cfe0cf4bee155444a85aa08f1ce2bf8b99538d18f9b16710335493e784f83f24fb848a'
    SQLALCHEMY_DATABASE_URI = 'mysql+pymysql://root@localhost/vouch'
    SQLALCHEMY_TRACK_MODIFICATIONS = False