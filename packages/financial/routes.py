"""Money pages: pending donation records, receipts, public ledgers and expenses."""
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timezone

import requests
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError

from packages.models import (
    Campaign,
    CampaignRevision,
    Currency,
    Disbursement,
    Donation,
    ExpenseRequest,
    ExpenseReview,
    Organization,
    PaymentAttempt,
    PaymentProviderEvent,
    PayoutRecipient,
    User,
    db,
)


finance = Blueprint("finance", __name__, template_folder="templates", static_folder="static")


@finance.before_request
def _protect_finance_posts():
    if request.method == "POST" and request.endpoint != "finance.paystack_webhook":
        expected = session.get("_finance_csrf_token")
        submitted = request.form.get("csrf_token", "")
        if not expected or not secrets.compare_digest(
            expected.encode("utf-8"),
            submitted.encode("utf-8"),
        ):
            abort(400)


def _csrf_token():
    if "_finance_csrf_token" not in session:
        session["_finance_csrf_token"] = secrets.token_urlsafe(32)
    return session["_finance_csrf_token"]


def _paystack_settings():
    secret_key = current_app.config.get("PAYSTACK_SECRET_KEY", "").strip()
    callback_url = current_app.config.get("PAYSTACK_CALLBACK_URL", "").strip()
    return secret_key if secret_key.startswith("sk_test_") else "", callback_url


def _verify_paystack_transaction(reference, secret_key):
    response = requests.get(
        f"https://api.paystack.co/transaction/verify/{reference}",
        headers={"Authorization": f"Bearer {secret_key}"},
        timeout=15,
    )
    response.raise_for_status()
    result = response.json()
    transaction = result.get("data") if isinstance(result, dict) else None
    if (
        not isinstance(result, dict)
        or result.get("status") is not True
        or not isinstance(transaction, dict)
        or transaction.get("reference") != reference
    ):
        current_app.logger.error("Paystack returned an invalid verification response for reference %s", reference)
        abort(502)
    return transaction


def _record_successful_payment(attempt, transaction):
    if (
        transaction.get("status") != "success"
        or transaction.get("amount") != attempt.charge_amount_minor
        or transaction.get("currency") != attempt.charge_currency_code
    ):
        current_app.logger.error(
            "Paystack status, amount, or currency mismatch for reference %s",
            attempt.provider_reference,
        )
        abort(502)

    if attempt.status == "succeeded":
        return

    donation = attempt.donation
    now = datetime.now(timezone.utc)
    donation.status = "succeeded"
    donation.credited_amount_minor = donation.intended_amount_minor
    donation.succeeded_at = now
    attempt.status = "succeeded"
    attempt.settled_amount_minor = transaction["amount"]
    attempt.settled_currency_code = transaction["currency"]
    attempt.campaign_credit_amount_minor = donation.intended_amount_minor
    attempt.campaign_currency_code = donation.campaign_currency_code
    attempt.provider_fee_minor = transaction.get("fees")
    attempt.provider_fee_currency_code = (
        transaction["currency"] if transaction.get("fees") is not None else None
    )
    attempt.confirmed_at = now


def _signed_in_user():
    user_id = session.get("user_id")
    account = db.session.get(User, user_id) if user_id else None
    if account and (account.account_status != "active" or account.deleted_at is not None):
        return None
    return account


def _latest_revision(campaign_id, status=None):
    query = CampaignRevision.query.filter_by(campaign_id=campaign_id)
    if status:
        query = query.filter_by(status=status)
    return query.order_by(CampaignRevision.version_number.desc()).first()


def _active_campaign(campaign_id):
    campaign_record = Campaign.query.filter_by(
        id=campaign_id,
        status="active",
        deleted_at=None,
    ).first()
    if campaign_record is None:
        return None, None
    return campaign_record, _latest_revision(campaign_record.id, "approved")


