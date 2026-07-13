import frappe
from frappe.model.document import Document


class CompetitorSite(Document):
	def on_trash(self):
		frappe.db.delete("Scraped Product", {"source_site": self.name})
		frappe.db.commit()
