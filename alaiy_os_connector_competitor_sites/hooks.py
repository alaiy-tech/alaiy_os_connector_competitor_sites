app_name = "alaiy_os_connector_competitor_sites"
app_title = "Alaiy OS Connector Competitor Sites"
app_publisher = "Alaiy"
app_description = "Jewelry Feeder Platform"
app_email = "amit@alaiy.com"
app_license = "mit"

_V = "20260716c"
app_include_js = [
    f"/assets/alaiy_os_connector_competitor_sites/js/connector_card.js?v={_V}",
    f"/assets/alaiy_os_connector_competitor_sites/js/website_manager.js?v={_V}",
    f"/assets/alaiy_os_connector_competitor_sites/js/scrape_runner.js?v={_V}",
    f"/assets/alaiy_os_connector_competitor_sites/js/review_queue.js?v={_V}",
    f"/assets/alaiy_os_connector_competitor_sites/js/pricing.js?v={_V}",
]
app_include_css = [
    f"/assets/alaiy_os_connector_competitor_sites/css/connector_card.css?v={_V}",
    f"/assets/alaiy_os_connector_competitor_sites/css/website_manager.css?v={_V}",
    f"/assets/alaiy_os_connector_competitor_sites/css/scrape_runner.css?v={_V}",
    f"/assets/alaiy_os_connector_competitor_sites/css/review_queue.css?v={_V}",
    f"/assets/alaiy_os_connector_competitor_sites/css/pricing.css?v={_V}",
]

after_install = "alaiy_os_connector_competitor_sites.setup.install.after_install"

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "competitor_sites",
# 		"logo": "/assets/alaiy_os_connector_competitor_sites/logo.png",
# 		"title": "Competitor Sites",
# 		"route": "/competitor_sites",
# 		"has_permission": "alaiy_os_connector_competitor_sites.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/alaiy_os_connector_competitor_sites/css/competitor_sites.css"
# app_include_js = "/assets/alaiy_os_connector_competitor_sites/js/competitor_sites.js"

# include js, css files in header of web template
# web_include_css = "/assets/alaiy_os_connector_competitor_sites/css/competitor_sites.css"
# web_include_js = "/assets/alaiy_os_connector_competitor_sites/js/competitor_sites.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "competitor_sites/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "competitor_sites/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "competitor_sites.utils.jinja_methods",
# 	"filters": "competitor_sites.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "competitor_sites.install.before_install"
# after_install = "competitor_sites.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "competitor_sites.uninstall.before_uninstall"
# after_uninstall = "competitor_sites.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "competitor_sites.utils.before_app_install"
# after_app_install = "competitor_sites.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "competitor_sites.utils.before_app_uninstall"
# after_app_uninstall = "competitor_sites.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "competitor_sites.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

doc_events = {
	"Workspace Sidebar": {
		"on_update": "alaiy_os_connector_competitor_sites.setup.install._on_sidebar_update",
	}
}

# Scheduled Tasks
# ---------------

scheduler_events = {
	"cron": {
		"*/5 * * * *": [
			"alaiy_os_connector_competitor_sites.tasks.reap_stale_scrapes"
		]
	}
}

# Testing
# -------

# before_tests = "competitor_sites.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "competitor_sites.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "competitor_sites.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["competitor_sites.utils.before_request"]
# after_request = ["competitor_sites.utils.after_request"]

# Job Events
# ----------
# before_job = ["competitor_sites.utils.before_job"]
# after_job = ["competitor_sites.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"competitor_sites.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []


after_migrate = ["alaiy_os_connector_competitor_sites.setup.install.sync_connector_registry", "alaiy_os_connector_competitor_sites.setup.install.after_migrate"]