def _owned_campaign(campaign_id, user_id):
    campaign_record = Campaign.query.filter_by(
        id=campaign_id,
        deleted_at=None,
    ).first()
    if campaign_record is None:
        return None
    if campaign_record.owner_user_id == user_id:
        return campaign_record
    organization = campaign_record.owner_organization
    if organization and organization.owner_user_id == user_id and organization.deleted_at is None:
        return campaign_record
    return None


def _campaign_money(campaign_id):
    raised_minor = db.session.query(
        func.coalesce(func.sum(Donation.credited_amount_minor), 0)
    ).filter(
        Donation.campaign_id == campaign_id,
        Donation.status == "succeeded",
    ).scalar() or 0
    paid_minor = db.session.query(
        func.coalesce(func.sum(Disbursement.amount_minor), 0)
    ).join(
        ExpenseRequest,
        ExpenseRequest.id == Disbursement.expense_request_id,
    ).filter(
        ExpenseRequest.campaign_id == campaign_id,
        Disbursement.status == "paid",
    ).scalar() or 0
    reserved_minor = db.session.query(
        func.coalesce(func.sum(ExpenseRequest.amount_minor), 0)
    ).filter(
        ExpenseRequest.campaign_id == campaign_id,
        ExpenseRequest.status.in_(
            ["submitted", "in_review", "changes_requested", "approved"]
        ),
    ).scalar() or 0
    return int(raised_minor), int(paid_minor), int(reserved_minor)


def _owner_campaign_ids(user_id):
    organization_ids = db.session.query(Organization.id).filter(
        Organization.owner_user_id == user_id,
        Organization.deleted_at.is_(None),
    )
    return db.session.query(Campaign.id).filter(
        Campaign.deleted_at.is_(None),
        or_(
            Campaign.owner_user_id == user_id,
            Campaign.owner_organization_id.in_(organization_ids),
        ),
    )


# ---------- Donations ----------

