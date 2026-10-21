"""Admin console: review queues, payouts, monitoring, accounts, categories.

The admin login flag is stored in the same signed Flask session as member login.
"""
import re
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_from_directory, session, url_for
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash

from packages.models import (
    Campaign,
    CampaignReview,
    CampaignRevision,
    Category,
    Currency,
    Disbursement,
    Donation,
    ExpenseRequest,
    ExpenseReview,
    Organization,
    PaymentAttempt,
    PayoutRecipient,
    StaffRole,
    User,
    VerificationCase,
    VerificationDocument,
    VerificationReview,
    db,
)


admin = Blueprint("admin", __name__, template_folder="templates", static_folder="static")


def _admin_csrf_token():
    token = session.get("_admin_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_admin_csrf_token"] = token
    return token


def _format_minor_amount(amount_minor, exponent):
    amount_minor = amount_minor or 0
    return format(Decimal(amount_minor).scaleb(-exponent), f",.{exponent}f")


@admin.context_processor
def inject_admin_csrf_token():
    return {
        "admin_csrf_token": _admin_csrf_token(),
        "admin_minor_amount": _format_minor_amount,
    }


@admin.before_app_request
def require_admin_login():
    """Keep every admin page behind the simple login session."""
    if request.path != "/admin" and not request.path.startswith("/admin/"):
        return None
    if request.endpoint == "admin.static" or request.path == "/admin/login":
        return None
    admin_user = db.session.get(User, session.get("admin_user_id")) if session.get("admin_user_id") else None
    if (
        not session.get("admin_logged_in")
        or admin_user is None
        or admin_user.account_status != "active"
        or not StaffRole.query.filter_by(
            user_id=admin_user.id,
            role="admin",
            revoked_at=None,
        ).first()
    ):
        session.clear()
        return redirect(url_for("user.landing_page"))
    if request.method == "POST":
        expected_token = session.get("_admin_csrf_token", "")
        submitted_token = request.form.get("csrf_token", "")
        if not expected_token or not secrets.compare_digest(
            expected_token.encode("utf-8"),
            submitted_token.encode("utf-8"),
        ):
            abort(400)
    return None


@admin.route("/login", methods=["GET", "POST"])
def login():
    csrf_token = session.get("_admin_login_csrf")
    if not csrf_token:
        csrf_token = secrets.token_urlsafe(32)
        session["_admin_login_csrf"] = csrf_token

    if request.method == "POST":
        submitted_token = request.form.get("csrf_token", "")
        if not secrets.compare_digest(
            csrf_token.encode("utf-8"),
            submitted_token.encode("utf-8"),
        ):
            return render_template(
                "users/login.html",
                admin_login=True,
                csrf_token=csrf_token,
            ), 400

        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        admin_email = current_app.config.get("ADMIN_EMAIL", "").strip().lower()
        admin_password = current_app.config.get("ADMIN_PASSWORD", "")
        if (
            not admin_email
            or not admin_password
            or not secrets.compare_digest(email.encode("utf-8"), admin_email.encode("utf-8"))
            or not secrets.compare_digest(password.encode("utf-8"), admin_password.encode("utf-8"))
        ):
            flash("Email or password is incorrect.", "error")
            return render_template(
                "users/login.html",
                admin_login=True,
                csrf_token=csrf_token,
            ), 401

        admin_user = User.query.filter_by(email=admin_email).first()
        if admin_user is None:
            admin_user = User(
                email=admin_email,
                password_hash=generate_password_hash(admin_password),
                display_name="Vouch Admin",
            )
            db.session.add(admin_user)
            db.session.flush()
        elif admin_user.account_status != "active" or admin_user.deleted_at is not None:
            flash("This admin account cannot sign in. Contact support for help.", "error")
            return render_template(
                "users/login.html",
                admin_login=True,
                csrf_token=csrf_token,
            ), 403

        admin_role = StaffRole.query.filter_by(
            user_id=admin_user.id,
            role="admin",
            revoked_at=None,
        ).first()
        if admin_role is None:
            db.session.add(
                StaffRole(
                    user_id=admin_user.id,
                    role="admin",
                    granted_by_user_id=admin_user.id,
                )
            )
        db.session.commit()

        next_page = request.args.get("next", "")
        session.clear()
        session["admin_logged_in"] = True
        session["admin_user_id"] = admin_user.id
        if next_page == "/admin" or next_page.startswith("/admin/"):
            return redirect(next_page)
        return redirect(url_for("admin.dashboard"))

    return render_template(
        "users/login.html",
        admin_login=True,
        csrf_token=csrf_token,
    )


