"""Campaign pages and the database-backed explore search."""
import re
import secrets
from datetime import date, datetime, timezone

from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import func, or_

from packages.models import Campaign, CampaignRevision, CampaignReport, CampaignUpdate, Category, Currency, Donation, User, VerificationCase, db

campaign = Blueprint("campaign", __name__, template_folder="templates", static_folder="static")


def _campaign_search():
    """Return active, approved campaigns matching the current search filters."""
    raised = (
        db.session.query(func.coalesce(func.sum(Donation.credited_amount_minor), 0))
        .filter(Donation.campaign_id == Campaign.id, Donation.status == "succeeded")
        .correlate(Campaign)
        .scalar_subquery()
    )
    query = (
        db.session.query(CampaignRevision, Campaign, raised.label("raised_minor"))
        .join(Campaign, Campaign.id == CampaignRevision.campaign_id)
        .filter(
            Campaign.status == "active",
            Campaign.deleted_at.is_(None),
            CampaignRevision.status == "approved",
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
        query = query.filter(
            db.session.query(VerificationCase.id)
            .filter(
                VerificationCase.subject_user_id == Campaign.owner_user_id,
                VerificationCase.status == "approved",
            )
            .exists()
        )

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
        }
        for revision, campaign_record, raised_minor in results
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
    return {
        "title": title,
        "description": description,
        "category_id": category.id,
        "goal_amount_minor": goal * 100,
        "currency_code": "NGN",
        "start_date": start_date,
        "end_date": end_date,
    }, None


def _new_campaign_slug(title):
    prefix = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:160] or "campaign"
    return f"{prefix}-{secrets.token_hex(3)}"


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
    return render_template("campaigns/detail.html")


@campaign.route("/report", methods=["GET", "POST"])
def report():
    campaigns = Campaign.query.filter_by(status="active", deleted_at=None).all()
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
            flash("Choose an active campaign and explain the concern.", "error")
            return render_template("campaigns/report.html", campaigns=campaigns), 400
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
    return render_template("campaigns/report.html", campaigns=campaigns)


# ---------- Campaigner ----------

@campaign.route("/create", methods=["GET", "POST"])
def create():
    account = _signed_in_user()
    if account is None:
        return redirect(url_for("user.login", next=request.path))
    categories = Category.query.filter_by(is_active=True).order_by(Category.name).all()
    if request.method == "POST":
        cover = request.files.get("cover")
        if cover and cover.filename:
            flash("Campaign images cannot be saved until file storage is configured.", "error")
            return render_template("campaigns/create.html", categories=categories), 503
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
        if action not in {"draft", "evidence"}:
            flash("Choose whether to save the draft or continue to evidence.", "error")
            return render_template("campaigns/create.html", categories=categories), 400
        campaign_record = Campaign(
            public_id=secrets.token_hex(16),
            slug=_new_campaign_slug(values["title"]),
            owner_user_id=account.id,
            created_by_user_id=account.id,
            status="draft",
        )
        revision = CampaignRevision(
            campaign=campaign_record,
            version_number=1,
            submitted_by_user_id=account.id,
            status="draft",
            **values,
        )
        db.session.add_all([campaign_record, revision])
        db.session.commit()
        flash("Your campaign draft was saved.", "success")
        if action == "evidence":
            flash("Evidence upload is not enabled until file storage is configured.", "error")
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
    records = (
        Campaign.query.filter_by(owner_user_id=account.id, deleted_at=None)
        .order_by(Campaign.created_at.desc())
        .all()
    )
    return render_template("campaigns/my_campaigns.html", campaigns=records)


@campaign.route("/manage")
def manage():
    return render_template("campaigns/manage.html")


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
    categories = Category.query.filter_by(is_active=True).order_by(Category.name).all()
    if request.method == "POST":
        cover = request.files.get("cover")
        if cover and cover.filename:
            flash("Campaign images cannot be saved until file storage is configured.", "error")
            return render_template("campaigns/edit.html", campaign=campaign_record, revision=revision, categories=categories), 503
        values, error = _campaign_form_values()
        action = request.form.get("action", "save")
        if error:
            flash(error, "error")
            return render_template("campaigns/edit.html", campaign=campaign_record, revision=revision, categories=categories), 400
        if action not in {"save", "submit"}:
            flash("Choose whether to save or submit the campaign.", "error")
            return render_template("campaigns/edit.html", campaign=campaign_record, revision=revision, categories=categories), 400
        if revision.status in {"approved", "rejected", "superseded"}:
            revision = CampaignRevision(
                campaign_id=campaign_record.id,
                version_number=revision.version_number + 1,
                submitted_by_user_id=account.id,
                status="draft",
                **values,
            )
            db.session.add(revision)
        else:
            for field, value in values.items():
                setattr(revision, field, value)
        if action == "submit":
            revision.status = "submitted"
            revision.submitted_at = datetime.now(timezone.utc)
            if campaign_record.status == "draft":
                campaign_record.status = "submitted"
        db.session.commit()
        flash("Your campaign changes were saved.", "success")
        return redirect(url_for("campaign.edit", campaign_id=campaign_record.id))
    return render_template("campaigns/edit.html", campaign=campaign_record, revision=revision, categories=categories)


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
        return redirect(url_for("campaign.my_campaigns"))
    return render_template("campaigns/post_update.html", campaign=campaign_record)
