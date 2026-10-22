"""Campaign pages and the database-backed explore search."""
import re
import secrets
from datetime import date, datetime, timezone
from pathlib import Path

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import func, or_
from sqlalchemy.exc import SQLAlchemyError

from packages.models import (
    Campaign,
    CampaignEvidence,
    CampaignReport,
    CampaignReview,
    CampaignRevision,
    CampaignUpdate,
    Category,
    Currency,
    Donation,
    ExpenseRequest,
    Organization,
    User,
    VerificationCase,
    db,
)

campaign = Blueprint("campaign", __name__, template_folder="templates", static_folder="static")


def _campaign_search():
    """Return active, approved campaigns matching the current search filters."""
    latest_approved_version = (
        db.session.query(func.max(CampaignRevision.version_number))
        .filter(
            CampaignRevision.campaign_id == Campaign.id,
            CampaignRevision.status == "approved",
        )
        .correlate(Campaign)
        .scalar_subquery()
    )
    raised = (
        db.session.query(func.coalesce(func.sum(Donation.credited_amount_minor), 0))
        .filter(Donation.campaign_id == Campaign.id, Donation.status == "succeeded")
        .correlate(Campaign)
        .scalar_subquery()
    )
    verified = db.session.query(VerificationCase.id).filter(
        VerificationCase.status == "approved",
        or_(
            VerificationCase.subject_user_id == Campaign.owner_user_id,
            VerificationCase.subject_organization_id == Campaign.owner_organization_id,
        ),
    ).exists()
    query = (
        db.session.query(
            CampaignRevision,
            Campaign,
            raised.label("raised_minor"),
            verified.label("is_verified"),
        )
        .join(Campaign, Campaign.id == CampaignRevision.campaign_id)
        .filter(
            Campaign.status == "active",
            Campaign.deleted_at.is_(None),
            CampaignRevision.status == "approved",
            CampaignRevision.version_number == latest_approved_version,
        )
    )

    search = request.args.get("q", "").strip()[:100]
    category_id = request.args.get("category_id", type=int)
    sort = request.args.get("sort", "newest")
    if search:
        pattern = f"%{search}%"
        query = query.filter(
            or_(
                CampaignRevision.title.ilike(pattern),
                CampaignRevision.description.ilike(pattern),
            )
        )
    if category_id:
        query = query.filter(CampaignRevision.category_id == category_id)
    if request.args.get("verified") == "1":
        query = query.filter(verified)

    if sort == "ending":
        query = query.order_by(CampaignRevision.end_date.asc())
    elif sort == "funded":
        query = query.order_by(raised.desc())
    elif sort == "goal":
        query = query.order_by(CampaignRevision.goal_amount_minor.asc())
    else:
        query = query.order_by(CampaignRevision.created_at.desc())
    return query.limit(60).all()


def _campaign_json(results):
    """Return only the fields the browser needs to redraw a campaign card."""
    return [
        {
            "slug": campaign_record.slug,
            "title": revision.title,
            "description": revision.description,
            "category": revision.category.name,
            "goal_minor": revision.goal_amount_minor,
            "raised_minor": int(raised_minor or 0),
            "verified": bool(is_verified),
            "cover_image_url": revision.cover_image_url,
        }
        for revision, campaign_record, raised_minor, is_verified in results
    ]


def _signed_in_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return db.session.get(User, user_id)


def _campaign_for_owner(campaign_id, user_id):
    return Campaign.query.filter_by(
        id=campaign_id,
        owner_user_id=user_id,
        deleted_at=None,
    ).first()


def _campaign_form_values():
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    category_id = request.form.get("category_id", type=int)
    goal = request.form.get("goal_amount", type=int)
    try:
        start_date = date.fromisoformat(request.form.get("start_date", ""))
        end_date = date.fromisoformat(request.form.get("end_date", ""))
    except ValueError:
        return None, "Enter valid campaign dates."
    category = db.session.get(Category, category_id) if category_id else None
    if not title or not description or len(title) > 180 or len(description) > 8000:
        return None, "Enter a title and story within the allowed length."
    if category is None or not category.is_active:
        return None, "Choose an active campaign category."
    if goal is None or goal < 1000:
        return None, "The campaign goal must be at least ₦1,000."
    if end_date < start_date:
        return None, "The end date must be on or after the start date."
    net_goal_amount_minor = goal * 100
    platform_fee_minor = (net_goal_amount_minor + 9) // 10
    return {
        "title": title,
        "description": description,
        "category_id": category.id,
        "goal_amount_minor": net_goal_amount_minor + platform_fee_minor,
        "currency_code": "NGN",
        "start_date": start_date,
        "end_date": end_date,
    }, None


