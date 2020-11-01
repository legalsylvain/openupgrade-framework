# Copyright 2011-2015 Therp BV <https://therp.nl>
# Copyright 2016 Opener B.V. <https://opener.am>
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
{
    "name": "OpenUpgrade Records",
    "summary": """TODO WRITE ME.""",
    "version": "14.0.1.0.0",
    "category": "Migration",
    "author": "Odoo Community Association (OCA), Therp BV, Opener B.V., GRAP",
    "website": "https://github.com/OCA/openupgrade-framework",
    "data": [
        "views/menu.xml",
        "views/openupgrade_comparison_config.xml",
        "views/openupgrade_record.xml",
        "wizards/openupgrade_analysis_wizard.xml",
        "wizards/openupgrade_generate_records_wizard.xml",
        "wizards/openupgrade_install_all_wizard.xml",
        "security/ir.model.access.csv",
    ],
    "installable": True,
    "external_dependencies": {
        "python": ["odoorpc", "openupgradelib"],
    },
    "license": "AGPL-3",
}