@admin.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("admin.login"))


@admin.route("/")
@admin.route("/dashboard.html")
def dashboard():
    waiting_campaigns = CampaignRevision.query.filter(
        CampaignRevision.status.in_(("submitted", "changes_requested"))
    ).order_by(CampaignRevision.submitted_at.asc()).limit(5).all()
    waiting_campaign_count = CampaignRevision.query.filter(
        CampaignRevision.status.in_(("submitted", "changes_requested"))
    ).count()
    recent_reviews = []
    for review in CampaignReview.query.order_by(CampaignReview.decided_at.desc()).limit(5):
        recent_reviews.append((review.decision, f"Campaign: {review.revision.title}", review.reviewer.display_name, review.decided_at))
    for review in ExpenseReview.query.order_by(ExpenseReview.decided_at.desc()).limit(5):
        recent_reviews.append((review.decision, f"Expense: {review.expense_request.title}", review.reviewer.display_name, review.decided_at))
    for review in VerificationReview.query.order_by(VerificationReview.decided_at.desc()).limit(5):
        subject = review.case.subject_user or review.case.subject_organization
        name = (
            getattr(subject, "display_name", None)
            or getattr(subject, "legal_name", None)
            or "Unknown subject"
        )
        recent_reviews.append((review.decision, f"Identity: {name}", review.reviewer.display_name, review.decided_at))

    since = datetime.now(timezone.utc) - timedelta(days=30)
    successful_donations = Donation.query.filter_by(status="succeeded")
    donation_totals = db.session.query(
        Donation.campaign_currency_code,
        Currency.minor_unit_exponent,
        db.func.sum(Donation.credited_amount_minor),
    ).join(
        Currency, Currency.code == Donation.campaign_currency_code
    ).filter(
        Donation.status == "succeeded",
        Donation.created_at >= since,
    ).group_by(
        Donation.campaign_currency_code,
        Currency.minor_unit_exponent,
    ).order_by(Donation.campaign_currency_code).all()
    processor_fees = db.session.query(
        PaymentAttempt.provider_fee_currency_code,
        Currency.minor_unit_exponent,
        db.func.sum(PaymentAttempt.provider_fee_minor),
    ).outerjoin(
        Currency, Currency.code == PaymentAttempt.provider_fee_currency_code
    ).filter(
        PaymentAttempt.status == "succeeded",
        PaymentAttempt.created_at >= since,
    ).group_by(
        PaymentAttempt.provider_fee_currency_code,
        Currency.minor_unit_exponent,
    ).order_by(PaymentAttempt.provider_fee_currency_code).all()
    latest_approved_version = (
        db.session.query(db.func.max(CampaignRevision.version_number))
        .filter(
            CampaignRevision.campaign_id == Campaign.id,
            CampaignRevision.status == "approved",
        )
        .correlate(Campaign)
        .scalar_subquery()
    )
    campaign_platform_fees = db.session.query(
        CampaignRevision.currency_code,
        Currency.minor_unit_exponent,
        db.func.sum(
            CampaignRevision.goal_amount_minor
            - CampaignRevision.goal_amount_minor * 10 // 11
        ),
    ).join(
        Currency, Currency.code == CampaignRevision.currency_code
    ).join(
        Campaign, Campaign.id == CampaignRevision.campaign_id
    ).filter(
        Campaign.status == "active",
        Campaign.deleted_at.is_(None),
        CampaignRevision.status == "approved",
        CampaignRevision.version_number == latest_approved_version,
    ).group_by(
        CampaignRevision.currency_code,
        Currency.minor_unit_exponent,
    ).order_by(CampaignRevision.currency_code).all()
    donor_count = db.session.query(db.func.count(db.func.distinct(Donation.donor_user_id))).filter(
        Donation.status == "succeeded",
        Donation.donor_user_id.isnot(None),
        Donation.created_at >= since,
    ).scalar()
    recent_reviews.sort(key=lambda item: item[3], reverse=True)
    return render_template(
        "admin/dashboard.html",
        waiting_campaigns=waiting_campaigns,
        waiting_campaign_count=waiting_campaign_count,
        waiting_expenses=ExpenseRequest.query.filter(
            ExpenseRequest.status.in_(("submitted", "in_review", "changes_requested"))
        ).count(),
        waiting_verifications=VerificationCase.query.filter(
            VerificationCase.status.in_(("submitted", "in_review", "changes_requested"))
        ).count(),
        queued_payouts=Disbursement.query.filter_by(status="queued").count(),
        donation_totals=donation_totals,
        processor_fees=processor_fees,
        campaign_platform_fees=campaign_platform_fees,
        live_campaigns=Campaign.query.filter_by(status="active", deleted_at=None).count(),
        donor_count=int(donor_count or 0),
        recent_reviews=recent_reviews[:5],
        successful_donation_count=successful_donations.count(),
    )


