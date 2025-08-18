"""
unittest_runner
==================

A minimal unittest runner for MicroPython that can be embedded or imported.

Discovery:
    Pass a namespace (e.g., `globals()`) or a list of modules/namespaces to `run()`.
    If nothing is passed, the runner tries __main__ and finally its own globals.

Usage (single file):
    if __name__ == "__main__":
        run(globals())

Usage (aggregate multiple test files):
    import test_stepper, test_driver
    from mp_unittest_runner import run

    if __name__ == "__main__":
        run([test_stepper, test_driver])

Returns:
    A summary dict with totals, counts, and per-case details.
"""

import unittest
import logging
import sys

logger = logging.getLogger(__name__)


def _namespace_from(obj):
    """
    Normalize an input object into a dict-like namespace.

    Accepts:
        - dict: returned as-is (e.g., globals()).
        - module: converted via vars(module).

    Returns:
        dict or None if not convertible.
    """
    if isinstance(obj, dict):
        return obj
    # Treat anything module-like as a module
    if hasattr(obj, "__dict__") and hasattr(obj, "__name__"):
        try:
            return vars(obj)
        except Exception:
            return None
    return None


def _iter_test_cases_from_namespace(ns):
    """
    Collect unittest.TestCase subclasses defined in a given namespace dict.
    """
    cases = []
    for obj in ns.values():
        try:
            if (
                isinstance(obj, type)
                and issubclass(obj, unittest.TestCase)
                and obj is not unittest.TestCase
            ):
                cases.append(obj)
        except TypeError:
            pass
    return cases


def _collect_cases(ns_or_list):
    """
    Build a de-duplicated list of TestCase classes from one or more namespaces/modules.
    """
    namespaces = []

    if ns_or_list is None:
        # Best-effort default: use __main__ if available; else our own globals.
        main_mod = sys.modules.get("__main__")
        if main_mod is not None and main_mod is not sys.modules.get(__name__):
            ns = _namespace_from(main_mod)
            if ns:
                namespaces.append(ns)
        else:
            namespaces.append(globals())
    elif isinstance(ns_or_list, (list, tuple)):
        for obj in ns_or_list:
            ns = _namespace_from(obj)
            if ns:
                namespaces.append(ns)
    else:
        ns = _namespace_from(ns_or_list)
        if ns:
            namespaces.append(ns)

    seen = set()
    out = []
    for ns in namespaces:
        for cls in _iter_test_cases_from_namespace(ns):
            if cls not in seen:
                seen.add(cls)
                out.append(cls)
    return out


def run(ns_or_list=None):
    """
    Run all discovered unittest.TestCase subclasses from the given namespace(s).

    Args:
        ns_or_list: dict (e.g., globals()), a module, or a list/tuple of either.

    Lifecycle:
        - setUpClass/tearDownClass once per TestCase (if defined).
        - setUp/tearDown around each test method (if defined).

    Classification:
        - AssertionError -> failure
        - Any other Exception -> error

    Returns:
        dict: {
            "total": int,
            "passed": int,
            "failures": int,
            "errors": int,
            "by_case": {
                "CaseName": {
                    "passed": [test names],
                    "failures": [(test name, message)],
                    "errors": [(test name, repr(exception))],
                },
                ...
            },
        }
    """
    cases = _collect_cases(ns_or_list)

    total = 0
    passed = 0
    failures = 0
    errors = 0
    by_case = {}

    for cls in cases:
        case_name = cls.__name__
        by_case[case_name] = {"passed": [], "failures": [], "errors": []}
        logger.info("Starting test case: %s", case_name)

        if hasattr(cls, "setUpClass"):
            try:
                cls.setUpClass()
            except Exception as e:
                errors += 1
                by_case[case_name]["errors"].append(("setUpClass", repr(e)))
                logger.exception("Error in %s.setUpClass", case_name)
                # Try to tear down even if setup failed
                if hasattr(cls, "tearDownClass"):
                    try:
                        cls.tearDownClass()
                    except Exception as e2:
                        errors += 1
                        by_case[case_name]["errors"].append(
                            ("tearDownClass", repr(e2))
                        )
                        logger.exception(
                            "Error in %s.tearDownClass after failed setUpClass",
                            case_name,
                        )
                continue

        inst = cls()
        test_methods = sorted(
            name for name in dir(cls) if name.startswith("test")
        )

        for name in test_methods:
            total += 1
            try:
                if hasattr(inst, "setUp"):
                    inst.setUp()

                getattr(inst, name)()

                passed += 1
                by_case[case_name]["passed"].append(name)
                logger.info("ok - %s.%s", case_name, name)

            except AssertionError as ae:
                failures += 1
                by_case[case_name]["failures"].append((name, str(ae)))
                logger.error("FAIL - %s.%s -> %s", case_name, name, ae)

            except Exception as e:
                errors += 1
                by_case[case_name]["errors"].append((name, repr(e)))
                logger.exception(
                    "ERROR - %s.%s raised an unexpected exception",
                    case_name,
                    name,
                )

            finally:
                if hasattr(inst, "tearDown"):
                    try:
                        inst.tearDown()
                    except Exception as e:
                        errors += 1
                        by_case[case_name]["errors"].append(
                            ("tearDown", repr(e))
                        )
                        logger.exception(
                            "Error in %s.tearDown for %s", case_name, name
                        )

        if hasattr(cls, "tearDownClass"):
            try:
                cls.tearDownClass()
            except Exception as e:
                errors += 1
                by_case[case_name]["errors"].append(("tearDownClass", repr(e)))
                logger.exception("Error in %s.tearDownClass", case_name)

        logger.info("Finished test case: %s", case_name)

    summary = {
        "total": total,
        "passed": passed,
        "failures": failures,
        "errors": errors,
        "by_case": by_case,
    }
    logger.info(
        "Test summary: total=%d, passed=%d, failures=%d, errors=%d",
        total,
        passed,
        failures,
        errors,
    )
    return summary
