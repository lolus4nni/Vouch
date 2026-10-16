from flask import Blueprint, render_template, request, redirect, url_for, session, jsonify
from werkzeug.utils import secure_filename
# from packages.models import db, Post, Item, Payment, User
import secrets, os, requests
from dotenv import load_dotenv

load_dotenv()



finance = Blueprint("finance", __name__, template_folder="templates", static_folder="static")



@finance.get("/")
def finance_home():
    return render_template("financial/ledger.html")