# ---------- Campaign reviews ----------

@admin.route("/campaign_queue")
def campaign_queue():
    revisions = (
        CampaignRevision.query.filter(
            CampaignRevision.status.in_(("submitted", "changes_requested"))
        )
        .order_by(CampaignRevision.submitted_at.asc())
        .limit(100)
        .all()
    )
    return render_template("admin/campaign_queue.html", revisions=revisions)


@admin.route("/campaign_review", methods=["GET", "POST"])
def campaign_review():
    revision_id = request.values.get("revision_id", type=int)
    revision = db.session.get(CampaignRevision, revision_id) if revision_id else None
    if revision is None:
        flash("Choose a campaign from the review queue.", "error")
        return redirect(url_for("admin.campaign_queue"))
    if revision.status not in ("submitted", "changes_requested"):
        flash("That campaign revision is no longer waiting for review.", "error")
        return redirect(url_for("admin.campaign_queue"))
    if request.method == "POST":
        action = request.form.get("action_taken", "")
        public_notes = request.form.get("public_notes", "").strip()
        internal_notes = request.form.get("internal_notes", "").strip()
        decisions = {
            "approved": "approved",
            "request_changes": "changes_requested",
            "rejected": "rejected",
        }
        if action not in decisions:
            flash("Choose a valid campaign decision.", "error")
            return render_template("admin/campaign_review.html", revision=revision), 400
        if action in {"request_changes", "rejected"} and not public_notes:
            flash("Add a note for the organiser before requesting changes or rejecting.", "error")
            return render_template("admin/campaign_review.html", revision=revision), 400

        campaign_record = revision.campaign
        revision.status = decisions[action]
        reviewer_id = session["admin_user_id"]
        review = CampaignReview.query.filter_by(
            revision_id=revision.id,
            reviewer_user_id=reviewer_id,
        ).first()
        if review is None:
            review = CampaignReview(
                revision_id=revision.id,
                reviewer_user_id=reviewer_id,
            )
            db.session.add(review)
        review.decision = decisions[action]
        review.public_notes = public_notes or None
        review.internal_notes = internal_notes or None
        if action == "approved":
            revision.approved_at = datetime.now(timezone.utc)
            for older_revision in CampaignRevision.query.filter(
                CampaignRevision.campaign_id == campaign_record.id,
                CampaignRevision.id != revision.id,
                CampaignRevision.status == "approved",
            ):
                older_revision.status = "superseded"
            campaign_record.status = "active"
        elif action == "rejected" and not CampaignRevision.query.filter(
            CampaignRevision.campaign_id == campaign_record.id,
            CampaignRevision.id != revision.id,
            CampaignRevision.status == "approved",
        ).first():
            campaign_record.status = "rejected"
        db.session.commit()
        flash("The campaign decision was saved.", "success")
        return redirect(url_for("admin.campaign_queue"))
    return render_template("admin/campaign_review.html", revision=revision)