@finance.route("/donate", methods=["GET", "POST"])
def donate():
    campaign_id = request.values.get("campaign_id", type=int)
    campaign_record, revision = _active_campaign(campaign_id) if campaign_id else (None, None)
    if campaign_record is None or revision is None:
        flash("Choose an active campaign before donating.", "error")
        return redirect(url_for("campaign.explore"))

    account = _signed_in_user()
    currency = revision.currency
    amount = request.form.get("amount", type=int) if request.method == "POST" else None
    guest_email = request.form.get("guest_email", "").strip().lower()
    is_anonymous = request.form.get("is_anonymous") == "1"
    secret_key, callback_url = _paystack_settings()
    paystack_ready = bool(secret_key and callback_url)
    raised_minor, _, _ = _campaign_money(campaign_record.id)
    if raised_minor >= revision.goal_amount_minor:
        flash("This campaign has reached its target and is no longer accepting donations.", "info")
        return redirect(url_for("campaign.detail", slug=campaign_record.slug))
    progress = min(100, round(raised_minor * 100 / revision.goal_amount_minor))
    context = {
        "campaign": campaign_record,
        "revision": revision,
        "currency": currency,
        "raised_minor": raised_minor,
        "net_goal_minor": revision.goal_amount_minor * 10 // 11,
        "platform_fee_minor": revision.goal_amount_minor - revision.goal_amount_minor * 10 // 11,
        "progress": progress,
        "amount": amount,
        "guest_email": guest_email,
        "is_anonymous": is_anonymous,
        "account": account,
        "csrf_token": _csrf_token(),
        "paystack_ready": paystack_ready and revision.currency_code == "NGN",
    }

    if request.method == "POST":
        if amount is None or amount < 500:
            flash("Enter a donation amount of at least 500.", "error")
            return render_template("financial/donate.html", **context), 400
        if account is None and ("@" not in guest_email or "." not in guest_email.rsplit("@", 1)[-1]):
            flash("Enter a valid email address for your receipt.", "error")
            return render_template("financial/donate.html", **context), 400
        if currency is None:
            flash("This campaign's currency is not configured.", "error")
            return render_template("financial/donate.html", **context), 503
        if revision.currency_code != "NGN":
            flash("Paystack test checkout is currently available for NGN campaigns only.", "error")
            return render_template("financial/donate.html", **context), 400
        if not secret_key or not callback_url:
            flash("Paystack test checkout is not configured. No payment was started.", "error")
            return render_template("financial/donate.html", **context), 503
        donor_email = account.email if account else guest_email
        if not donor_email:
            flash("Add an email address to your account before donating.", "error")
            return render_template("financial/donate.html", **context), 400

        donation = Donation(
            receipt_id=secrets.token_hex(16),
            campaign_id=campaign_record.id,
            donor_user_id=account.id if account else None,
            guest_email=None if account else guest_email,
            is_anonymous=is_anonymous,
            intended_amount_minor=amount * (10 ** currency.minor_unit_exponent),
            campaign_currency_code=revision.currency_code,
            status="pending",
        )
        db.session.add(donation)
        db.session.flush()
        reference = secrets.token_hex(16)
        attempt = PaymentAttempt(
            donation_id=donation.id,
            idempotency_key=reference,
            provider_reference=reference,
            status="initialized",
            charge_amount_minor=donation.intended_amount_minor,
            charge_currency_code="NGN",
        )
        db.session.add(attempt)
        db.session.commit()

        try:
            response = requests.post(
                "https://api.paystack.co/transaction/initialize",
                json={
                    "email": donor_email,
                    "amount": donation.intended_amount_minor,
                    "currency": "NGN",
                    "reference": reference,
                    "callback_url": callback_url,
                    "metadata": {"donation_receipt_id": donation.receipt_id},
                },
                headers={"Authorization": f"Bearer {secret_key}"},
                timeout=15,
            )
            response.raise_for_status()
            result = response.json()
        except requests.exceptions.RequestException:
            attempt.status = "failed"
            donation.status = "failed"
            db.session.commit()
            flash("Paystack checkout could not be started. Please try again.", "error")
            return render_template("financial/donate.html", **context), 502

        checkout = result.get("data") if isinstance(result, dict) else None
        authorization_url = checkout.get("authorization_url") if isinstance(checkout, dict) else None
        if (
            not isinstance(result, dict)
            or result.get("status") is not True
            or not isinstance(checkout, dict)
            or checkout.get("reference") != reference
            or not isinstance(authorization_url, str)
            or not authorization_url.startswith("https://")
        ):
            attempt.status = "failed"
            donation.status = "failed"
            db.session.commit()
            flash("Paystack did not create a checkout session. Please try again.", "error")
            return render_template("financial/donate.html", **context), 502

        attempt.status = "pending"
        db.session.commit()
        return redirect(authorization_url)

    return render_template("financial/donate.html", **context)


@finance.route("/donation_success")
def donation_success():
    reference = request.args.get("reference", "").strip()
    attempt = PaymentAttempt.query.filter_by(
        provider="paystack",
        provider_reference=reference,
    ).first() if reference else None
    if attempt is None:
        flash("We could not match this Paystack transaction to a donation.", "error")
        return redirect(url_for("campaign.explore"))

    donation = attempt.donation
    if attempt.status == "succeeded":
        return redirect(url_for("finance.receipt", receipt_id=donation.receipt_id))

    secret_key, _ = _paystack_settings()
    if not secret_key:
        flash("Paystack verification is not configured. The donation remains pending.", "error")
        return redirect(url_for("finance.receipt", receipt_id=donation.receipt_id))

    try:
        transaction = _verify_paystack_transaction(reference, secret_key)
    except requests.exceptions.RequestException:
        flash("Paystack could not verify the transaction yet. The donation remains pending.", "error")
        return redirect(url_for("finance.receipt", receipt_id=donation.receipt_id))

    if transaction.get("status") == "success":
        _record_successful_payment(attempt, transaction)
    elif transaction.get("status") in {"failed", "abandoned", "reversed"}:
        donation.status = "failed"
        attempt.status = "failed"

    db.session.commit()
    if donation.status == "succeeded":
        flash("Your Paystack payment was verified. Thank you for supporting this campaign.", "success")
    elif donation.status == "failed":
        flash("The Paystack payment did not complete. You have not been credited for this donation.", "error")
    else:
        flash("Paystack is still processing this payment. Check back shortly.", "info")
    return redirect(url_for("finance.receipt", receipt_id=donation.receipt_id))


