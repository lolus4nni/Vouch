from flask import Blueprint, render_template, redirect, request, url_for

finance = Blueprint('finance', __name__, template_folder='templates', static_folder= 'static')

