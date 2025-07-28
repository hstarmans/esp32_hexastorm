import io
import sys

from hexastorm.tests import test_mpy


def run_test(class_name, function_name, *args, **kwargs):
    """executes test in hexastorm.tests.test_electrical

    Hexastorm uses yield syntax due to Amaranth HDL and that's why test are
    executed in this elaborated way.
    """
    print(f"Executing class {class_name} in test_mpy")
    try:
        # Get the class and function *directly*
        test_class = getattr(test_mpy, class_name)
        test_instance = test_class()
        test_instance.setUpClass()  # Call setUpClass

        test_function = getattr(
            test_instance, function_name
        )  # Get the function

        if args or kwargs:
            print(f"Got positional arguments: {args}")
            print(f"Got keyword arguments: {kwargs}")
            test_function(*args, **kwargs)  # Call the function directly
        else:
            test_function()  # Call without variable
    except KeyboardInterrupt:
        test_instance.tearDownClass()
    except Exception as e:
        # print exception in red
        s = io.StringIO()
        sys.print_exception(e, s)
        error_message = s.getvalue()
        print(f"\033[91m{error_message}\033[0m")
        # reset after exception
        test_instance.tearDownClass()


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
