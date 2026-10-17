"""Campaign pages: explore, detail, create/edit, manage, evidence, updates, reports.

No database yet: GET renders the template, POST redirects to the next page.
"""
from flask import Blueprint, redirect, render_template, request, url_for


campaign = Blueprint("campaign", __name__, template_folder="templates", static_folder="static")


# ---------- Public ----------

@campaign.route("/")
@campaign.route("/explore")
def explore():
    # The search/filter forms on this page submit with GET to this same URL.
    return render_template("campaigns/explore.html")


@campaign.route("/detail")
def detail():
    return render_template("campaigns/detail.html")


@campaign.route("/report", methods=["GET", "POST"])
def report():
    if request.method == "POST":
        return redirect(url_for("campaign.detail"))
    return render_template("campaigns/report.html")


# ---------- Campaigner ----------

@campaign.route("/create", methods=["GET", "POST"])
def create():
    if request.method == "POST":
        return redirect(url_for("campaign.submitted"))
    return render_template("campaigns/create.html")


@campaign.route("/submitted")
def submitted():
    return render_template("campaigns/submitted.html")


@campaign.route("/my_campaigns")
def my_campaigns():
    return render_template("campaigns/my_campaigns.html")


@campaign.route("/manage")
def manage():
    return render_template("campaigns/manage.html")


@campaign.route("/edit", methods=["GET", "POST"])
def edit():
    if request.method == "POST":
        return redirect(url_for("campaign.manage"))
    return render_template("campaigns/edit.html")


@campaign.route("/evidence", methods=["GET", "POST"])
def evidence():
    # Handles both "upload document" and "remove document" forms.
    if request.method == "POST":
        return redirect(url_for("campaign.evidence"))
    return render_template("campaigns/evidence.html")


@campaign.route("/post_update", methods=["GET", "POST"])
def post_update():
    if request.method == "POST":
        return redirect(url_for("campaign.detail"))
    return render_template("campaigns/post_update.html")
