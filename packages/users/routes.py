"""User-facing pages: landing, auth, dashboard, profile, settings.

Member accounts are stored in the existing users table. A signed-in user's ID
is kept in Flask's session until they log out.
"""
import hashlib
import re
import secrets
from datetime import date, datetime, timezone
from functools import wraps
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_from_directory, session, url_for
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from packages.models import (
    Campaign,
    CampaignRevision,
    CampaignUpdate,
    Donation,
    ExpenseRequest,
    FileAsset,
    Organization,
    User,
    VerificationCase,
    VerificationDocument,
    db,
)


user = Blueprint("user", __name__, template_folder="templates", static_folder="static", static_url_path="/users/static")

MAX_VERIFICATION_FILE_SIZE = 5 * 1024 * 1024
VERIFICATION_FILE_TYPES = {
    "identity": "National ID / Passport",
    "proof_of_address": "Proof of address",
    "organization_registration": "Organization registration",
}


def login_required(view):
    """Send signed-out users to the login page before opening a member page."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        account = db.session.get(User, session.get("user_id")) if session.get("user_id") else None
        if account is None or account.account_status != "active" or account.deleted_at is not None:
            session.pop("user_id", None)
            return redirect(url_for("user.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def _signed_in_user():
    """Load the account named by the current login session."""
    user_id = session.get("user_id")
    if not user_id:
        return None
    return db.session.get(User, user_id)


# ---------- Public pages ----------

@user.route("/landing")
@user.route("/")
def landing_page():
    return render_template("users/landing.html")


@user.route("/how_it_works")
def how_it_works():
    return render_template("users/how_it_works.html")


@user.route("/public_profile")
@user.route("/public_profile/<int:user_id>")
def public_profile(user_id=None):
    if user_id is None:
        profile = _signed_in_user()
        if profile is None:
            return redirect(url_for("user.login", next=request.path))
    else:
        profile = User.query.filter_by(
            id=user_id,
            account_status="active",
            deleted_at=None,
        ).first_or_404()

    organization = Organization.query.filter_by(
        owner_user_id=profile.id,
        deleted_at=None,
        organization_status="active",
    ).first()
    if organization:
        verification_case = VerificationCase.query.filter_by(
            subject_organization_id=organization.id,
            status="approved",
        ).first()
    else:
        verification_case = VerificationCase.query.filter_by(
            subject_user_id=profile.id,
            status="approved",
        ).first()

    campaign_query = Campaign.query.filter_by(
        status="active",
        deleted_at=None,
    )
    if organization:
        campaign_query = campaign_query.filter(
            or_(
                Campaign.owner_user_id == profile.id,
                Campaign.owner_organization_id == organization.id,
            )
        )
    else:
        campaign_query = campaign_query.filter_by(owner_user_id=profile.id)

    public_campaigns = []
    for campaign_record in campaign_query.order_by(Campaign.updated_at.desc()).all():
        revision = CampaignRevision.query.filter_by(
            campaign_id=campaign_record.id,
            status="approved",
        ).order_by(CampaignRevision.version_number.desc()).first()
        if revision is None:
            continue
        raised_minor = (
            db.session.query(func.coalesce(func.sum(Donation.credited_amount_minor), 0))
            .filter(
                Donation.campaign_id == campaign_record.id,
                Donation.status == "succeeded",
            )
            .scalar()
            or 0
        )
        public_campaigns.append({
            "campaign": campaign_record,
            "revision": revision,
            "raised_minor": int(raised_minor),
            "progress": min(100, round(int(raised_minor) * 100 / revision.goal_amount_minor)),
        })

    total_raised_minor = sum(item["raised_minor"] for item in public_campaigns)
    profile_type = (
        "organisation"
        if organization
        else "campaigner"
        if profile.campaigner_enabled
        else "donor"
    )
    return render_template(
        "users/public_profile.html",
        profile=profile,
        organization=organization,
        profile_type=profile_type,
        is_verified=verification_case is not None,
        campaigns=public_campaigns,
        total_raised_minor=total_raised_minor,
    )


@user.route("/pages")
def page_library():
    return render_template("index.html")


# ---------- Auth ----------

@user.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        role = request.form.get("account_role", "donor")
        display_name = request.form.get("display_name", "").strip()
        phone_number = request.form.get("phone_number", "").strip()
        legal_name = request.form.get("legal_name", "").strip()

        if not email or not password or not display_name or role not in {"donor", "campaigner", "ngo"}:
            flash("Enter your name, email, and password, then choose an account type.", "error")
            return render_template("users/register.html"), 400
        if len(password) < 8:
            flash("Your password must have at least 8 characters.", "error")
            return render_template("users/register.html"), 400
        if role == "ngo" and not legal_name:
            flash("Enter the registered organisation name.", "error")
            return render_template("users/register.html"), 400

        account = User(
            email=email,
            password_hash=generate_password_hash(password),
            display_name=display_name,
            phone_number=phone_number or None,
            legal_name=legal_name or None,
            campaigner_enabled=role in {"campaigner", "ngo"},
        )
        try:
            db.session.add(account)
            db.session.flush()
            if role == "ngo":
                db.session.add(
                    Organization(
                        legal_name=legal_name,
                        display_name=display_name,
                        owner_user_id=account.id,
                    )
                )
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("An account already uses that email address.", "error")
            return render_template("users/register.html"), 400

        session.clear()
        session["user_id"] = account.id
        return redirect(url_for("user.dashboard"))
    return render_template("users/register.html")


@user.route("/welcome")
def welcome():
    return render_template("users/welcome.html")


@user.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        next_page = request.args.get("next", "")

        if email == current_app.config.get("ADMIN_EMAIL", "").strip().lower():
            return redirect(url_for("admin.login", next=next_page))

        account = User.query.filter_by(email=email).first()
        if account is None or not account.password_hash or not check_password_hash(account.password_hash, password):
            flash("Email or password is incorrect.", "error")
            return render_template("users/login.html"), 401
        if account.account_status != "active" or account.deleted_at is not None:
            flash("This account cannot sign in. Contact support for help.", "error")
            return render_template("users/login.html"), 403

        session.clear()
        session["user_id"] = account.id
        if next_page.startswith("/") and not next_page.startswith("//") and not next_page.startswith("/admin"):
            return redirect(next_page)
        return redirect(url_for("user.dashboard"))
    return render_template("users/login.html")


@user.route("/forgot_password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        flash("Password reset is unavailable until email delivery is configured.", "error")
        return render_template("users/forgot_password.html"), 503
    return render_template("users/forgot_password.html")


@user.route("/reset_password", methods=["GET", "POST"])
def reset_password():
    if request.method == "POST":
        flash("Password reset is unavailable until email delivery is configured.", "error")
        return render_template("users/reset_password.html"), 503
    return render_template("users/reset_password.html")


@user.route("/logout", methods=["GET", "POST"])
@user.route("/users/logout", methods=["GET", "POST"])
def logout():
    session.clear()
    return redirect(url_for("user.landing_page"))


# ---------- Member area ----------

@user.route("/dashboard")
@login_required
def dashboard():
    account = _signed_in_user()
    if account is None:
        session.clear()
        return redirect(url_for("user.login"))

    organization = Organization.query.filter_by(
        owner_user_id=account.id,
        deleted_at=None,
    ).first()
    campaign_query = Campaign.query.filter_by(deleted_at=None)
    if organization:
        campaign_query = campaign_query.filter(
            or_(
                Campaign.owner_user_id == account.id,
                Campaign.owner_organization_id == organization.id,
            )
        )
    else:
        campaign_query = campaign_query.filter_by(owner_user_id=account.id)
    owned_campaigns = campaign_query.order_by(Campaign.updated_at.desc()).all()
    campaign_ids = [record.id for record in owned_campaigns]

    campaign_rows = []
    for campaign_record in owned_campaigns[:4]:
        revision = CampaignRevision.query.filter_by(
            campaign_id=campaign_record.id,
        ).order_by(CampaignRevision.version_number.desc()).first()
        if revision is None:
            continue
        raised_minor = (
            db.session.query(func.coalesce(func.sum(Donation.credited_amount_minor), 0))
            .filter(
                Donation.campaign_id == campaign_record.id,
                Donation.status == "succeeded",
            )
            .scalar()
            or 0
        )
        campaign_rows.append({
            "campaign": campaign_record,
            "revision": revision,
            "raised_minor": int(raised_minor),
            "progress": min(100, round(int(raised_minor) * 100 / revision.goal_amount_minor)),
        })

    total_raised_minor = 0
    paid_expenses_minor = 0
    approved_expenses_minor = 0
    expenses_in_review = 0
    if campaign_ids:
        total_raised_minor = (
            db.session.query(func.coalesce(func.sum(Donation.credited_amount_minor), 0))
            .filter(
                Donation.campaign_id.in_(campaign_ids),
                Donation.status == "succeeded",
            )
            .scalar()
            or 0
        )
        paid_expenses_minor = (
            db.session.query(func.coalesce(func.sum(ExpenseRequest.amount_minor), 0))
            .filter(
                ExpenseRequest.campaign_id.in_(campaign_ids),
                ExpenseRequest.status == "paid",
            )
            .scalar()
            or 0
        )
        approved_expenses_minor = (
            db.session.query(func.coalesce(func.sum(ExpenseRequest.amount_minor), 0))
            .filter(
                ExpenseRequest.campaign_id.in_(campaign_ids),
                ExpenseRequest.status == "approved",
            )
            .scalar()
            or 0
        )
        expenses_in_review = ExpenseRequest.query.filter(
            ExpenseRequest.campaign_id.in_(campaign_ids),
            ExpenseRequest.status.in_(["submitted", "in_review"]),
        ).count()

    donations = Donation.query.filter_by(
        donor_user_id=account.id,
    ).order_by(Donation.created_at.desc()).limit(5).all()
    recent_donations = []
    for donation in donations:
        revision = CampaignRevision.query.filter_by(
            campaign_id=donation.campaign_id,
            status="approved",
        ).order_by(CampaignRevision.version_number.desc()).first()
        recent_donations.append({
            "donation": donation,
            "revision": revision,
        })
    donation_totals = (
        db.session.query(
            func.coalesce(func.sum(Donation.credited_amount_minor), 0),
            func.count(Donation.id),
            func.count(func.distinct(Donation.campaign_id)),
        )
        .filter(
            Donation.donor_user_id == account.id,
            Donation.status == "succeeded",
        )
        .one()
    )
    supported_campaign_ids = [
        campaign_id
        for (campaign_id,) in db.session.query(Donation.campaign_id)
        .filter(
            Donation.donor_user_id == account.id,
            Donation.status == "succeeded",
        )
        .distinct()
        .all()
    ]
    recent_updates = []
    if supported_campaign_ids:
        updates = CampaignUpdate.query.filter(
            CampaignUpdate.campaign_id.in_(supported_campaign_ids),
            CampaignUpdate.published_at.isnot(None),
            CampaignUpdate.deleted_at.is_(None),
        ).order_by(CampaignUpdate.published_at.desc()).limit(5).all()
        for update in updates:
            revision = CampaignRevision.query.filter_by(
                campaign_id=update.campaign_id,
                status="approved",
            ).order_by(CampaignRevision.version_number.desc()).first()
            recent_updates.append({
                "campaign_update": update,
                "revision": revision,
            })

    verification_subject = (
        {"subject_organization_id": organization.id}
        if organization
        else {"subject_user_id": account.id}
    )
    verification_case = VerificationCase.query.filter_by(
        **verification_subject,
    ).order_by(VerificationCase.submitted_at.desc()).first()

    return render_template(
        "users/dashboard.html",
        account=account,
        campaign_rows=campaign_rows,
        campaign_count=len(owned_campaigns),
        total_raised_minor=int(total_raised_minor),
        paid_expenses_minor=int(paid_expenses_minor),
        available_minor=max(
            int(total_raised_minor)
            - int(paid_expenses_minor)
            - int(approved_expenses_minor),
            0,
        ),
        expenses_in_review=expenses_in_review,
        recent_donations=recent_donations,
        total_given_minor=int(donation_totals[0] or 0),
        donation_count=donation_totals[1],
        supported_campaign_count=donation_totals[2],
        recent_updates=recent_updates,
        verification_status=verification_case.status if verification_case else "not_started",
    )


@user.route("/profile_edit", methods=["GET", "POST"])
@login_required
def profile_edit():
    account = _signed_in_user()
    if account is None:
        session.clear()
        return redirect(url_for("user.login"))
    organization = Organization.query.filter_by(owner_user_id=account.id, deleted_at=None).first()
    verification_subject = (
        {"subject_organization_id": organization.id}
        if organization
        else {"subject_user_id": account.id}
    )
    verification_case = VerificationCase.query.filter_by(
        **verification_subject,
    ).order_by(VerificationCase.submitted_at.desc()).first()
    verification_status = verification_case.status if verification_case else "not_started"
    if request.method == "POST":
        next_action = request.form.get("action", "save")
        avatar = request.files.get("avatar")
        if avatar and avatar.filename:
            flash("Profile photos cannot be saved until file storage is configured.", "error")
            return render_template(
                "users/profile_edit.html",
                account=account,
                organization=organization,
                verification_status=verification_status,
            ), 503
        display_name = request.form.get("display_name", "").strip()
        if not display_name:
            flash("Enter a display name.", "error")
            return render_template(
                "users/profile_edit.html",
                account=account,
                organization=organization,
                verification_status=verification_status,
            ), 400

        account.display_name = display_name[:120]
        account.bio = request.form.get("bio", "").strip()[:500] or None
        account.legal_name = request.form.get("legal_name", "").strip()[:200] or None
        account.phone_number = request.form.get("phone_number", "").strip()[:32] or None
        account.address_country = request.form.get("address_country", "").strip()[:100] or None
        account.address_street = request.form.get("address_street", "").strip()[:500] or None
        account.address_state = request.form.get("address_state", "").strip()[:120] or None

        if organization:
            organization.display_name = account.display_name
            organization.legal_name = account.legal_name or organization.legal_name
            organization.registration_number = request.form.get("registration_number", "").strip()[:80] or None
            organization.website_url = request.form.get("website_url", "").strip()[:2048] or None
            founded_date = request.form.get("founded_date", "").strip()
            try:
                organization.founded_date = date.fromisoformat(founded_date) if founded_date else None
            except ValueError:
                flash("Enter a valid organisation founding date.", "error")
                db.session.rollback()
                return render_template(
                    "users/profile_edit.html",
                    account=account,
                    organization=organization,
                    verification_status=verification_status,
                ), 400
            organization.tax_exempt_status = request.form.get("tax_exempt_status") == "1"

        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("That organisation registration number is already in use.", "error")
            return render_template(
                "users/profile_edit.html",
                account=account,
                organization=organization,
                verification_status=verification_status,
            ), 400
        flash("Your profile was saved.", "success")
        if next_action == "submit_verification":
            return redirect(url_for("user.verification"))
        return redirect(url_for("user.profile_edit"))
    return render_template(
        "users/profile_edit.html",
        account=account,
        organization=organization,
        verification_status=verification_status,
    )


@user.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    account = _signed_in_user()
    if account is None:
        session.clear()
        return redirect(url_for("user.login"))
    if request.method == "POST":
        section = request.form.get("section")
        current_password = request.form.get("current_password", "")
        if not account.password_hash or not check_password_hash(account.password_hash, current_password):
            flash("Your current password is incorrect.", "error")
            return render_template("users/settings.html", account=account), 400

        if section == "email":
            email = request.form.get("email", "").strip().lower()
            if not email or "@" not in email:
                flash("Enter a valid email address.", "error")
                return render_template("users/settings.html", account=account), 400
            account.email = email
        elif section == "password":
            password = request.form.get("new_password", "")
            if len(password) < 8 or password != request.form.get("confirm_password", ""):
                flash("Enter matching passwords with at least 8 characters.", "error")
                return render_template("users/settings.html", account=account), 400
            account.password_hash = generate_password_hash(password)
        elif section == "deactivate":
            account.account_status = "closed"
            account.deleted_at = datetime.now(timezone.utc)
            db.session.commit()
            session.clear()
            flash("Your account has been deactivated.", "success")
            return redirect(url_for("user.landing_page"))
        else:
            flash("Choose an account setting to update.", "error")
            return render_template("users/settings.html", account=account), 400

        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("That email address is already in use.", "error")
            return render_template("users/settings.html", account=account), 400
        flash("Your account settings were saved.", "success")
        return redirect(url_for("user.settings"))
    return render_template("users/settings.html", account=account)


@user.route("/verification", methods=["GET", "POST"])
@login_required
def verification():
    account = _signed_in_user()
    if account is None:
        session.clear()
        return redirect(url_for("user.login"))
    if not account.campaigner_enabled:
        abort(403)

    organization = Organization.query.filter_by(
        owner_user_id=account.id,
        deleted_at=None,
    ).first()
    verification_subject = (
        {"subject_organization_id": organization.id}
        if organization
        else {"subject_user_id": account.id}
    )
    verification_case = VerificationCase.query.filter_by(
        **verification_subject,
    ).order_by(VerificationCase.submitted_at.desc()).first()

    if request.method == "POST":
        document_type = request.form.get("doc_type", "")
        if document_type not in VERIFICATION_FILE_TYPES or (
            document_type == "organization_registration" and organization is None
        ):
            flash("Choose a valid document type for your account.", "error")
            return redirect(url_for("user.verification"))

        uploaded_file = request.files.get("file")
        if uploaded_file is None or not uploaded_file.filename:
            flash("Choose a verification document to upload.", "error")
            return redirect(url_for("user.verification"))

        file_data = uploaded_file.stream.read(MAX_VERIFICATION_FILE_SIZE + 1)
        if len(file_data) > MAX_VERIFICATION_FILE_SIZE:
            flash("Verification documents must be 5 MB or smaller.", "error")
            return redirect(url_for("user.verification"))
        if not file_data:
            flash("The selected verification document is empty.", "error")
            return redirect(url_for("user.verification"))

        if file_data.startswith(b"%PDF-"):
            extension, content_type = ".pdf", "application/pdf"
        elif file_data.startswith(b"\xff\xd8\xff"):
            extension, content_type = ".jpg", "image/jpeg"
        elif file_data.startswith(b"\x89PNG\r\n\x1a\n"):
            extension, content_type = ".png", "image/png"
        else:
            flash("Choose a valid PDF, JPG, or PNG document.", "error")
            return redirect(url_for("user.verification"))

        upload_folder = Path(current_app.instance_path) / "private_uploads" / "verification"
        upload_folder.mkdir(parents=True, exist_ok=True)
        filename = f"{secrets.token_hex(16)}{extension}"
        file_path = upload_folder / filename
        with file_path.open("xb") as saved_file:
            saved_file.write(file_data)

        try:
            if verification_case is None or verification_case.status not in {
                "submitted",
                "in_review",
                "changes_requested",
            }:
                verification_case = VerificationCase(
                    **verification_subject,
                    status="submitted",
                )
                db.session.add(verification_case)
                db.session.flush()
            else:
                verification_case.status = "submitted"
                verification_case.submitted_at = datetime.now(timezone.utc)
                verification_case.closed_at = None

            file_asset = FileAsset(
                storage_key=f"verification/{filename}",
                original_filename=secure_filename(uploaded_file.filename)[:255] or "verification-document",
                detected_content_type=content_type,
                byte_size=len(file_data),
                sha256_hex=hashlib.sha256(file_data).hexdigest(),
                uploaded_by_user_id=account.id,
            )
            document = VerificationDocument(
                case=verification_case,
                file_asset=file_asset,
                document_type=document_type,
            )
            db.session.add_all([file_asset, document])
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            if file_path.exists():
                file_path.unlink()
            raise

        flash("Your document was uploaded and sent for verification review.", "success")
        return redirect(url_for("user.verification"))

    latest_review = (
        max(verification_case.reviews, key=lambda review: review.decided_at)
        if verification_case and verification_case.reviews
        else None
    )
    return render_template(
        "users/verification.html",
        account=account,
        organization=organization,
        verification_case=verification_case,
        latest_review=latest_review,
        document_types=VERIFICATION_FILE_TYPES,
    )


@user.route("/verification/documents/<int:document_id>")
@login_required
def verification_document(document_id):
    account = _signed_in_user()
    document = db.session.get(VerificationDocument, document_id)
    if account is None or document is None:
        abort(404)

    organization = Organization.query.filter_by(
        owner_user_id=account.id,
        deleted_at=None,
    ).first()
    is_owner = document.case.subject_user_id == account.id or (
        organization is not None
        and document.case.subject_organization_id == organization.id
    )
    if not is_owner:
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
