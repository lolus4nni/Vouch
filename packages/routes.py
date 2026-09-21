from flask import render_template, request, redirect, flash
from packages import app

@app.after_request
def after_req(resp):
    resp.headers['Cache-Control'] = 'no-cache,no-store,must-revalidate'
    return resp

# @app.before_request
# def before_req(resp):


@app.route('/')
def home():
    return 'This is the homepage'