def _new_campaign_slug(title):
    prefix = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:160] or "campaign"
    return f"{prefix}-{secrets.token_hex(3)}"


def _save_campaign_cover():
    cover = request.files.get("cover")
    if cover is None or not cover.filename:
        return None, None, None

    image_data = cover.read(5 * 1024 * 1024 + 1)
    if len(image_data) > 5 * 1024 * 1024:
        return None, None, "Campaign images must be 5 MB or smaller."

    if image_data.startswith(b"\x89PNG\r\n\x1a\n"):
        extension = ".png"
    elif image_data.startswith(b"\xff\xd8\xff"):
        extension = ".jpg"
    elif image_data[:4] == b"RIFF" and image_data[8:12] == b"WEBP":
        extension = ".webp"
    else:
        return None, None, "Choose a valid JPG, PNG, or WebP image."

    upload_folder = Path(current_app.root_path).parent / "static" / "image_uploads"
    upload_folder.mkdir(parents=True, exist_ok=True)
    filename = f"{secrets.token_hex(16)}{extension}"
    file_path = upload_folder / filename
    with file_path.open("xb") as image_file:
        image_file.write(image_data)
    return url_for("uploaded_campaign_image", filename=filename), file_path, None


def _remove_campaign_cover_file(file_path):
    if file_path and file_path.exists():
        file_path.unlink()


# ---------- Public ----------

@campaign.route("/")
@campaign.route("/explore")
def explore():
    results = _campaign_search()
    if request.args.get("format") == "json":
        return jsonify(_campaign_json(results))
    categories = Category.query.filter_by(is_active=True).order_by(Category.name).all()
    return render_template(
        "campaigns/explore.html",
        campaigns=results,
        categories=categories,
        search=request.args.get("q", ""),
        selected_category=request.args.get("category_id", ""),
        selected_sort=request.args.get("sort", "newest"),
        verified=request.args.get("verified") == "1",
    )


@campaign.route("/search")
def search():
    return jsonify(_campaign_json(_campaign_search()))


