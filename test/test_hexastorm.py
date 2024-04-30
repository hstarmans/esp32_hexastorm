import unittest


def run_test(class_name, function, variable=None):
    print(class_name)

    exec(f"from hexastorm.tests.test_electrical import {class_name}")
    exec(f"tst = {class_name}()")
    exec("tst.setUpClass()")
    if variable:
        print(variable)
        exec(f"tst.{function}({variable})")
    else:
        exec(f"tst.{function}()")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("class_name", type=str)
    parser.add_argument("function", type=str)
    parser.add_argument("--variable", type=str)
    args = parser.parse_args()
    # normally you would use vars(args)
    # this is not supported
    run_test(args.class_name, args.function, args.variable)
