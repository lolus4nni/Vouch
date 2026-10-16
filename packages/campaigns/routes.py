from flask import Blueprint, render_template


campaign = Blueprint("campaign", __name__, template_folder="templates", static_folder="static")

CAMPAIGN_PAGES = {
    "create": "campaigns/create.html",
    "detail": "campaigns/detail.html",
    "edit": "campaigns/edit.html",
    "evidence": "campaigns/evidence.html",
    "explore": "campaigns/explore.html",
    "manage": "campaigns/manage.html",
    "my_campaigns": "campaigns/my_campaigns.html",
    "post_update": "campaigns/post_update.html",
    "report": "campaigns/report.html",
    "submitted": "campaigns/submitted.html",
}


@campaign.get("/")
def show_campaigns():
    return render_template("campaigns/explore.html")


for page_name, template_name in CAMPAIGN_PAGES.items():
    def render_page(template: str = template_name):
        return render_template(template)

    campaign.add_url_rule(
        f"/{page_name}.html",
        endpoint=page_name,
        view_func=render_page,
    )
