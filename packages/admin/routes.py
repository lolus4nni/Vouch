"""Admin console: review queues, payouts, monitoring, accounts, categories.

The admin login flag is stored in the same signed Flask session as member login.
"""
import re
from datetime import datetime, timezone

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from packages.models import Campaign, CampaignReview, CampaignRevision, Category, Organization, StaffRole, User, db


admin = Blueprint("admin", __name__, template_folder="templates", static_folder="static")


@admin.before_request
def require_admin_login():
    """Keep every admin page behind the simple login session."""
    if request.endpoint == "admin.static":
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
        return redirect(url_for("user.login", next=request.path))
    return None


@admin.route("/")
@admin.route("/dashboard.html")
def dashboard():
    return render_template("admin/dashboard.html")


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
    return render_template("admin/expense_queue.html")


@admin.route("/expense_review", methods=["GET", "POST"])
def expense_review():
    if request.method == "POST":
        flash("Expense reviews are not connected to live review records yet.", "error")
        return render_template("admin/expense_review.html"), 503
    return render_template("admin/expense_review.html")


# ---------- Identity checks ----------

@admin.route("/profile_queue")
def profile_queue():
    return render_template("admin/profile_queue.html")


@admin.route("/profile_review", methods=["GET", "POST"])
def profile_review():
    if request.method == "POST":
        flash("Identity reviews are not connected to live review records yet.", "error")
        return render_template("admin/profile_review.html"), 503
    return render_template("admin/profile_review.html")


# ---------- Money ----------

@admin.route("/disbursals", methods=["GET", "POST"])
def disbursals():
    if request.method == "POST":
        flash("Payouts require a configured payment provider and must not be marked as sent yet.", "error")
        return render_template("admin/disbursals.html"), 503
    return render_template("admin/disbursals.html")


@admin.route("/donations")
def donations():
    return render_template("admin/donations.html")


# ---------- Platform ----------

@admin.route("/accounts", methods=["GET", "POST"])
def accounts():
    search = request.args.get("q", "").strip()
    role = request.args.get("role", "").strip()
    query = User.query.filter(User.deleted_at.is_(None))

    if search:
        text = f"%{search}%"
        query = query.filter(or_(User.display_name.ilike(text), User.email.ilike(text)))
    if role == "campaigner":
        query = query.filter(User.campaigner_enabled.is_(True))
    elif role == "donor":
        query = query.filter(User.campaigner_enabled.is_(False))
    elif role == "ngo":
        query = query.filter(User.id.in_(db.session.query(Organization.owner_user_id)))
    elif role == "admin":
        query = query.filter(
            User.id.in_(
                db.session.query(StaffRole.user_id).filter(
                    StaffRole.role == "admin",
                    StaffRole.revoked_at.is_(None),
                )
            )
        )

    accounts = query.order_by(User.created_at.desc()).limit(100).all()
    if request.args.get("format") == "json":
        admin_ids = {
            user_id
            for (user_id,) in db.session.query(StaffRole.user_id).filter(
                StaffRole.role == "admin",
                StaffRole.revoked_at.is_(None),
            )
        }
        ngo_ids = {
            user_id
            for (user_id,) in db.session.query(Organization.owner_user_id)
        }
        return {
            "accounts": [
                {
                    "id": account.id,
                    "name": account.display_name,
                    "email": account.email or "",
                    "role": (
                        "admin" if account.id in admin_ids
                        else "ngo" if account.id in ngo_ids
                        else "campaigner" if account.campaigner_enabled
                        else "donor"
                    ),
                    "joined": account.created_at.strftime("%d %b %Y"),
                    "status": account.account_status,
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
    return render_template("admin/verification_logs.html")
