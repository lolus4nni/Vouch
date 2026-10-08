from flask import Blueprint, render_template, redirect, request, url_for

user = Blueprint('user', __name__, template_folder='templates', static_folder= 'static')

@user.route('/')
def landing_page():
    return render_template('users/index.html')