@finance.route("/paystack_webhook", methods=["POST"])
def paystack_webhook():
    secret_key, _ = _paystack_settings()
    if not secret_key:
        abort(503)

    raw_payload = request.get_data()
    signature = request.headers.get("x-paystack-signature", "")
    expected_signature = hmac.new(
        secret_key.encode("utf-8"),
        raw_payload,
        hashlib.sha512,
    ).hexdigest()
    if not signature or not hmac.compare_digest(signature, expected_signature):
        abort(401)

    try:
        payload = json.loads(raw_payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        abort(400)
    if not isinstance(payload, dict):
        abort(400)
    if payload.get("event") != "charge.success":
        return "", 200

    data = payload.get("data")
    if not isinstance(data, dict):
        abort(400)
    reference = data.get("reference")
    event_id = data.get("id")
    if not isinstance(reference, str) or not reference or event_id is None:
        abort(400)
    provider_event_id = f"charge.success:{event_id}"
    payment_event = PaymentProviderEvent.query.filter_by(
        provider="paystack",
        provider_event_id=provider_event_id,
    ).first()
    if payment_event and payment_event.processing_status == "processed":
        return "", 200

    attempt = PaymentAttempt.query.filter_by(
        provider="paystack",
        provider_reference=reference,
    ).first()
    if attempt is None:
        current_app.logger.error("Paystack webhook reference %s has no matching payment attempt", reference)
        abort(404)

    if payment_event is None:
        payment_event = PaymentProviderEvent(
            payment_attempt_id=attempt.id,
            provider="paystack",
            provider_event_id=provider_event_id,
            event_type="charge.success",
            payload_sha256=hashlib.sha256(raw_payload).hexdigest(),
            processing_status="received",
        )
        db.session.add(payment_event)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return "", 200

    try:
        transaction = _verify_paystack_transaction(reference, secret_key)
    except requests.exceptions.RequestException:
        current_app.logger.exception("Could not verify Paystack webhook reference %s", reference)
        abort(502)

    _record_successful_payment(attempt, transaction)
    payment_event.processing_status = "processed"
    payment_event.processed_at = datetime.now(timezone.utc)
    db.session.commit()
    return "", 200


@finance.route("/donation_failed")
def donation_failed():
    return render_template("financial/donation_failed.html")


@finance.route("/receipt")
def receipt():
    receipt_id = request.args.get("receipt_id", "").strip()
    donation = Donation.query.filter_by(receipt_id=receipt_id).first() if receipt_id else None
    if donation is None:
        flash("Choose a donation to view its receipt.", "error")
        return redirect(url_for("finance.my_donations") if _signed_in_user() else url_for("campaign.explore"))
    account = _signed_in_user()
    if donation.donor_user_id and (account is None or donation.donor_user_id != account.id):
        abort(404)
    revision = _latest_revision(donation.campaign_id, "approved")
    attempt = PaymentAttempt.query.filter_by(
        donation_id=donation.id,
    ).order_by(PaymentAttempt.created_at.desc()).first()
    return render_template(
        "financial/receipt.html",
        donation=donation,
        revision=revision,
        attempt=attempt,
    )


@finance.route("/my_donations")
def my_donations():
    account = _signed_in_user()
    if account is None:
        return redirect(url_for("user.login", next=request.full_path))
    donations = Donation.query.filter_by(
        donor_user_id=account.id,
    ).order_by(Donation.created_at.desc()).limit(100).all()
    totals_by_currency = db.session.query(
        Donation.campaign_currency_code,
        Currency.minor_unit_exponent,
        func.coalesce(func.sum(Donation.credited_amount_minor), 0),
    ).join(
        Currency,
        Currency.code == Donation.campaign_currency_code,
    ).filter(
        Donation.donor_user_id == account.id,
        Donation.status == "succeeded",
    ).group_by(Donation.campaign_currency_code, Currency.minor_unit_exponent).all()
    donation_rows = [
        {"donation": donation, "revision": _latest_revision(donation.campaign_id, "approved")}
        for donation in donations
    ]
    donation_count = Donation.query.filter_by(donor_user_id=account.id).count()
    supported_campaign_count = db.session.query(
        func.count(func.distinct(Donation.campaign_id))
    ).filter(
        Donation.donor_user_id == account.id,
    ).scalar() or 0
    return render_template(
        "financial/my_donations.html",
        donation_rows=donation_rows,
        totals_by_currency=totals_by_currency,
        donation_count=donation_count,
        supported_campaign_count=int(supported_campaign_count),
    )


# ---------- Transparency ----------

@finance.route("/")
@finance.route("/ledger")
def ledger():
    campaign_id = request.args.get("campaign_id", type=int)
    campaign_record, revision = _active_campaign(campaign_id) if campaign_id else (None, None)
    if campaign_record is None or revision is None:
        flash("Choose an active campaign to view its public ledger.", "error")
        return redirect(url_for("campaign.explore"))

    donations = Donation.query.filter_by(
        campaign_id=campaign_record.id,
        status="succeeded",
    ).order_by(Donation.created_at.desc()).limit(100).all()
    disbursements = Disbursement.query.join(
        ExpenseRequest,
        ExpenseRequest.id == Disbursement.expense_request_id,
    ).filter(
        ExpenseRequest.campaign_id == campaign_record.id,
        Disbursement.status == "paid",
    ).order_by(Disbursement.paid_at.desc()).limit(100).all()
    gross_minor = db.session.query(
        func.coalesce(func.sum(Donation.intended_amount_minor), 0)
    ).filter(
        Donation.campaign_id == campaign_record.id,
        Donation.status == "succeeded",
    ).scalar() or 0
    credited_minor = db.session.query(
        func.coalesce(func.sum(Donation.credited_amount_minor), 0)
    ).filter(
        Donation.campaign_id == campaign_record.id,
        Donation.status == "succeeded",
    ).scalar() or 0
    _, paid_total_minor, reserved_minor = _campaign_money(campaign_record.id)
    return render_template(
        "financial/ledger.html",
        campaign=campaign_record,
        revision=revision,
        donations=donations,
        disbursements=disbursements,
        gross_minor=gross_minor,
        credited_minor=credited_minor,
        difference_minor=max(gross_minor - credited_minor, 0),
        paid_minor=paid_total_minor,
        available_minor=max(
            min(credited_minor, revision.goal_amount_minor * 10 // 11)
            - paid_total_minor
            - reserved_minor,
            0,
        ),
        progress=min(100, round(credited_minor * 100 / revision.goal_amount_minor)),
    )


# ---------- Expenses & payouts ----------

@finance.route("/expense_new", methods=["GET", "POST"])
def expense_new():
    account = _signed_in_user()
    if account is None:
        return redirect(url_for("user.login", next=request.full_path))
    campaign_id = request.values.get("campaign_id", type=int)
    campaign_record = _owned_campaign(campaign_id, account.id) if campaign_id else None
    if campaign_record is None:
        flash("Choose one of your campaigns before requesting an expense.", "error")
        return redirect(url_for("campaign.my_campaigns"))
    revision = _latest_revision(campaign_record.id, "approved") or _latest_revision(campaign_record.id)
    if revision is None:
        flash("This campaign has no saved details yet.", "error")
        return redirect(url_for("campaign.my_campaigns"))
    currency = revision.currency
    recipients = PayoutRecipient.query.filter_by(
        created_by_user_id=account.id,
        status="active",
        deleted_at=None,
    ).order_by(PayoutRecipient.display_name).all()
    raised_minor, paid_minor, reserved_minor = _campaign_money(campaign_record.id)
    available_minor = max(
        min(raised_minor, revision.goal_amount_minor * 10 // 11)
        - paid_minor
        - reserved_minor,
        0,
    )
    amount_factor = 10 ** currency.minor_unit_exponent if currency else 100
    maximum_amount = available_minor // amount_factor
    form_values = {
        "title": request.form.get("title", "").strip(),
        "description": request.form.get("description", "").strip(),
        "amount": request.form.get("amount", "").strip(),
        "recipient_id": request.form.get("recipient_id", "").strip(),
    }
    context = {
        "campaign": campaign_record,
        "revision": revision,
        "currency": currency,
        "recipients": recipients,
        "raised_minor": raised_minor,
        "paid_minor": paid_minor,
        "available_minor": available_minor,
        "maximum_amount": maximum_amount,
        "form_values": form_values,
        "csrf_token": _csrf_token(),
    }

    if request.method == "POST":
        uploaded_file = request.files.get("file")
        if uploaded_file and uploaded_file.filename:
            flash("Supporting document uploads are unavailable until secure file storage is configured.", "error")
            return render_template("financial/expense_new.html", **context), 503
        amount = request.form.get("amount", type=int)
        recipient_id = request.form.get("recipient_id", type=int)
        recipient = next(
            (item for item in recipients if item.id == recipient_id),
            None,
        )
        if (
            not form_values["title"]
            or len(form_values["title"]) > 150
            or not form_values["description"]
            or amount is None
            or amount < 1
            or amount > maximum_amount
            or recipient is None
            or currency is None
        ):
            flash("Enter valid expense details, choose one of your secure recipients, and stay within the available balance.", "error")
            return render_template("financial/expense_new.html", **context), 400

        expense = ExpenseRequest(
            campaign_id=campaign_record.id,
            submitted_by_user_id=account.id,
            recipient_id=recipient.id,
            title=form_values["title"],
            description=form_values["description"],
            amount_minor=amount * amount_factor,
            currency_code=revision.currency_code,
            status="submitted",
        )
        db.session.add(expense)
        db.session.commit()
        flash("Your expense request was saved for review. No payout has been sent.", "success")
        return redirect(url_for("finance.expense_detail", expense_id=expense.id))

    return render_template("financial/expense_new.html", **context)


@finance.route("/expense_detail")
def expense_detail():
    account = _signed_in_user()
    if account is None:
        return redirect(url_for("user.login", next=request.full_path))
    expense_id = request.args.get("expense_id", type=int)
    expense = db.session.get(ExpenseRequest, expense_id) if expense_id else None
    if expense is None or _owned_campaign(expense.campaign_id, account.id) is None:
        abort(404)
    latest_review = ExpenseReview.query.filter_by(
        expense_request_id=expense.id,
    ).order_by(ExpenseReview.decided_at.desc()).first()
    return render_template(
        "financial/expense_detail.html",
        expense=expense,
        latest_review=latest_review,
    )


@finance.route("/my_payouts")
def my_payouts():
    account = _signed_in_user()
    if account is None:
        return redirect(url_for("user.login", next=request.full_path))
    campaign_ids = _owner_campaign_ids(account.id)
    expenses = ExpenseRequest.query.join(
        Campaign,
        Campaign.id == ExpenseRequest.campaign_id,
    ).filter(
        Campaign.id.in_(campaign_ids),
    ).order_by(ExpenseRequest.submitted_at.desc()).all()
    expense_rows = [
        {"expense": expense, "revision": _latest_revision(expense.campaign_id, "approved")}
        for expense in expenses
    ]
    paid_count = sum(
        expense.disbursement is not None and expense.disbursement.status == "paid"
        for expense in expenses
    )
    approved_waiting_count = sum(
        expense.status == "approved"
        and (expense.disbursement is None or expense.disbursement.status != "paid")
        for expense in expenses
    )
    pending_count = sum(
        expense.status in {"submitted", "in_review", "changes_requested"}
        for expense in expenses
    )
    return render_template(
        "financial/my_payouts.html",
        expense_rows=expense_rows,
        request_count=len(expenses),
        paid_count=paid_count,
        approved_waiting_count=approved_waiting_count,
        pending_count=pending_count,
    )
