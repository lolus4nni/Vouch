"""User-facing pages: landing, auth, dashboard, profile, settings.

No database/sessions yet: every GET just renders its template and every
POST just redirects to the next page in the flow.
"""
from flask import Blueprint, redirect, render_template, request, url_for


user = Blueprint("user", __name__, template_folder="templates", static_folder="static", static_url_path="/users/static")


# ---------- Public pages ----------

@user.route("/landing")
@user.route("/")
def landing_page():
    return render_template("users/landing.html")


@user.route("/how_it_works")
def how_it_works():
    return render_template("users/how_it_works.html")


@user.route("/public_profile")
def public_profile():
    return render_template("users/public_profile.html")


@user.route("/pages")
def page_library():
    return render_template("index.html")


# ---------- Auth ----------

@user.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        return redirect(url_for("user.welcome"))
    return render_template("users/register.html")


@user.route("/welcome")
def welcome():
    return render_template("users/welcome.html")


@user.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        return redirect(url_for("user.dashboard"))
    return render_template("users/login.html")


@user.route("/forgot_password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        return redirect(url_for("user.reset_password"))
    return render_template("users/forgot_password.html")


@user.route("/reset_password", methods=["GET", "POST"])
def reset_password():
    if request.method == "POST":
        return redirect(url_for("user.login"))
    return render_template("users/reset_password.html")


@user.route("/logout", methods=["GET", "POST"])
@user.route("/users/logout", methods=["GET", "POST"])
def logout():
    return redirect(url_for("user.landing_page"))


# ---------- Member area ----------

@user.route("/dashboard")
def dashboard():
    return render_template("users/dashboard.html")


@user.route("/profile_edit", methods=["GET", "POST"])
def profile_edit():
    if request.method == "POST":
        return redirect(url_for("user.public_profile"))
    return render_template("users/profile_edit.html")


@user.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        return redirect(url_for("user.settings"))
    return render_template("users/settings.html")


@user.route("/verification", methods=["GET", "POST"])
def verification():
    if request.method == "POST":
        return redirect(url_for("user.dashboard"))
    return render_template("users/verification.html")