# ---------- Expense reviews ----------

@admin.route("/expense_queue")
def expense_queue():
    selected_status = request.args.get("status", "waiting")
    query = ExpenseRequest.query
    status_groups = {
        "waiting": ("submitted", "in_review", "changes_requested"),
        "approved": ("approved",),
        "rejected": ("rejected",),
    }
    if selected_status in status_groups:
        query = query.filter(ExpenseRequest.status.in_(status_groups[selected_status]))
    elif selected_status != "all":
        selected_status = "all"
    expenses = query.order_by(ExpenseRequest.submitted_at.asc()).limit(100).all()
    return render_template(
        "admin/expense_queue.html",
        expenses=expenses,
        selected_status=selected_status,
    )


@admin.route("/expense_review", methods=["GET", "POST"])
def expense_review():
    expense_id = request.values.get("expense_id", type=int)
    expense = db.session.get(ExpenseRequest, expense_id) if expense_id else None
    if expense is None:
        flash("Choose an expense request from the queue.", "error")
        return redirect(url_for("admin.expense_queue"))
    if request.method == "POST":
        if expense.status not in {"submitted", "in_review", "changes_requested"}:
            flash("This expense is no longer waiting for review.", "error")
            return redirect(url_for("admin.expense_queue"))
        action = request.form.get("action_taken", "")
        public_notes = request.form.get("public_notes", "").strip()
        internal_notes = request.form.get("internal_notes", "").strip()
        decisions = {
            "approved": ("approved", "approved"),
            "request_changes": ("changes_requested", "needs_info"),
            "rejected": ("rejected", "rejected"),
        }
        if action not in decisions:
            flash("Choose a valid expense decision.", "error")
            return render_template(
                "admin/expense_review.html",
                expense=expense,
                public_notes=public_notes,
                internal_notes=internal_notes,
            ), 400
        if action in {"request_changes", "rejected"} and not public_notes:
            flash("Add a note for the organiser before requesting changes or rejecting.", "error")
            return render_template(
                "admin/expense_review.html",
                expense=expense,
                public_notes=public_notes,
                internal_notes=internal_notes,
            ), 400

        reviewer_id = session["admin_user_id"]
        review = ExpenseReview.query.filter_by(
            expense_request_id=expense.id,
            reviewer_user_id=reviewer_id,
        ).first()
        if review is None:
            review = ExpenseReview(
                expense_request_id=expense.id,
                reviewer_user_id=reviewer_id,
            )
            db.session.add(review)
        expense.status, review.decision = decisions[action]
        review.public_notes = public_notes or None
        review.internal_notes = internal_notes or None
        db.session.commit()
        flash("The expense decision was saved. No payout was sent.", "success")
        return redirect(url_for("admin.expense_queue"))
    return render_template("admin/expense_review.html", expense=expense)


# ---------- Identity checks ----------

@admin.route("/profile_queue")
def profile_queue():
    selected_status = request.args.get("status", "waiting")
    query = VerificationCase.query
    status_groups = {
        "waiting": ("submitted", "in_review", "changes_requested"),
        "verified": ("approved",),
        "rejected": ("rejected",),
    }
    if selected_status in status_groups:
        query = query.filter(VerificationCase.status.in_(status_groups[selected_status]))
    elif selected_status != "all":
        selected_status = "all"
    cases = query.order_by(VerificationCase.submitted_at.asc()).limit(100).all()
    return render_template(
        "admin/profile_review.html",
        cases=cases,
        selected_status=selected_status,
    )


