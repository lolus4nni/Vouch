from flask import Blueprint, render_template


admin = Blueprint("admin", __name__, template_folder="templates", static_folder="static")

ADMIN_PAGES = {
    "accounts": "admin/accounts.html",
    "campaign_queue": "admin/campaign_queue.html",
    "campaign_review": "admin/campaign_review.html",
    "categories": "admin/categories.html",
    "dashboard": "admin/dashboard.html",
    "disbursals": "admin/disbursals.html",
    "donations": "admin/donations.html",
    "expense_queue": "admin/expense_queue.html",
    "expense_review": "admin/expense_review.html",
    "profile_queue": "admin/profile_queue.html",
    "profile_review": "admin/profile_review.html",
    "verification_logs": "admin/verification_logs.html",
}


@admin.get("/")
def admin_home():
    return render_template("admin/dashboard.html")


for page_name, template_name in ADMIN_PAGES.items():
    def render_page(template: str = template_name):
        return render_template(template)

    admin.add_url_rule(
        f"/{page_name}.html",
        endpoint=page_name,
        view_func=render_page,
    )
