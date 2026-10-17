"""Database extension and persistence models for Vouch.

Uploaded files live in private object storage. Passwords, payment-card data, raw
bank account details, and unencrypted provider credentials do not belong here.
"""

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import CheckConstraint, Index, UniqueConstraint


db = SQLAlchemy()


class TimestampMixin:
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.current_timestamp())
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        server_default=db.func.current_timestamp(),
        onupdate=db.func.current_timestamp(),
    )


class User(TimestampMixin, db.Model):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("account_status IN ('active', 'suspended', 'closed')", name="ck_users_account_status"),
        CheckConstraint("email IS NOT NULL OR anonymized_at IS NOT NULL", name="ck_users_email_or_anonymized"),
        CheckConstraint("password_hash IS NOT NULL OR anonymized_at IS NOT NULL", name="ck_users_password_or_anonymized"),
        Index("ix_users_deleted_at", "deleted_at"),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    email = db.Column(db.String(320), nullable=True, unique=True)
    password_hash = db.Column(db.String(255), nullable=True)
    display_name = db.Column(db.String(120), nullable=False)
    bio = db.Column(db.String(500), nullable=True)
    phone_number = db.Column(db.String(32), nullable=True)
    legal_name = db.Column(db.String(200), nullable=True)
    address_country = db.Column(db.String(100), nullable=True)
    address_street = db.Column(db.String(500), nullable=True)
    address_state = db.Column(db.String(120), nullable=True)
    account_status = db.Column(db.String(16), nullable=False, server_default="active")
    email_verified_at = db.Column(db.DateTime(timezone=True), nullable=True)
    campaigner_enabled = db.Column(db.Boolean, nullable=False, server_default=db.false())
    deleted_at = db.Column(db.DateTime(timezone=True), nullable=True)
    anonymized_at = db.Column(db.DateTime(timezone=True), nullable=True)

    owned_campaigns = db.relationship(
        "Campaign", back_populates="owner_user", foreign_keys="Campaign.owner_user_id"
    )
    organization_memberships = db.relationship(
        "OrganizationMembership", back_populates="user", cascade="all, delete-orphan"
    )
    donations = db.relationship("Donation", back_populates="donor")
    submitted_expenses = db.relationship("ExpenseRequest", back_populates="submitter")
    staff_roles = db.relationship(
        "StaffRole",
        back_populates="user",
        foreign_keys="StaffRole.user_id",
        cascade="all, delete-orphan",
    )
    avatar = db.relationship("UserAvatar", back_populates="user", uselist=False, cascade="all, delete-orphan")