@admin.route("/profile_review", methods=["GET", "POST"])
def profile_review():
    case_id = request.values.get("case_id", type=int)
    verification_case = db.session.get(VerificationCase, case_id) if case_id else None
    if verification_case is None:
        flash("Choose an identity case from the queue.", "error")
        return redirect(url_for("admin.profile_queue"))
    if request.method == "POST":
        if verification_case.status not in {"submitted", "in_review", "changes_requested"}:
            flash("This identity case is no longer waiting for review.", "error")
            return redirect(url_for("admin.profile_queue"))
        action = request.form.get("action_taken", "")
        public_notes = request.form.get("public_notes", "").strip()
        internal_notes = request.form.get("internal_notes", "").strip()
        decisions = {
            "approved": ("approved", "approved"),
            "request_changes": ("changes_requested", "changes_requested"),
            "rejected": ("rejected", "rejected"),
        }
        if action not in decisions:
            flash("Choose a valid identity decision.", "error")
            return render_template(
                "admin/profile_review.html",
                verification_case=verification_case,
                public_notes=public_notes,
                internal_notes=internal_notes,
            ), 400
        if action in {"request_changes", "rejected"} and not public_notes:
            flash("Add a note for the user before requesting changes or rejecting.", "error")
            return render_template(
                "admin/profile_review.html",
                verification_case=verification_case,
                public_notes=public_notes,
                internal_notes=internal_notes,
            ), 400

        reviewer_id = session["admin_user_id"]
        review = VerificationReview.query.filter_by(
            case_id=verification_case.id,
            reviewer_user_id=reviewer_id,
        ).first()
        if review is None:
            review = VerificationReview(
                case_id=verification_case.id,
                reviewer_user_id=reviewer_id,
            )
            db.session.add(review)
        verification_case.status, review.decision = decisions[action]
        verification_case.closed_at = (
            datetime.now(timezone.utc) if action in {"approved", "rejected"} else None
        )
        review.public_notes = public_notes or None
        review.internal_notes = internal_notes or None
        db.session.commit()
        flash("The identity decision was saved.", "success")
        return redirect(url_for("admin.profile_queue"))
    return render_template("admin/profile_review.html", verification_case=verification_case)


