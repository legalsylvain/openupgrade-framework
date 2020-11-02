# flake8: noqa
# pylint: skip-file

import logging
import sys
import time

import odoo
import odoo.tools as tools
from odoo import api, SUPERUSER_ID
from odoo.modules import loading
from odoo.modules.module import adapt_version, load_openerp_module

from odoo.modules.loading import load_data, load_demo
from odoo.addons.openupgrade_framework.openupgrade import openupgrade_loading

import os

_logger = logging.getLogger(__name__)
_test_logger = logging.getLogger('odoo.tests')


def load_module_graph(cr, graph, status=None, perform_checks=True,
                      skip_modules=None, report=None, models_to_check=None, upg_registry=None):
    """Migrates+Updates or Installs all module nodes from ``graph``
       :param graph: graph of module nodes to load
       :param status: deprecated parameter, unused, left to avoid changing signature in 8.0
       :param perform_checks: whether module descriptors should be checked for validity (prints warnings
                              for same cases)
       :param skip_modules: optional list of module names (packages) which have previously been loaded and can be skipped
       :return: list of modules that were installed or updated
    """
    if skip_modules is None:
        skip_modules = []

    if models_to_check is None:
        models_to_check = set()

    processed_modules = []
    loaded_modules = []
    registry = odoo.registry(cr.dbname)
    migrations = odoo.modules.migration.MigrationManager(cr, graph)
    module_count = len(graph)
    _logger.info('loading %d modules...', module_count)

    # <OpenUpgrade:ADD>
    # suppress commits to have the upgrade of one module in just one transaction
    cr.commit_org = cr.commit
    cr.commit = lambda *args: None
    cr.rollback_org = cr.rollback
    cr.rollback = lambda *args: None
    # </OpenUpgrade>

    # register, instantiate and initialize models for each modules
    t0 = time.time()
    loading_extra_query_count = odoo.sql_db.sql_counter
    loading_cursor_query_count = cr.sql_log_count

    models_updated = set()

    for index, package in enumerate(graph, 1):
        module_name = package.name
        module_id = package.id

        # <OpenUpgrade:CHANGE>
        if module_name in skip_modules or module_name in loaded_modules:
            # </OpenUpgrade>
            continue

        module_t0 = time.time()
        module_cursor_query_count = cr.sql_log_count
        module_extra_query_count = odoo.sql_db.sql_counter

        needs_update = (
            hasattr(package, "init")
            or hasattr(package, "update")
            or package.state in ("to install", "to upgrade")
        )
        module_log_level = logging.DEBUG
        if needs_update:
            module_log_level = logging.INFO
        _logger.log(module_log_level, 'Loading module %s (%d/%d)', module_name, index, module_count)

        if needs_update:
            if package.name != 'base':
                registry.setup_models(cr)
            migrations.migrate_module(package, 'pre')
            if package.name != 'base':
                env = api.Environment(cr, SUPERUSER_ID, {})
                env['base'].flush()

        load_openerp_module(package.name)

        new_install = package.state == 'to install'
        if new_install:
            py_module = sys.modules['odoo.addons.%s' % (module_name,)]
            pre_init = package.info.get('pre_init_hook')
            if pre_init:
                getattr(py_module, pre_init)(cr)

        model_names = registry.load(cr, package)

        mode = 'update'
        if hasattr(package, 'init') or package.state == 'to install':
            mode = 'init'

        loaded_modules.append(package.name)
        if needs_update:
            models_updated |= set(model_names)
            models_to_check -= set(model_names)
            registry.setup_models(cr)
            # <OpenUpgrade:ADD>
            # rebuild the local registry based on the loaded models
            local_registry = {}
            env = api.Environment(cr, SUPERUSER_ID, {})
            for model in env.values():
                if not model._auto:
                    continue
                openupgrade_loading.log_model(model, local_registry)
            openupgrade_loading.compare_registries(
                cr, package.name, upg_registry, local_registry)
            # </OpenUpgrade>

            registry.init_models(cr, model_names, {'module': package.name}, new_install)
        elif package.state != 'to remove':
            # The current module has simply been loaded. The models extended by this module
            # and for which we updated the schema, must have their schema checked again.
            # This is because the extension may have changed the model,
            # e.g. adding required=True to an existing field, but the schema has not been
            # updated by this module because it's not marked as 'to upgrade/to install'.
            models_to_check |= set(model_names) & models_updated

        idref = {}

        if needs_update:
            env = api.Environment(cr, SUPERUSER_ID, {})
            # Can't put this line out of the loop: ir.module.module will be
            # registered by init_models() above.
            module = env['ir.module.module'].browse(module_id)

            if perform_checks:
                module._check()

            if package.state == 'to upgrade':
                # upgrading the module information
                module.write(module.get_values_from_terp(package.data))
            load_data(cr, idref, mode, kind='data', package=package)
            demo_loaded = package.dbdemo = load_demo(cr, package, idref, mode)
            cr.execute('update ir_module_module set demo=%s where id=%s', (demo_loaded, module_id))
            module.invalidate_cache(['demo'])

            # <OpenUpgrade:CHANGE>
            # add 'try' block for logging exceptions
            # as errors in post scripts seem to be dropped
            try:
                migrations.migrate_module(package, 'post')
            except Exception as exc:
                _logger.error('Error executing post migration script for module %s: %s',
                              package, exc)
                raise
            # </OpenUpgrade>

            # Update translations for all installed languages
            overwrite = odoo.tools.config["overwrite_existing_translations"]
            module.with_context(overwrite=overwrite)._update_translations()

        if package.name is not None:
            registry._init_modules.add(package.name)

        if needs_update:
            if new_install:
                post_init = package.info.get('post_init_hook')
                if post_init:
                    getattr(py_module, post_init)(cr, registry)

            if mode == 'update':
                # validate the views that have not been checked yet
                env['ir.ui.view']._validate_module_views(module_name)

            # need to commit any modification the module's installation or
            # update made to the schema or data so the tests can run
            # (separately in their own transaction)
            # <OpenUpgrade:CHANGE>
            # commit after processing every module as well, for
            # easier debugging and continuing an interrupted migration
            cr.commit_org()
            # </OpenUpgrade

        updating = tools.config.options['init'] or tools.config.options['update']
        test_time = test_queries = 0
        test_results = None
        if tools.config.options['test_enable'] and (needs_update or not updating):
            env = api.Environment(cr, SUPERUSER_ID, {})
            loader = odoo.tests.loader
            suite = loader.make_suite(module_name, 'at_install')
            if suite.countTestCases():
                if not needs_update:
                    registry.setup_models(cr)
                # Python tests
                env['ir.http']._clear_routing_map()     # force routing map to be rebuilt

                tests_t0, tests_q0 = time.time(), odoo.sql_db.sql_counter
                test_results = loader.run_suite(suite, module_name)
                report.update(test_results)
                test_time = time.time() - tests_t0
                test_queries = odoo.sql_db.sql_counter - tests_q0

                # tests may have reset the environment
                env = api.Environment(cr, SUPERUSER_ID, {})
                module = env['ir.module.module'].browse(module_id)

        if needs_update:
            # <OpenUpgrade:CHANGE>
            # run tests
            if os.environ.get('OPENUPGRADE_TESTS') and package.name is not None:
                prefix = '.migrations'
                registry.openupgrade_test_prefixes[package.name] = prefix
                report.record_result(odoo.modules.module.run_unit_tests(module_name, openupgrade_prefix=prefix))
            # </OpenUpgrade

            processed_modules.append(package.name)

            ver = adapt_version(package.data['version'])
            # Set new modules and dependencies
            module.write({'state': 'installed', 'latest_version': ver})
            # <OpenUpgrade:ADD>
            # commit module_n state and version immediatly
            # to avoid invalid database state if module_n+1 raises an
            # exception
            cr.commit_org()
            # </OpenUpgrade>

            package.load_state = package.state
            package.load_version = package.installed_version
            package.state = 'installed'
            for kind in ('init', 'demo', 'update'):
                if hasattr(package, kind):
                    delattr(package, kind)
            module.flush()

        extra_queries = odoo.sql_db.sql_counter - module_extra_query_count - test_queries
        extras = []
        if test_queries:
            extras.append(f'+{test_queries} test')
        if extra_queries:
            extras.append(f'+{extra_queries} other')
        _logger.log(
            module_log_level, "Module %s loaded in %.2fs%s, %s queries%s",
            module_name, time.time() - module_t0,
            f' (incl. {test_time:.2f}s test)' if test_time else '',
            cr.sql_log_count - module_cursor_query_count,
            f' ({", ".join(extras)})' if extras else ''
        )
        if test_results and not test_results.wasSuccessful():
            _logger.error(
                "Module %s: %d failures, %d errors of %d tests",
                module_name, len(test_results.failures), len(test_results.errors),
                test_results.testsRun
            )

    _logger.runbot("%s modules loaded in %.2fs, %s queries (+%s extra)",
                   len(graph),
                   time.time() - t0,
                   cr.sql_log_count - loading_cursor_query_count,
                   odoo.sql_db.sql_counter - loading_extra_query_count)  # extra queries: testes, notify, any other closed cursor

    # <OpenUpgrade:ADD>
    # restore commit method
    cr.commit = cr.commit_org
    cr.commit()
    # </OpenUpgrade>

    return loaded_modules, processed_modules


loading.load_module_graph = load_module_graph