class Organization(TimestampMixin, db.Model):
    __tablename__ = "organizations"
    __table_args__ = (
        CheckConstraint("organization_status IN ('pending', 'active', 'suspended', 'closed')", name="ck_organizations_status"),
        Index("ix_organizations_deleted_at", "deleted_at"),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    legal_name = db.Column(db.String(200), nullable=False)
    display_name = db.Column(db.String(120), nullable=False)
    registration_number = db.Column(db.String(80), nullable=True, unique=True)
    website_url = db.Column(db.String(2048), nullable=True)
    founded_date = db.Column(db.Date, nullable=True)
    tax_exempt_status = db.Column(db.Boolean, nullable=False, server_default=db.false())
    owner_user_id = db.Column(db.BigInteger, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    organization_status = db.Column(db.String(16), nullable=False, server_default="pending")
    deleted_at = db.Column(db.DateTime(timezone=True), nullable=True)

    owner = db.relationship("User", foreign_keys=[owner_user_id])
    memberships = db.relationship(
        "OrganizationMembership", back_populates="organization", cascade="all, delete-orphan"
    )
    campaigns = db.relationship("Campaign", back_populates="owner_organization")


class OrganizationMembership(TimestampMixin, db.Model):
    __tablename__ = "organization_memberships"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", name="uq_org_membership"),
        CheckConstraint("membership_role IN ('owner', 'admin', 'member')", name="ck_org_membership_role"),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    organization_id = db.Column(db.BigInteger, db.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    user_id = db.Column(db.BigInteger, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    membership_role = db.Column(db.String(16), nullable=False, server_default="member")
    is_active = db.Column(db.Boolean, nullable=False, server_default=db.true())

    organization = db.relationship("Organization", back_populates="memberships")
    user = db.relationship("User", back_populates="organization_memberships")


class StaffRole(TimestampMixin, db.Model):
    __tablename__ = "staff_roles"
    __table_args__ = (
        UniqueConstraint("user_id", "role", name="uq_staff_role"),
        CheckConstraint(
            "role IN ('admin', 'campaign_reviewer', 'verification_reviewer', 'finance')",
            name="ck_staff_role",
        ),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    user_id = db.Column(db.BigInteger, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    role = db.Column(db.String(32), nullable=False)
    granted_by_user_id = db.Column(db.BigInteger, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    revoked_at = db.Column(db.DateTime(timezone=True), nullable=True)

    user = db.relationship("User", back_populates="staff_roles", foreign_keys=[user_id])
    granted_by = db.relationship("User", foreign_keys=[granted_by_user_id])


class Currency(db.Model):
    __tablename__ = "currencies"
    __table_args__ = (
        CheckConstraint("minor_unit_exponent BETWEEN 0 AND 4", name="ck_currency_minor_unit_exponent"),
    )

    code = db.Column(db.String(3), primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    minor_unit_exponent = db.Column(db.SmallInteger, nullable=False)
    is_enabled = db.Column(db.Boolean, nullable=False, server_default=db.false())


class Category(TimestampMixin, db.Model):
    __tablename__ = "categories"
    __table_args__ = (UniqueConstraint("slug", name="uq_category_slug"),)

    id = db.Column(db.BigInteger, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    slug = db.Column(db.String(120), nullable=False)
    description = db.Column(db.String(500), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, server_default=db.true())


class FileAsset(TimestampMixin, db.Model):
    __tablename__ = "file_assets"
    __table_args__ = (
        CheckConstraint("byte_size > 0", name="ck_file_asset_positive_size"),
        CheckConstraint("length(sha256_hex) = 64", name="ck_file_asset_sha256_length"),
        CheckConstraint("storage_key IS NOT NULL OR deleted_at IS NOT NULL", name="ck_file_asset_key_or_deleted"),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    storage_key = db.Column(db.String(512), nullable=True, unique=True)
    original_filename = db.Column(db.String(255), nullable=True)
    detected_content_type = db.Column(db.String(127), nullable=False)
    byte_size = db.Column(db.BigInteger, nullable=False)
    sha256_hex = db.Column(db.String(64), nullable=False)
    uploaded_by_user_id = db.Column(db.BigInteger, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    deleted_at = db.Column(db.DateTime(timezone=True), nullable=True)

    uploaded_by = db.relationship("User", foreign_keys=[uploaded_by_user_id])


class UserAvatar(TimestampMixin, db.Model):
    __tablename__ = "user_avatars"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_user_avatar_user"),
        UniqueConstraint("file_asset_id", name="uq_user_avatar_file"),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    user_id = db.Column(db.BigInteger, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    file_asset_id = db.Column(db.BigInteger, db.ForeignKey("file_assets.id", ondelete="RESTRICT"), nullable=False)

    user = db.relationship("User", back_populates="avatar")
    file_asset = db.relationship("FileAsset")


class VerificationCase(TimestampMixin, db.Model):
    __tablename__ = "verification_cases"
    __table_args__ = (
        CheckConstraint(
            "(subject_user_id IS NOT NULL AND subject_organization_id IS NULL) OR "
            "(subject_user_id IS NULL AND subject_organization_id IS NOT NULL)",
            name="ck_verification_case_one_subject",
        ),
        CheckConstraint(
            "status IN ('submitted', 'in_review', 'changes_requested', 'approved', 'rejected', 'closed')",
            name="ck_verification_case_status",
        ),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    subject_user_id = db.Column(db.BigInteger, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    subject_organization_id = db.Column(
        db.BigInteger, db.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=True
    )
    status = db.Column(db.String(24), nullable=False, server_default="submitted")
    submitted_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.current_timestamp())
    closed_at = db.Column(db.DateTime(timezone=True), nullable=True)

    subject_user = db.relationship("User", foreign_keys=[subject_user_id])
    subject_organization = db.relationship("Organization", foreign_keys=[subject_organization_id])
    documents = db.relationship("VerificationDocument", back_populates="case", cascade="all, delete-orphan")
    reviews = db.relationship("VerificationReview", back_populates="case")


class VerificationDocument(TimestampMixin, db.Model):
    __tablename__ = "verification_documents"
    __table_args__ = (
        UniqueConstraint("file_asset_id", name="uq_verification_document_file"),
        CheckConstraint(
            "document_type IN ('identity', 'proof_of_address', 'organization_registration', 'other')",
            name="ck_verification_document_type",
        ),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    case_id = db.Column(db.BigInteger, db.ForeignKey("verification_cases.id", ondelete="RESTRICT"), nullable=False)
    file_asset_id = db.Column(db.BigInteger, db.ForeignKey("file_assets.id", ondelete="RESTRICT"), nullable=False)
    document_type = db.Column(db.String(32), nullable=False)
    received_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.current_timestamp())

    case = db.relationship("VerificationCase", back_populates="documents")
    file_asset = db.relationship("FileAsset")


class VerificationReview(TimestampMixin, db.Model):
    __tablename__ = "verification_reviews"
    __table_args__ = (
        UniqueConstraint("case_id", "reviewer_user_id", name="uq_verification_review_reviewer"),
        CheckConstraint(
            "decision IN ('approved', 'changes_requested', 'rejected')",
            name="ck_verification_review_decision",
        ),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    case_id = db.Column(db.BigInteger, db.ForeignKey("verification_cases.id", ondelete="RESTRICT"), nullable=False)
    reviewer_user_id = db.Column(db.BigInteger, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    decision = db.Column(db.String(24), nullable=False)
    public_notes = db.Column(db.Text, nullable=True)
    internal_notes = db.Column(db.Text, nullable=True)
    decided_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.current_timestamp())

    case = db.relationship("VerificationCase", back_populates="reviews")
    reviewer = db.relationship("User")


class Campaign(TimestampMixin, db.Model):
    __tablename__ = "campaigns"
    __table_args__ = (
        UniqueConstraint("public_id", name="uq_campaign_public_id"),
        CheckConstraint(
            "(owner_user_id IS NOT NULL AND owner_organization_id IS NULL) OR "
            "(owner_user_id IS NULL AND owner_organization_id IS NOT NULL)",
            name="ck_campaign_one_owner",
        ),
        CheckConstraint("status IN ('draft', 'submitted', 'active', 'paused', 'closed', 'rejected')", name="ck_campaign_status"),
        Index("ix_campaigns_status_created", "status", "created_at"),
        Index("ix_campaigns_owner_user", "owner_user_id", "deleted_at"),
        Index("ix_campaigns_owner_org", "owner_organization_id", "deleted_at"),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    public_id = db.Column(db.String(32), nullable=False)
    slug = db.Column(db.String(180), nullable=False, unique=True)
    owner_user_id = db.Column(db.BigInteger, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True)
    owner_organization_id = db.Column(
        db.BigInteger, db.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=True
    )
    status = db.Column(db.String(16), nullable=False, server_default="draft")
    created_by_user_id = db.Column(db.BigInteger, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    deleted_at = db.Column(db.DateTime(timezone=True), nullable=True)

    owner_user = db.relationship("User", back_populates="owned_campaigns", foreign_keys=[owner_user_id])
    owner_organization = db.relationship("Organization", back_populates="campaigns")
    created_by = db.relationship("User", foreign_keys=[created_by_user_id])
    revisions = db.relationship("CampaignRevision", back_populates="campaign", cascade="all, delete-orphan")
    donations = db.relationship("Donation", back_populates="campaign")
    expense_requests = db.relationship("ExpenseRequest", back_populates="campaign")
    updates = db.relationship("CampaignUpdate", back_populates="campaign")
    reports = db.relationship("CampaignReport", back_populates="campaign")
    evidence = db.relationship("CampaignEvidence", back_populates="campaign")


class CampaignRevision(TimestampMixin, db.Model):
    __tablename__ = "campaign_revisions"
    __table_args__ = (
        UniqueConstraint("campaign_id", "version_number", name="uq_campaign_revision_number"),
        CheckConstraint("version_number > 0", name="ck_campaign_revision_positive_version"),
        CheckConstraint("goal_amount_minor > 0", name="ck_campaign_revision_positive_goal"),
        CheckConstraint("end_date IS NULL OR start_date IS NULL OR end_date >= start_date", name="ck_campaign_revision_dates"),
        CheckConstraint(
            "status IN ('draft', 'submitted', 'changes_requested', 'approved', 'rejected', 'superseded')",
            name="ck_campaign_revision_status",
        ),
        Index("ix_campaign_revision_review_queue", "status", "created_at"),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    campaign_id = db.Column(db.BigInteger, db.ForeignKey("campaigns.id", ondelete="RESTRICT"), nullable=False)
    version_number = db.Column(db.Integer, nullable=False)
    title = db.Column(db.String(180), nullable=False)
    description = db.Column(db.Text, nullable=False)
    category_id = db.Column(db.BigInteger, db.ForeignKey("categories.id", ondelete="RESTRICT"), nullable=False)
    goal_amount_minor = db.Column(db.BigInteger, nullable=False)
    currency_code = db.Column(db.String(3), db.ForeignKey("currencies.code", ondelete="RESTRICT"), nullable=False)
    start_date = db.Column(db.Date, nullable=True)
    end_date = db.Column(db.Date, nullable=True)
    cover_file_asset_id = db.Column(db.BigInteger, db.ForeignKey("file_assets.id", ondelete="RESTRICT"), nullable=True)
    submitted_by_user_id = db.Column(db.BigInteger, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    status = db.Column(db.String(24), nullable=False, server_default="draft")
    submitted_at = db.Column(db.DateTime(timezone=True), nullable=True)
    approved_at = db.Column(db.DateTime(timezone=True), nullable=True)

    campaign = db.relationship("Campaign", back_populates="revisions")
    category = db.relationship("Category")
    currency = db.relationship("Currency")
    cover_file = db.relationship("FileAsset")
    submitted_by = db.relationship("User")
    reviews = db.relationship("CampaignReview", back_populates="revision", cascade="all, delete-orphan")


class CampaignReview(TimestampMixin, db.Model):
    __tablename__ = "campaign_reviews"
    __table_args__ = (
        UniqueConstraint("revision_id", "reviewer_user_id", name="uq_campaign_review_reviewer"),
        CheckConstraint(
            "decision IN ('approved', 'changes_requested', 'rejected')",
            name="ck_campaign_review_decision",
        ),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    revision_id = db.Column(db.BigInteger, db.ForeignKey("campaign_revisions.id", ondelete="RESTRICT"), nullable=False)
    reviewer_user_id = db.Column(db.BigInteger, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    decision = db.Column(db.String(24), nullable=False)
    public_notes = db.Column(db.Text, nullable=True)
    internal_notes = db.Column(db.Text, nullable=True)
    is_final = db.Column(db.Boolean, nullable=False, server_default=db.true())
    decided_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.current_timestamp())

    revision = db.relationship("CampaignRevision", back_populates="reviews")
    reviewer = db.relationship("User")


class CampaignEvidence(TimestampMixin, db.Model):
    __tablename__ = "campaign_evidence"
    __table_args__ = (
        UniqueConstraint("file_asset_id", name="uq_campaign_evidence_file"),
        CheckConstraint("visibility IN ('private', 'pending_public', 'approved_public')", name="ck_campaign_evidence_visibility"),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    campaign_id = db.Column(db.BigInteger, db.ForeignKey("campaigns.id", ondelete="RESTRICT"), nullable=False)
    file_asset_id = db.Column(db.BigInteger, db.ForeignKey("file_assets.id", ondelete="RESTRICT"), nullable=False)
    uploaded_by_user_id = db.Column(db.BigInteger, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    document_type = db.Column(db.String(40), nullable=False)
    visibility = db.Column(db.String(24), nullable=False, server_default="private")
    reviewed_by_user_id = db.Column(db.BigInteger, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewed_at = db.Column(db.DateTime(timezone=True), nullable=True)

    campaign = db.relationship("Campaign", back_populates="evidence")
    file_asset = db.relationship("FileAsset")
    uploaded_by = db.relationship("User", foreign_keys=[uploaded_by_user_id])
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_user_id])


class CampaignUpdate(TimestampMixin, db.Model):
    __tablename__ = "campaign_updates"
    __table_args__ = (Index("ix_campaign_updates_published", "campaign_id", "published_at"),)

    id = db.Column(db.BigInteger, primary_key=True)
    campaign_id = db.Column(db.BigInteger, db.ForeignKey("campaigns.id", ondelete="RESTRICT"), nullable=False)
    author_user_id = db.Column(db.BigInteger, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    title = db.Column(db.String(180), nullable=False)
    content = db.Column(db.Text, nullable=False)
    published_at = db.Column(db.DateTime(timezone=True), nullable=True)
    deleted_at = db.Column(db.DateTime(timezone=True), nullable=True)

    campaign = db.relationship("Campaign", back_populates="updates")
    author = db.relationship("User")


class CampaignReport(TimestampMixin, db.Model):
    __tablename__ = "campaign_reports"
    __table_args__ = (
        CheckConstraint(
            "reason IN ('fraud', 'misleading', 'misuse', 'inappropriate', 'other')",
            name="ck_campaign_report_reason",
        ),
        CheckConstraint("status IN ('open', 'investigating', 'resolved', 'dismissed')", name="ck_campaign_report_status"),
        Index("ix_campaign_reports_queue", "status", "created_at"),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    campaign_id = db.Column(db.BigInteger, db.ForeignKey("campaigns.id", ondelete="RESTRICT"), nullable=False)
    reporter_user_id = db.Column(db.BigInteger, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reporter_email = db.Column(db.String(320), nullable=True)
    reason = db.Column(db.String(24), nullable=False)
    details = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(24), nullable=False, server_default="open")
    assigned_to_user_id = db.Column(db.BigInteger, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    resolved_at = db.Column(db.DateTime(timezone=True), nullable=True)

    campaign = db.relationship("Campaign", back_populates="reports")
    reporter = db.relationship("User", foreign_keys=[reporter_user_id])
    assigned_to = db.relationship("User", foreign_keys=[assigned_to_user_id])


class Donation(TimestampMixin, db.Model):
    __tablename__ = "donations"
    __table_args__ = (
        UniqueConstraint("receipt_id", name="uq_donation_receipt_id"),
        CheckConstraint("intended_amount_minor > 0", name="ck_donation_positive_intended_amount"),
        CheckConstraint("donor_user_id IS NOT NULL OR guest_email IS NOT NULL", name="ck_donation_identity_or_receipt_email"),
        CheckConstraint(
            "status IN ('pending', 'succeeded', 'failed', 'partially_refunded', 'refunded', 'disputed')",
            name="ck_donation_status",
        ),
        Index("ix_donations_campaign_status", "campaign_id", "status", "created_at"),
        Index("ix_donations_donor_created", "donor_user_id", "created_at"),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    receipt_id = db.Column(db.String(40), nullable=False)
    campaign_id = db.Column(db.BigInteger, db.ForeignKey("campaigns.id", ondelete="RESTRICT"), nullable=False)
    donor_user_id = db.Column(db.BigInteger, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    guest_email = db.Column(db.String(320), nullable=True)
    is_anonymous = db.Column(db.Boolean, nullable=False, server_default=db.false())
    intended_amount_minor = db.Column(db.BigInteger, nullable=False)
    campaign_currency_code = db.Column(db.String(3), db.ForeignKey("currencies.code", ondelete="RESTRICT"), nullable=False)
    credited_amount_minor = db.Column(db.BigInteger, nullable=True)
    status = db.Column(db.String(24), nullable=False, server_default="pending")
    succeeded_at = db.Column(db.DateTime(timezone=True), nullable=True)

    campaign = db.relationship("Campaign", back_populates="donations")
    donor = db.relationship("User", back_populates="donations")
    campaign_currency = db.relationship("Currency")
    payment_attempts = db.relationship("PaymentAttempt", back_populates="donation", cascade="all, delete-orphan")
    refunds = db.relationship("Refund", back_populates="donation")


class PaymentAttempt(TimestampMixin, db.Model):
    __tablename__ = "payment_attempts"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_payment_attempt_idempotency"),
        UniqueConstraint("provider_reference", name="uq_payment_provider_reference"),
        CheckConstraint("charge_amount_minor > 0", name="ck_payment_positive_charge"),
        CheckConstraint(
            "status IN ('initialized', 'pending', 'succeeded', 'failed', 'cancelled')",
            name="ck_payment_attempt_status",
        ),
        Index("ix_payment_attempt_donation", "donation_id", "created_at"),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    donation_id = db.Column(db.BigInteger, db.ForeignKey("donations.id", ondelete="RESTRICT"), nullable=False)
    provider = db.Column(db.String(32), nullable=False, server_default="paystack")
    idempotency_key = db.Column(db.String(128), nullable=False)
    provider_reference = db.Column(db.String(128), nullable=True)
    status = db.Column(db.String(16), nullable=False, server_default="initialized")
    charge_amount_minor = db.Column(db.BigInteger, nullable=False)
    charge_currency_code = db.Column(db.String(3), db.ForeignKey("currencies.code", ondelete="RESTRICT"), nullable=False)
    settled_amount_minor = db.Column(db.BigInteger, nullable=True)
    settled_currency_code = db.Column(db.String(3), db.ForeignKey("currencies.code", ondelete="RESTRICT"), nullable=True)
    campaign_credit_amount_minor = db.Column(db.BigInteger, nullable=True)
    campaign_currency_code = db.Column(db.String(3), db.ForeignKey("currencies.code", ondelete="RESTRICT"), nullable=True)
    provider_fee_minor = db.Column(db.BigInteger, nullable=True)
    provider_fee_currency_code = db.Column(db.String(3), db.ForeignKey("currencies.code", ondelete="RESTRICT"), nullable=True)
    confirmed_at = db.Column(db.DateTime(timezone=True), nullable=True)

    donation = db.relationship("Donation", back_populates="payment_attempts")
    charge_currency = db.relationship("Currency", foreign_keys=[charge_currency_code])
    settled_currency = db.relationship("Currency", foreign_keys=[settled_currency_code])
    campaign_currency = db.relationship("Currency", foreign_keys=[campaign_currency_code])
    provider_fee_currency = db.relationship("Currency", foreign_keys=[provider_fee_currency_code])
    events = db.relationship("PaymentProviderEvent", back_populates="payment_attempt", cascade="all, delete-orphan")


class PaymentProviderEvent(TimestampMixin, db.Model):
    __tablename__ = "payment_provider_events"
    __table_args__ = (
        UniqueConstraint("provider", "provider_event_id", name="uq_payment_provider_event"),
        CheckConstraint("processing_status IN ('received', 'processed', 'failed')", name="ck_payment_event_processing"),
        Index("ix_payment_events_processing", "processing_status", "created_at"),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    payment_attempt_id = db.Column(
        db.BigInteger, db.ForeignKey("payment_attempts.id", ondelete="SET NULL"), nullable=True
    )
    provider = db.Column(db.String(32), nullable=False)
    provider_event_id = db.Column(db.String(128), nullable=False)
    event_type = db.Column(db.String(80), nullable=False)
    payload_sha256 = db.Column(db.String(64), nullable=False)
    processing_status = db.Column(db.String(16), nullable=False, server_default="received")
    processed_at = db.Column(db.DateTime(timezone=True), nullable=True)

    payment_attempt = db.relationship("PaymentAttempt", back_populates="events")


class Refund(TimestampMixin, db.Model):
    __tablename__ = "refunds"
    __table_args__ = (
        UniqueConstraint("provider_reference", name="uq_refund_provider_reference"),
        CheckConstraint("amount_minor > 0", name="ck_refund_positive_amount"),
        CheckConstraint("status IN ('requested', 'pending', 'succeeded', 'failed')", name="ck_refund_status"),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    donation_id = db.Column(db.BigInteger, db.ForeignKey("donations.id", ondelete="RESTRICT"), nullable=False)
    provider_reference = db.Column(db.String(128), nullable=True)
    amount_minor = db.Column(db.BigInteger, nullable=False)
    currency_code = db.Column(db.String(3), db.ForeignKey("currencies.code", ondelete="RESTRICT"), nullable=False)
    status = db.Column(db.String(16), nullable=False, server_default="requested")
    requested_by_user_id = db.Column(db.BigInteger, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    completed_at = db.Column(db.DateTime(timezone=True), nullable=True)

    donation = db.relationship("Donation", back_populates="refunds")
    currency = db.relationship("Currency")
    requested_by = db.relationship("User")


class PayoutRecipient(TimestampMixin, db.Model):
    __tablename__ = "payout_recipients"
    __table_args__ = (
        CheckConstraint("recipient_type IN ('vendor', 'institution', 'beneficiary')", name="ck_payout_recipient_type"),
        CheckConstraint("status IN ('active', 'disabled')", name="ck_payout_recipient_status"),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    created_by_user_id = db.Column(db.BigInteger, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    recipient_type = db.Column(db.String(20), nullable=False)
    display_name = db.Column(db.String(200), nullable=False)
    bank_code = db.Column(db.String(20), nullable=True)
    provider = db.Column(db.String(32), nullable=False, server_default="paystack")
    encrypted_recipient_code = db.Column(db.LargeBinary, nullable=False)
    recipient_code_key_version = db.Column(db.String(40), nullable=False)
    status = db.Column(db.String(16), nullable=False, server_default="active")
    deleted_at = db.Column(db.DateTime(timezone=True), nullable=True)

    created_by = db.relationship("User")
    expense_requests = db.relationship("ExpenseRequest", back_populates="recipient")


class ExpenseRequest(TimestampMixin, db.Model):
    __tablename__ = "expense_requests"
    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="ck_expense_positive_amount"),
        CheckConstraint(
            "status IN ('submitted', 'in_review', 'changes_requested', 'approved', 'rejected', 'paid', 'cancelled')",
            name="ck_expense_status",
        ),
        Index("ix_expense_review_queue", "status", "created_at"),
        Index("ix_expense_campaign_status", "campaign_id", "status"),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    campaign_id = db.Column(db.BigInteger, db.ForeignKey("campaigns.id", ondelete="RESTRICT"), nullable=False)
    submitted_by_user_id = db.Column(db.BigInteger, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    recipient_id = db.Column(db.BigInteger, db.ForeignKey("payout_recipients.id", ondelete="RESTRICT"), nullable=False)
    title = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text, nullable=False)
    amount_minor = db.Column(db.BigInteger, nullable=False)
    currency_code = db.Column(db.String(3), db.ForeignKey("currencies.code", ondelete="RESTRICT"), nullable=False)
    status = db.Column(db.String(24), nullable=False, server_default="submitted")
    submitted_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.current_timestamp())

    campaign = db.relationship("Campaign", back_populates="expense_requests")
    submitter = db.relationship("User", back_populates="submitted_expenses")
    recipient = db.relationship("PayoutRecipient", back_populates="expense_requests")
    currency = db.relationship("Currency")
    evidence = db.relationship("ExpenseEvidence", back_populates="expense_request", cascade="all, delete-orphan")
    reviews = db.relationship("ExpenseReview", back_populates="expense_request", cascade="all, delete-orphan")
    disbursement = db.relationship("Disbursement", back_populates="expense_request", uselist=False)


class ExpenseEvidence(TimestampMixin, db.Model):
    __tablename__ = "expense_evidence"
    __table_args__ = (
        UniqueConstraint("file_asset_id", name="uq_expense_evidence_file"),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    expense_request_id = db.Column(db.BigInteger, db.ForeignKey("expense_requests.id", ondelete="RESTRICT"), nullable=False)
    file_asset_id = db.Column(db.BigInteger, db.ForeignKey("file_assets.id", ondelete="RESTRICT"), nullable=False)
    is_public = db.Column(db.Boolean, nullable=False, server_default=db.false())
    public_approved_by_user_id = db.Column(db.BigInteger, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    public_approved_at = db.Column(db.DateTime(timezone=True), nullable=True)

    expense_request = db.relationship("ExpenseRequest", back_populates="evidence")
    file_asset = db.relationship("FileAsset")
    public_approved_by = db.relationship("User")


class ExpenseReview(TimestampMixin, db.Model):
    __tablename__ = "expense_reviews"
    __table_args__ = (
        UniqueConstraint("expense_request_id", "reviewer_user_id", name="uq_expense_review_reviewer"),
        CheckConstraint(
            "decision IN ('approved', 'needs_info', 'rejected')",
            name="ck_expense_review_decision",
        ),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    expense_request_id = db.Column(
        db.BigInteger, db.ForeignKey("expense_requests.id", ondelete="RESTRICT"), nullable=False
    )
    reviewer_user_id = db.Column(db.BigInteger, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    decision = db.Column(db.String(24), nullable=False)
    public_notes = db.Column(db.Text, nullable=True)
    internal_notes = db.Column(db.Text, nullable=True)
    decided_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.current_timestamp())

    expense_request = db.relationship("ExpenseRequest", back_populates="reviews")
    reviewer = db.relationship("User")


class Disbursement(TimestampMixin, db.Model):
    __tablename__ = "disbursements"
    __table_args__ = (
        UniqueConstraint("expense_request_id", name="uq_disbursement_expense"),
        CheckConstraint("amount_minor > 0", name="ck_disbursement_positive_amount"),
        CheckConstraint("status IN ('queued', 'processing', 'paid', 'failed', 'cancelled')", name="ck_disbursement_status"),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    expense_request_id = db.Column(
        db.BigInteger, db.ForeignKey("expense_requests.id", ondelete="RESTRICT"), nullable=False
    )
    recipient_id = db.Column(db.BigInteger, db.ForeignKey("payout_recipients.id", ondelete="RESTRICT"), nullable=False)
    amount_minor = db.Column(db.BigInteger, nullable=False)
    currency_code = db.Column(db.String(3), db.ForeignKey("currencies.code", ondelete="RESTRICT"), nullable=False)
    status = db.Column(db.String(16), nullable=False, server_default="queued")
    approved_at = db.Column(db.DateTime(timezone=True), nullable=False)
    paid_at = db.Column(db.DateTime(timezone=True), nullable=True)

    expense_request = db.relationship("ExpenseRequest", back_populates="disbursement")
    recipient = db.relationship("PayoutRecipient")
    currency = db.relationship("Currency")
    attempts = db.relationship("PayoutAttempt", back_populates="disbursement", cascade="all, delete-orphan")


class PayoutAttempt(TimestampMixin, db.Model):
    __tablename__ = "payout_attempts"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_payout_attempt_idempotency"),
        UniqueConstraint("provider_reference", name="uq_payout_provider_reference"),
        CheckConstraint("status IN ('initialized', 'pending', 'succeeded', 'failed', 'cancelled')", name="ck_payout_attempt_status"),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    disbursement_id = db.Column(db.BigInteger, db.ForeignKey("disbursements.id", ondelete="RESTRICT"), nullable=False)
    provider = db.Column(db.String(32), nullable=False, server_default="paystack")
    idempotency_key = db.Column(db.String(128), nullable=False)
    provider_reference = db.Column(db.String(128), nullable=True)
    status = db.Column(db.String(16), nullable=False, server_default="initialized")
    initiated_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.current_timestamp())
    completed_at = db.Column(db.DateTime(timezone=True), nullable=True)

    disbursement = db.relationship("Disbursement", back_populates="attempts")


class LedgerAccount(TimestampMixin, db.Model):
    __tablename__ = "ledger_accounts"
    __table_args__ = (
        UniqueConstraint("code", "currency_code", name="uq_ledger_account_code_currency"),
        CheckConstraint("account_type IN ('asset', 'liability', 'equity', 'income', 'expense')", name="ck_ledger_account_type"),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    code = db.Column(db.String(40), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    account_type = db.Column(db.String(16), nullable=False)
    currency_code = db.Column(db.String(3), db.ForeignKey("currencies.code", ondelete="RESTRICT"), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, server_default=db.true())

    currency = db.relationship("Currency")
    entries = db.relationship("LedgerEntry", back_populates="account")


class LedgerTransaction(TimestampMixin, db.Model):
    __tablename__ = "ledger_transactions"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_ledger_transaction_idempotency"),
        CheckConstraint(
            "(donation_id IS NOT NULL AND disbursement_id IS NULL) OR "
            "(donation_id IS NULL AND disbursement_id IS NOT NULL)",
            name="ck_ledger_transaction_one_source",
        ),
        CheckConstraint(
            "event_type IN ('donation_settled', 'donation_refunded', 'chargeback', 'payout_sent', 'payout_reversed', 'fee')",
            name="ck_ledger_transaction_event_type",
        ),
        Index("ix_ledger_transactions_posted", "posted_at"),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    donation_id = db.Column(db.BigInteger, db.ForeignKey("donations.id", ondelete="RESTRICT"), nullable=True)
    disbursement_id = db.Column(db.BigInteger, db.ForeignKey("disbursements.id", ondelete="RESTRICT"), nullable=True)
    idempotency_key = db.Column(db.String(128), nullable=False)
    event_type = db.Column(db.String(32), nullable=False)
    description = db.Column(db.String(255), nullable=False)
    posted_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.current_timestamp())

    donation = db.relationship("Donation")
    disbursement = db.relationship("Disbursement")
    entries = db.relationship("LedgerEntry", back_populates="transaction", cascade="all, delete-orphan")


class LedgerEntry(db.Model):
    __tablename__ = "ledger_entries"
    __table_args__ = (
        CheckConstraint(
            "(debit_minor > 0 AND credit_minor = 0) OR (credit_minor > 0 AND debit_minor = 0)",
            name="ck_ledger_entry_one_positive_side",
        ),
        Index("ix_ledger_entries_account_transaction", "account_id", "transaction_id"),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    transaction_id = db.Column(
        db.BigInteger, db.ForeignKey("ledger_transactions.id", ondelete="RESTRICT"), nullable=False
    )
    account_id = db.Column(db.BigInteger, db.ForeignKey("ledger_accounts.id", ondelete="RESTRICT"), nullable=False)
    debit_minor = db.Column(db.BigInteger, nullable=False, server_default="0")
    credit_minor = db.Column(db.BigInteger, nullable=False, server_default="0")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.current_timestamp())

    transaction = db.relationship("LedgerTransaction", back_populates="entries")
    account = db.relationship("LedgerAccount", back_populates="entries")


class AuditEvent(db.Model):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_entity", "entity_type", "entity_id", "created_at"),
        Index("ix_audit_events_actor", "actor_user_id", "created_at"),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    actor_user_id = db.Column(db.BigInteger, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action = db.Column(db.String(80), nullable=False)
    entity_type = db.Column(db.String(80), nullable=False)
    entity_id = db.Column(db.String(64), nullable=False)
    request_id = db.Column(db.String(64), nullable=True)
    metadata_json = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.current_timestamp())

    actor = db.relationship("User")


class OneTimeToken(db.Model):
    __tablename__ = "one_time_tokens"
    __table_args__ = (
        UniqueConstraint("token_digest", name="uq_one_time_token_digest"),
        CheckConstraint("purpose IN ('email_verification', 'password_reset')", name="ck_one_time_token_purpose"),
        Index("ix_one_time_tokens_user_purpose", "user_id", "purpose", "expires_at"),
    )

    id = db.Column(db.BigInteger, primary_key=True)
    user_id = db.Column(db.BigInteger, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    purpose = db.Column(db.String(24), nullable=False)
    token_digest = db.Column(db.String(128), nullable=False)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    consumed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.current_timestamp())

    user = db.relationship("User")