from odoo.addons import mrp
from odoo.addons.openupgrade_records.odoo_patch.odoo_patch import OdooPatch


class PreInitHookPatch(OdooPatch):
    target = mrp
    method_names = ['_pre_init_mrp']

    def _pre_init_mrp(cr):
        """ Don't try to create an existing column on reinstall """
        pass
