"""Admin console: review queues, payouts, monitoring, accounts, categories.

No database / auth yet: GET renders the template, POST redirects back to the
relevant queue or list.
"""
from flask import Blueprint, redirect, render_template, request, url_for


admin = Blueprint("admin", __name__, template_folder="templates", static_folder="static")


@admin.route("/")
@admin.route("/dashboard.html")
def dashboard():
    return render_template("admin/dashboard.html")


# ---------- Campaign reviews ----------

@admin.route("/campaign_queue")
def campaign_queue():
    return render_template("admin/campaign_queue.html")


@admin.route("/campaign_review", methods=["GET", "POST"])
def campaign_review():
    if request.method == "POST":
        return redirect(url_for("admin.campaign_queue"))
    return render_template("admin/campaign_review.html")


# ---------- Expense reviews ----------

@admin.route("/expense_queue")
def expense_queue():
    return render_template("admin/expense_queue.html")


@admin.route("/expense_review", methods=["GET", "POST"])
def expense_review():
    if request.method == "POST":
        return redirect(url_for("admin.expense_queue"))
    return render_template("admin/expense_review.html")


# ---------- Identity checks ----------

@admin.route("/profile_queue")
def profile_queue():
    return render_template("admin/profile_queue.html")


@admin.route("/profile_review", methods=["GET", "POST"])
def profile_review():
    if request.method == "POST":
        return redirect(url_for("admin.profile_queue"))
    return render_template("admin/profile_review.html")


# ---------- Money ----------

@admin.route("/disbursals", methods=["GET", "POST"])
def disbursals():
    if request.method == "POST":
        return redirect(url_for("admin.disbursals"))
    return render_template("admin/disbursals.html")


@admin.route("/donations")
def donations():
    return render_template("admin/donations.html")


# ---------- Platform ----------

@admin.route("/accounts", methods=["GET", "POST"])
def accounts():
    # GET = search/filter form, POST = deactivate account.
    if request.method == "POST":
        return redirect(url_for("admin.accounts"))
    return render_template("admin/accounts.html")


@admin.route("/categories", methods=["GET", "POST"])
def categories():
    if request.method == "POST":
        return redirect(url_for("admin.categories"))
    return render_template("admin/categories.html")


@admin.route("/verification_logs")
def verification_logs():
    return render_template("admin/verification_logs.html")
