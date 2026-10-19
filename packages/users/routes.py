"""User-facing pages: landing, auth, dashboard, profile, settings.

Member accounts are stored in the existing users table. A signed-in user's ID
is kept in Flask's session until they log out.
"""
from datetime import date, datetime, timezone
from functools import wraps

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from packages.models import Organization, StaffRole, User, db


user = Blueprint("user", __name__, template_folder="templates", static_folder="static", static_url_path="/users/static")


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
def public_profile():
    return render_template("users/public_profile.html")


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

        if email == current_app.config["ADMIN_EMAIL"] and password == current_app.config["ADMIN_PASSWORD"]:
            admin_account = User.query.filter_by(email=email).first()
            if admin_account is None:
                admin_account = User(
                    email=email,
                    password_hash=generate_password_hash(password),
                    display_name="Vouch Admin",
                )
                db.session.add(admin_account)
                db.session.flush()
            admin_role = StaffRole.query.filter_by(
                user_id=admin_account.id,
                role="admin",
                revoked_at=None,
            ).first()
            if admin_role is None:
                db.session.add(
                    StaffRole(
                        user_id=admin_account.id,
                        role="admin",
                        granted_by_user_id=admin_account.id,
                    )
                )
            db.session.commit()
            session.clear()
            session["admin_logged_in"] = True
            session["admin_user_id"] = admin_account.id
            return redirect(next_page if next_page.startswith("/admin") else url_for("admin.dashboard"))

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
    return render_template("users/dashboard.html")


@user.route("/profile_edit", methods=["GET", "POST"])
@login_required
def profile_edit():
    account = _signed_in_user()
    if account is None:
        session.clear()
        return redirect(url_for("user.login"))
    organization = Organization.query.filter_by(owner_user_id=account.id, deleted_at=None).first()
    if request.method == "POST":
        avatar = request.files.get("avatar")
        if avatar and avatar.filename:
            flash("Profile photos cannot be saved until file storage is configured.", "error")
            return render_template("users/profile_edit.html", account=account, organization=organization), 503
        display_name = request.form.get("display_name", "").strip()
        if not display_name:
            flash("Enter a display name.", "error")
            return render_template("users/profile_edit.html", account=account, organization=organization), 400

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
                return render_template("users/profile_edit.html", account=account, organization=organization), 400
            organization.tax_exempt_status = request.form.get("tax_exempt_status") == "1"

        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("That organisation registration number is already in use.", "error")
            return render_template("users/profile_edit.html", account=account, organization=organization), 400
        flash("Your profile was saved.", "success")
        return redirect(url_for("user.profile_edit"))
    return render_template("users/profile_edit.html", account=account, organization=organization)


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
    if request.method == "POST":
        flash("Identity documents cannot be saved until secure file storage is configured.", "error")
        return render_template("users/verification.html"), 503
    return render_template("users/verification.html")