@campaign.route("/detail")
def detail():
    slug = request.args.get("slug", "").strip()
    if not slug:
        return redirect(url_for("campaign.explore"))

    campaign_record = Campaign.query.filter_by(
        slug=slug,
        status="active",
        deleted_at=None,
    ).first_or_404()
    revision = CampaignRevision.query.filter_by(
        campaign_id=campaign_record.id,
        status="approved",
    ).order_by(CampaignRevision.version_number.desc()).first_or_404()

    owner_organization = campaign_record.owner_organization
    organizer = campaign_record.owner_user or (
        owner_organization.owner if owner_organization else None
    )
    organization = owner_organization
    if organization and (
        organization.organization_status != "active"
        or organization.deleted_at is not None
    ):
        organization = None

    verification_filter = (
        {"subject_organization_id": owner_organization.id}
        if owner_organization
        else {"subject_user_id": campaign_record.owner_user_id}
    )
    verified = VerificationCase.query.filter_by(
        **verification_filter,
        status="approved",
    ).first() is not None

    donations = Donation.query.filter_by(
        campaign_id=campaign_record.id,
        status="succeeded",
    )
    raised_minor = db.session.query(
        func.coalesce(func.sum(Donation.credited_amount_minor), 0)
    ).filter(
        Donation.campaign_id == campaign_record.id,
        Donation.status == "succeeded",
    ).scalar() or 0
    donor_count = donations.count()
    recent_donations = donations.order_by(Donation.created_at.desc()).limit(12).all()

    expenses = ExpenseRequest.query.filter_by(
        campaign_id=campaign_record.id,
        status="paid",
    ).order_by(ExpenseRequest.submitted_at.desc()).all()
    paid_minor = sum(expense.amount_minor for expense in expenses)
    committed_minor = db.session.query(
        func.coalesce(func.sum(ExpenseRequest.amount_minor), 0)
    ).filter(
        ExpenseRequest.campaign_id == campaign_record.id,
        ExpenseRequest.status.in_(("approved", "paid")),
    ).scalar() or 0
    updates = CampaignUpdate.query.filter_by(
        campaign_id=campaign_record.id,
        deleted_at=None,
    ).filter(
        CampaignUpdate.published_at.isnot(None),
    ).order_by(CampaignUpdate.published_at.desc()).all()
    evidence = CampaignEvidence.query.filter_by(
        campaign_id=campaign_record.id,
        visibility="approved_public",
    ).order_by(CampaignEvidence.created_at.desc()).all()
    review = CampaignReview.query.filter_by(
        revision_id=revision.id,
        decision="approved",
    ).order_by(CampaignReview.decided_at.desc()).first()

    progress = min(100, round(int(raised_minor) * 100 / revision.goal_amount_minor))
    days_left = (
        max((revision.end_date - date.today()).days, 0)
        if revision.end_date
        else None
    )
    return render_template(
        "campaigns/detail.html",
        campaign=campaign_record,
        revision=revision,
        organizer=organizer,
        organization=organization,
        verified=verified,
        raised_minor=int(raised_minor),
        paid_minor=paid_minor,
        available_minor=max(
            min(int(raised_minor), revision.goal_amount_minor * 10 // 11) - int(committed_minor),
            0,
        ),
        progress=progress,
        reached_target=int(raised_minor) >= revision.goal_amount_minor,
        days_left=days_left,
        donor_count=donor_count,
        recent_donations=recent_donations,
        expenses=expenses,
        updates=updates,
        evidence=evidence,
        review=review,
    )


@campaign.route("/report", methods=["GET", "POST"])
def report():
    campaigns = Campaign.query.filter_by(status="active", deleted_at=None).all()
    selected_campaign_id = request.args.get("campaign_id", type=int)
    campaign_by_id = {record.id: record for record in campaigns}
    selected_campaign = campaign_by_id.get(selected_campaign_id)
    if selected_campaign is None:
        selected_campaign_id = None
    campaign_titles = {}
    for campaign_record in campaigns:
        revision = CampaignRevision.query.filter_by(
            campaign_id=campaign_record.id,
            status="approved",
        ).order_by(CampaignRevision.version_number.desc()).first()
        campaign_titles[campaign_record.id] = (
            revision.title if revision else campaign_record.slug
        )
    if request.method == "POST":
        campaign_record = db.session.get(Campaign, request.form.get("campaign_id", type=int))
        reason = request.form.get("reason", "")
        details = request.form.get("details", "").strip()
        account = _signed_in_user()
        reporter_email = account.email if account else request.form.get("email", "").strip().lower()
        if (
            campaign_record not in campaigns
            or reason not in {"fraud", "misleading", "misuse", "inappropriate", "other"}
            or not details
            or not reporter_email
            or "@" not in reporter_email
        ):
            selected_campaign = campaign_by_id.get(
                campaign_record.id if campaign_record else None
            )
            flash("Choose an active campaign and explain the concern.", "error")
            return render_template(
                "campaigns/report.html",
                campaigns=campaigns,
                campaign_titles=campaign_titles,
                selected_campaign_id=selected_campaign.id if selected_campaign else None,
                selected_campaign=selected_campaign,
            ), 400
        db.session.add(
            CampaignReport(
                campaign_id=campaign_record.id,
                reporter_user_id=account.id if account else None,
                reporter_email=reporter_email,
                reason=reason,
                details=details[:5000],
            )
        )
        db.session.commit()
        flash("Thank you. Your report has been sent for review.", "success")
        return redirect(url_for("campaign.explore"))
    return render_template(
        "campaigns/report.html",
        campaigns=campaigns,
        campaign_titles=campaign_titles,
        selected_campaign_id=selected_campaign_id,
        selected_campaign=selected_campaign,
    )


# ---------- Campaigner ----------

@campaign.route("/create", methods=["GET", "POST"])
def create():
    account = _signed_in_user()
    if account is None:
        return redirect(url_for("user.login", next=request.path))
    if not account.campaigner_enabled:
        flash("Campaign creation is only available to campaigner accounts.", "error")
        return redirect(url_for("user.dashboard"))
    categories = Category.query.filter_by(is_active=True).order_by(Category.name).all()
    if request.method == "POST":
        values, error = _campaign_form_values()
        currency = db.session.get(Currency, "NGN")
        if error or (currency is not None and not currency.is_enabled):
            flash(error or "NGN is disabled for campaigns.", "error")
            return render_template("campaigns/create.html", categories=categories), 400
        if currency is None:
            currency = Currency(code="NGN", name="Naira", minor_unit_exponent=2, is_enabled=True)
            db.session.add(currency)
            db.session.flush()

        action = request.form.get("action", "draft")
        if action not in {"draft", "submit"}:
            flash("Choose whether to save a draft or submit the campaign for review.", "error")
            return render_template("campaigns/create.html", categories=categories), 400
        cover_image_url, cover_file_path, cover_error = _save_campaign_cover()
        if cover_error:
            flash(cover_error, "error")
            return render_template("campaigns/create.html", categories=categories), 400
        campaign_record = Campaign(
            public_id=secrets.token_hex(16),
            slug=_new_campaign_slug(values["title"]),
            owner_user_id=account.id,
            created_by_user_id=account.id,
            status="submitted" if action == "submit" else "draft",
        )
        revision = CampaignRevision(
            campaign=campaign_record,
            version_number=1,
            submitted_by_user_id=account.id,
            status="submitted" if action == "submit" else "draft",
            submitted_at=datetime.now(timezone.utc) if action == "submit" else None,
            cover_image_url=cover_image_url,
            **values,
        )
        db.session.add_all([campaign_record, revision])
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            _remove_campaign_cover_file(cover_file_path)
            raise
        flash(
            "Your campaign was submitted for review."
            if action == "submit"
            else "Your campaign draft was saved.",
            "success",
        )
        return redirect(url_for("campaign.edit", campaign_id=campaign_record.id))
    return render_template("campaigns/create.html", categories=categories)


@campaign.route("/submitted")
def submitted():
    return render_template("campaigns/submitted.html")


@campaign.route("/my_campaigns")
def my_campaigns():
    account = _signed_in_user()
    if account is None:
        return redirect(url_for("user.login", next=request.path))
    if not account.campaigner_enabled:
        flash("The campaign dashboard is only available to campaigner accounts.", "error")
        return redirect(url_for("user.dashboard"))
    selected_status = request.args.get("status", "all")
    records_query = Campaign.query.filter_by(owner_user_id=account.id, deleted_at=None)
    status_filters = {
        "live": "active",
        "review": "submitted",
        "drafts": "draft",
        "completed": "closed",
    }
    if selected_status in status_filters:
        records_query = records_query.filter_by(status=status_filters[selected_status])
    else:
        selected_status = "all"
    records = records_query.order_by(Campaign.created_at.desc()).all()
    return render_template(
        "campaigns/my_campaigns.html",
        campaigns=records,
        selected_status=selected_status,
    )


@campaign.route("/manage")
def manage():
    account = _signed_in_user()
    if account is None:
        return redirect(url_for("user.login", next=request.full_path))
    campaign_id = request.args.get("campaign_id", type=int)
    campaign_record = _campaign_for_owner(campaign_id, account.id) if campaign_id else None
    if campaign_record is None:
        flash("Choose one of your campaigns to manage.", "error")
        return redirect(url_for("campaign.my_campaigns"))

    revision = CampaignRevision.query.filter_by(
        campaign_id=campaign_record.id,
    ).order_by(CampaignRevision.version_number.desc()).first()
    if revision is None:
        flash("This campaign has no saved details yet.", "error")
        return redirect(url_for("campaign.my_campaigns"))
    raised_minor = db.session.query(
        func.coalesce(func.sum(Donation.credited_amount_minor), 0)
    ).filter(
        Donation.campaign_id == campaign_record.id,
        Donation.status == "succeeded",
    ).scalar() or 0
    expenses = ExpenseRequest.query.filter_by(
        campaign_id=campaign_record.id,
    ).order_by(ExpenseRequest.submitted_at.desc()).all()
    paid_minor = sum(
        expense.amount_minor for expense in expenses if expense.status == "paid"
    )
    committed_minor = sum(
        expense.amount_minor
        for expense in expenses
        if expense.status in {"approved", "paid"}
    )
    updates = CampaignUpdate.query.filter_by(
        campaign_id=campaign_record.id,
        deleted_at=None,
    ).order_by(CampaignUpdate.created_at.desc()).all()
    donations = Donation.query.filter_by(
        campaign_id=campaign_record.id,
        status="succeeded",
    ).order_by(Donation.created_at.desc()).limit(10).all()
    return render_template(
        "campaigns/manage.html",
        campaign=campaign_record,
        revision=revision,
        raised_minor=int(raised_minor),
        paid_minor=paid_minor,
        available_minor=max(
            min(int(raised_minor), revision.goal_amount_minor * 10 // 11) - committed_minor,
            0,
        ),
        progress=min(100, round(int(raised_minor) * 100 / revision.goal_amount_minor)),
        expenses=expenses,
        updates=updates,
        donations=donations,
    )


@campaign.route("/edit", methods=["GET", "POST"])
def edit():
    account = _signed_in_user()
    if account is None:
        return redirect(url_for("user.login", next=request.path))
    campaign_id = request.values.get("campaign_id", type=int)
    campaign_record = _campaign_for_owner(campaign_id, account.id) if campaign_id else None
    if campaign_record is None:
        flash("Choose one of your campaigns to edit.", "error")
        return redirect(url_for("campaign.my_campaigns"))
    revision = (
        CampaignRevision.query.filter_by(campaign_id=campaign_record.id)
        .order_by(CampaignRevision.version_number.desc())
        .first()
    )
    if revision is None:
        flash("This campaign has no editable draft.", "error")
        return redirect(url_for("campaign.my_campaigns"))
    review = CampaignReview.query.filter_by(
        revision_id=revision.id,
    ).order_by(CampaignReview.decided_at.desc()).first()
    categories = Category.query.filter_by(is_active=True).order_by(Category.name).all()
    if request.method == "POST":
        values, error = _campaign_form_values()
        action = request.form.get("action", "save")
        if error:
            flash(error, "error")
            return render_template(
                "campaigns/edit.html",
                campaign=campaign_record,
                revision=revision,
                categories=categories,
                review=review,
            ), 400
        if action not in {"save", "submit"}:
            flash("Choose whether to save or submit the campaign.", "error")
            return render_template(
                "campaigns/edit.html",
                campaign=campaign_record,
                revision=revision,
                categories=categories,
                review=review,
            ), 400
        cover_image_url, cover_file_path, cover_error = _save_campaign_cover()
        if cover_error:
            flash(cover_error, "error")
            return render_template(
                "campaigns/edit.html",
                campaign=campaign_record,
                revision=revision,
                categories=categories,
                review=review,
            ), 400
        if revision.status in {"approved", "rejected", "superseded"}:
            revision = CampaignRevision(
                campaign_id=campaign_record.id,
                version_number=revision.version_number + 1,
                submitted_by_user_id=account.id,
                status="draft",
                cover_image_url=revision.cover_image_url,
                **values,
            )
            db.session.add(revision)
        else:
            for field, value in values.items():
                setattr(revision, field, value)
        if cover_image_url:
            revision.cover_image_url = cover_image_url
        if action == "submit":
            revision.status = "submitted"
            revision.submitted_at = datetime.now(timezone.utc)
            if campaign_record.status == "draft":
                campaign_record.status = "submitted"
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            _remove_campaign_cover_file(cover_file_path)
            raise
        flash("Your campaign changes were saved.", "success")
        return redirect(url_for("campaign.edit", campaign_id=campaign_record.id))
    return render_template(
        "campaigns/edit.html",
        campaign=campaign_record,
        revision=revision,
        categories=categories,
        review=review,
    )


@campaign.route("/evidence", methods=["GET", "POST"])
def evidence():
    if request.method == "POST":
        flash("Campaign evidence cannot be saved until secure file storage is configured.", "error")
        return render_template("campaigns/evidence.html"), 503
    return render_template("campaigns/evidence.html")


@campaign.route("/post_update", methods=["GET", "POST"])
def post_update():
    account = _signed_in_user()
    if account is None:
        return redirect(url_for("user.login", next=request.path))
    campaign_id = request.values.get("campaign_id", type=int)
    campaign_record = _campaign_for_owner(campaign_id, account.id) if campaign_id else None
    if campaign_record is None:
        flash("Choose one of your campaigns before posting an update.", "error")
        return redirect(url_for("campaign.my_campaigns"))
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        content = request.form.get("content", "").strip()
        if not title or not content or len(title) > 180:
            flash("Enter an update headline and message.", "error")
            return render_template("campaigns/post_update.html", campaign=campaign_record), 400
        db.session.add(
            CampaignUpdate(
                campaign_id=campaign_record.id,
                author_user_id=account.id,
                title=title,
                content=content,
                published_at=datetime.now(timezone.utc),
            )
        )
        db.session.commit()
        flash("Your campaign update was published.", "success")
        return redirect(url_for("campaign.manage", campaign_id=campaign_record.id))
    return render_template("campaigns/post_update.html", campaign=campaign_record)