@admin.route("/verification_documents/<int:document_id>")
def verification_document(document_id):
    document = db.session.get(VerificationDocument, document_id)
    if document is None:
        abort(404)
    storage_key = document.file_asset.storage_key
    if not storage_key or not re.fullmatch(
        r"verification/[0-9a-f]{32}\.(pdf|jpg|png)",
        storage_key,
    ):
        abort(404)
    response = send_from_directory(
        Path(current_app.instance_path) / "private_uploads",
        storage_key,
        as_attachment=True,
        download_name=document.file_asset.original_filename or "verification-document",
        mimetype=document.file_asset.detected_content_type,
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


# ---------- Money ----------

@admin.route("/disbursals", methods=["GET", "POST"])
def disbursals():
    if request.method == "POST":
        flash("Payouts require a configured payment provider and must not be marked as sent yet.", "error")
        status_code = 503
    else:
        status_code = 200
    approved_expenses = (
        ExpenseRequest.query.join(PayoutRecipient, ExpenseRequest.recipient_id == PayoutRecipient.id)
        .filter(
            ExpenseRequest.status == "approved",
            ExpenseRequest.id.notin_(db.session.query(Disbursement.expense_request_id)),
            PayoutRecipient.status == "active",
        )
        .order_by(ExpenseRequest.submitted_at.asc())
        .limit(100)
        .all()
    )
    disbursements = Disbursement.query.order_by(Disbursement.created_at.desc()).limit(100).all()
    return render_template(
        "admin/disbursals.html",
        approved_expenses=approved_expenses,
        disbursements=disbursements,
    ), status_code


@admin.route("/donations")
def donations():
    selected_status = request.args.get("status", "all")
    query = Donation.query
    if selected_status in {"succeeded", "pending", "failed", "refunded", "partially_refunded", "disputed"}:
        query = query.filter_by(status=selected_status)
    elif selected_status != "all":
        selected_status = "all"
    donations = query.order_by(Donation.created_at.desc()).limit(100).all()
    donation_ids = [donation.id for donation in donations]
    latest_attempts = {}
    if donation_ids:
        attempts = PaymentAttempt.query.filter(
            PaymentAttempt.donation_id.in_(donation_ids)
        ).order_by(PaymentAttempt.created_at.desc()).all()
        for attempt in attempts:
            latest_attempts.setdefault(attempt.donation_id, attempt)
    gross_totals = db.session.query(
        Donation.campaign_currency_code,
        Currency.minor_unit_exponent,
        db.func.sum(Donation.intended_amount_minor),
    ).join(
        Currency, Currency.code == Donation.campaign_currency_code
    ).filter(
        Donation.status == "succeeded"
    ).group_by(
        Donation.campaign_currency_code,
        Currency.minor_unit_exponent,
    ).order_by(Donation.campaign_currency_code).all()
    fee_totals = db.session.query(
        PaymentAttempt.provider_fee_currency_code,
        Currency.minor_unit_exponent,
        db.func.sum(PaymentAttempt.provider_fee_minor),
    ).outerjoin(
        Currency, Currency.code == PaymentAttempt.provider_fee_currency_code
    ).filter(
        PaymentAttempt.status == "succeeded"
    ).group_by(
        PaymentAttempt.provider_fee_currency_code,
        Currency.minor_unit_exponent,
    ).order_by(PaymentAttempt.provider_fee_currency_code).all()
    return render_template(
        "admin/donations.html",
        donations=donations,
        latest_attempts=latest_attempts,
        selected_status=selected_status,
        gross_totals=gross_totals,
        fee_totals=fee_totals,
        donation_count=Donation.query.count(),
    )


# ---------- Platform ----------

@admin.route("/accounts", methods=["GET", "POST"])
def accounts():
    search = request.args.get("q", "").strip()
    role = request.args.get("role", "").strip()
    query = User.query.filter(User.deleted_at.is_(None))

    if search:
        text = f"%{search}%"
        query = query.filter(or_(User.display_name.ilike(text), User.email.ilike(text)))
    admin_user_ids = db.session.query(StaffRole.user_id).filter(
        StaffRole.role == "admin",
        StaffRole.revoked_at.is_(None),
    )
    ngo_user_ids = db.session.query(Organization.owner_user_id).filter(
        Organization.deleted_at.is_(None)
    )
    if role == "campaigner":
        query = query.filter(
            User.campaigner_enabled.is_(True),
            User.id.notin_(admin_user_ids),
            User.id.notin_(ngo_user_ids),
        )
    elif role == "donor":
        query = query.filter(
            User.campaigner_enabled.is_(False),
            User.id.notin_(admin_user_ids),
            User.id.notin_(ngo_user_ids),
        )
    elif role == "ngo":
        query = query.filter(User.id.in_(ngo_user_ids))
    elif role == "admin":
        query = query.filter(User.id.in_(admin_user_ids))

    accounts = query.order_by(User.created_at.desc()).limit(100).all()
    account_ids = [account.id for account in accounts]
    admin_ids = {
        user_id
        for (user_id,) in db.session.query(StaffRole.user_id).filter(
            StaffRole.role == "admin",
            StaffRole.revoked_at.is_(None),
        )
    }
    ngo_ids = {
        user_id
        for (user_id,) in db.session.query(Organization.owner_user_id).filter(
            Organization.deleted_at.is_(None)
        )
    }
    verification_statuses = {}
    if account_ids:
        verification_cases = VerificationCase.query.filter(
            VerificationCase.subject_user_id.in_(account_ids)
        ).order_by(VerificationCase.submitted_at.desc()).all()
        for verification_case in verification_cases:
            verification_statuses.setdefault(
                verification_case.subject_user_id,
                verification_case.status,
            )
    account_roles = {
        account.id: (
            "admin" if account.id in admin_ids
            else "ngo" if account.id in ngo_ids
            else "campaigner" if account.campaigner_enabled
            else "donor"
        )
        for account in accounts
    }
    if request.args.get("format") == "json":
        return {
            "accounts": [
                {
                    "id": account.id,
                    "name": account.display_name,
                    "email": account.email or "",
                    "role": account_roles[account.id],
                    "joined": account.created_at.strftime("%d %b %Y"),
                    "status": account.account_status,
                    "verification": verification_statuses.get(account.id, "not_submitted"),
                }
                for account in accounts
            ]
        }
    if request.method == "POST":
        account = db.session.get(User, request.form.get("account_id", type=int))
        if account is None or account.email == current_app.config.get("ADMIN_EMAIL"):
            flash("Choose a valid account to deactivate.", "error")
            return redirect(url_for("admin.accounts"))
        account.account_status = "closed"
        account.deleted_at = datetime.now(timezone.utc)
        db.session.commit()
        flash("The account was deactivated.", "success")
        return redirect(url_for("admin.accounts"))
    return render_template(
        "admin/accounts.html",
        accounts=accounts,
        search=search,
        selected_role=role,
        account_roles=account_roles,
        verification_statuses=verification_statuses,
    )


@admin.route("/categories", methods=["GET", "POST"])
def categories():
    if request.method == "POST":
        category_id = request.form.get("category_id", type=int)
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        if not name or len(name) > 100:
            flash("Enter a category name up to 100 characters.", "error")
            return redirect(url_for("admin.categories"))

        category = db.session.get(Category, category_id) if category_id else None
        if category_id and category is None:
            flash("That category no longer exists.", "error")
            return redirect(url_for("admin.categories"))
        if category is None:
            slug = request.form.get("slug", "").strip().lower()
            if not slug:
                slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
            if not slug:
                flash("Enter a valid category slug.", "error")
                return redirect(url_for("admin.categories"))
            category = Category(name=name, slug=slug[:120])
            db.session.add(category)
        category.name = name
        category.description = description[:500] or None
        if category_id:
            category.is_active = request.form.get("is_active") == "1"
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("A category already uses that name or slug.", "error")
            return redirect(url_for("admin.categories"))
        flash("The category was saved.", "success")
        return redirect(url_for("admin.categories"))
    categories = Category.query.order_by(Category.name).all()
    return render_template("admin/categories.html", categories=categories)


@admin.route("/verification_logs")
def verification_logs():
    selected_type = request.args.get("type", "all")
    if selected_type not in {"all", "campaigns", "expenses", "identities"}:
        selected_type = "all"
    audit_entries = []
    if selected_type in {"all", "campaigns"}:
        for review in CampaignReview.query.order_by(CampaignReview.decided_at.desc()).limit(100):
            audit_entries.append((review.decision, "Campaign", review.revision.title, review.reviewer.display_name, review.decided_at, review.public_notes, review.internal_notes))
    if selected_type in {"all", "expenses"}:
        for review in ExpenseReview.query.order_by(ExpenseReview.decided_at.desc()).limit(100):
            audit_entries.append((review.decision, "Expense", review.expense_request.title, review.reviewer.display_name, review.decided_at, review.public_notes, review.internal_notes))
    if selected_type in {"all", "identities"}:
        for review in VerificationReview.query.order_by(VerificationReview.decided_at.desc()).limit(100):
            subject = review.case.subject_user or review.case.subject_organization
            name = subject.display_name if hasattr(subject, "display_name") else subject.legal_name
            audit_entries.append((review.decision, "Identity", name, review.reviewer.display_name, review.decided_at, review.public_notes, review.internal_notes))
    audit_entries.sort(key=lambda item: item[4], reverse=True)
    return render_template(
        "admin/verification_logs.html",
        audit_entries=audit_entries[:100],
        selected_type=selected_type,
    )
