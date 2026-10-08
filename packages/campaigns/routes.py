from flask import Blueprint, render_template, redirect, request, url_for

campaign = Blueprint('campaign', __name__, template_folder='templates', static_folder= 'static')

@campaign.route('/')
def show_campaigns():
    return render_template('campaigns/explore.html')