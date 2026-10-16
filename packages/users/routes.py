from flask import Blueprint, redirect, render_template, url_for


user = Blueprint("user", __name__, template_folder="templates", static_folder="static")



@user.route("/")
def landing_page():
    return render_template("users/landing.html")


@user.route("/pages")
def page_library():
    return render_template("index.html")


@user.route("/logout")
@user.route("/users/logout")
def logout():
    return redirect(url_for("user.landing_page"))



