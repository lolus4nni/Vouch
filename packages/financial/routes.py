"""Money pages: donate flow, receipts, public ledger, expenses and payouts.

No database / payment gateway yet: GET renders the template, POST redirects.
"""
from flask import Blueprint, redirect, render_template, request, url_for


finance = Blueprint("finance", __name__, template_folder="templates", static_folder="static")


# ---------- Donations ----------

@finance.route("/donate", methods=["GET", "POST"])
def donate():
    if request.method == "POST":
        # TODO(payments): initialise the Paystack transaction here.
        return redirect(url_for("finance.donation_success"))
    return render_template("financial/donate.html")


@finance.route("/donation_success")
def donation_success():
    return render_template("financial/donation_success.html")


@finance.route("/donation_failed")
def donation_failed():
    return render_template("financial/donation_failed.html")


@finance.route("/receipt")
def receipt():
    return render_template("financial/receipt.html")


@finance.route("/my_donations")
def my_donations():
    return render_template("financial/my_donations.html")


# ---------- Transparency ----------

@finance.route("/")
@finance.route("/ledger")
def ledger():
    return render_template("financial/ledger.html")


# ---------- Expenses & payouts ----------

@finance.route("/expense_new", methods=["GET", "POST"])
def expense_new():
    if request.method == "POST":
        return redirect(url_for("finance.expense_detail"))
    return render_template("financial/expense_new.html")


@finance.route("/expense_detail")
def expense_detail():
    return render_template("financial/expense_detail.html")


@finance.route("/my_payouts")
def my_payouts():
    return render_template("financial/my_payouts.html")
