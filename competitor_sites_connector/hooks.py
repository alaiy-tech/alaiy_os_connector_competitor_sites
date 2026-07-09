app_name = "alaiy_os_connector_competitor_sites"
app_title = "Alaiy OS Connector Competitor Sites"
app_publisher = "Alaiy"
app_description = "Jewelry Feeder Platform"
app_email = "amit@alaiy.com"
app_license = "mit"

_V = "20260706a"
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
# 		"name": "stellar_brands",
# 		"logo": "/assets/alaiy_os_connector_competitor_sites/logo.png",
# 		"title": "Stellar Brands",
# 		"route": "/stellar_brands",
# 		"has_permission": "alaiy_os_connector_competitor_sites.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/alaiy_os_connector_competitor_sites/css/stellar_brands.css"
# app_include_js = "/assets/alaiy_os_connector_competitor_sites/js/stellar_brands.js"

# include js, css files in header of web template
# web_include_css = "/assets/alaiy_os_connector_competitor_sites/css/stellar_brands.css"
# web_include_js = "/assets/alaiy_os_connector_competitor_sites/js/stellar_brands.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "stellar_brands/public/scss/website"

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
# app_include_icons = "stellar_brands/public/icons.svg"

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
# 	"methods": "stellar_brands.utils.jinja_methods",
# 	"filters": "stellar_brands.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "stellar_brands.install.before_install"
# after_install = "stellar_brands.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "stellar_brands.uninstall.before_uninstall"
# after_uninstall = "stellar_brands.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "stellar_brands.utils.before_app_install"
# after_app_install = "stellar_brands.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "stellar_brands.utils.before_app_uninstall"
# after_app_uninstall = "stellar_brands.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "stellar_brands.notifications.get_notification_config"

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

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"stellar_brands.tasks.all"
# 	],
# 	"daily": [
# 		"stellar_brands.tasks.daily"
# 	],
# 	"hourly": [
# 		"stellar_brands.tasks.hourly"
# 	],
# 	"weekly": [
# 		"stellar_brands.tasks.weekly"
# 	],
# 	"monthly": [
# 		"stellar_brands.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "stellar_brands.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "stellar_brands.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "stellar_brands.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["stellar_brands.utils.before_request"]
# after_request = ["stellar_brands.utils.after_request"]

# Job Events
# ----------
# before_job = ["stellar_brands.utils.before_job"]
# after_job = ["stellar_brands.utils.after_job"]

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
# 	"stellar_brands.auth.validate"
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


after_migrate = ["alaiy_os_connector_competitor_sites.setup.install.sync_connector_registry",
                 "alaiy_os_connector_competitor_sites.setup.install.after_migrate"]
